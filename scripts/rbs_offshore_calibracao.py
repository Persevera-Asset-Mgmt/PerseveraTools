"""Reestima vols e correlações de uma calibração do RBS a partir das proxies do Fibery.

Lê do Fibery as classes do livro (nome, bucket e Proxy) e a calibração mais
recente desse livro (Max Weight, Max RC, RC Intra-Bucket, RC targets, σ). Lê o
histórico das proxies na tabela ``indicadores``. NÃO escreve no banco nem no
Fibery: os arquivos vão para ``examples/rbs_<livro>/`` (gitignored):

``parametros.csv``    Parâmetros de Classe com a Default Vol reestimada (os
                      demais campos repetem a calibração de referência).
``correlacoes.csv``   Correlação Cross-Classe, todos os pares (Classe A no
                      bucket de menor índice: RF < Alt < RV).
``cobertura.csv``     Início, fim e semanas usadas de cada classe.
``comparacao.csv``    Vol e correlações novas contra a calibração de referência.
``espectro.csv``      Pesos por perfil com os parâmetros novos (com --espectro).

Método padrão: vol de cada classe no próprio histórico e correlação par a par
no período em comum, com correção para a correlação PSD mais próxima
(``estimation.estimate_class_stats(method="pairwise")``).

Uso::

    python scripts/rbs_offshore_calibracao.py
    python scripts/rbs_offshore_calibracao.py --inicio 2005-01-01 --inicio-classe "Bitcoin=2014-01-01" --espectro
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import pandas as pd

from persevera_tools.db.fibery import read_fibery
from persevera_tools.db.operations import read_sql
from persevera_tools.quant_research.risk_budgeting_spectrum import build_spectrum
from persevera_tools.quant_research.risk_budgeting_spectrum.config import AssetClass
from persevera_tools.quant_research.risk_budgeting_spectrum.estimation import (
    ClassSpec, correlation_pairs, estimate_class_stats)
from persevera_tools.quant_research.risk_budgeting_spectrum.loaders import load_from_fibery


def read_classes(livro: str, reference_order: list[str]) -> list[ClassSpec]:
    """Classes do livro com bucket e proxy, na ordem da calibração de referência."""
    df = read_fibery("Inv-Rsrch-Quant/Classes de Ativo",
                     fields=["Name", "Livro", "Classificação Layer 1", "Proxy"])
    df = df[df["Livro"].fillna("Onshore") == livro].set_index("Name")
    sem_proxy = df.index[df["Proxy"].isna()].tolist()
    if sem_proxy:
        raise SystemExit(f"Classes sem Proxy no Fibery: {sem_proxy}")
    return [ClassSpec(n, df.loc[n, "Classificação Layer 1"], df.loc[n, "Proxy"])
            for n in reference_order]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--livro", default="Offshore")
    ap.add_argument("--status", default=None, help="status da calibração de referência (default: qualquer)")
    ap.add_argument("--inicio", default="2005-01-01", help="início da janela de estimação")
    ap.add_argument("--fim", default=None, help="fim da janela (default: último dado)")
    ap.add_argument("--inicio-classe", action="append", default=[],
                    help="início próprio de uma classe, 'Nome=YYYY-MM-DD' (repetível)")
    ap.add_argument("--metodo", default="pairwise", choices=["pairwise", "common"])
    ap.add_argument("--espectro", action="store_true", help="roda o motor com os parâmetros novos")
    ap.add_argument("--out", default=None, help="pasta de saída (default: examples/rbs_<livro>)")
    args = ap.parse_args()

    ref = load_from_fibery(status=args.status, livro=args.livro)
    specs = read_classes(args.livro, ref.class_names)
    class_start = dict(s.split("=", 1) for s in args.inicio_classe)

    codes = ",".join(f"'{p}'" for p in sorted({s.proxy for s in specs}))
    raw = read_sql(f"SELECT code, date, value FROM indicadores WHERE field = 'close' AND code IN ({codes})")
    prices = raw.pivot(index="date", columns="code", values="value")
    stats = estimate_class_stats(specs, prices, start=args.inicio, end=args.fim,
                                 method=args.metodo, class_start=class_start)

    out = Path(args.out or f"examples/rbs_{args.livro.lower()}")
    out.mkdir(parents=True, exist_ok=True)
    by_name = {a.name: a for a in ref.assets}
    intra = ref.intra_rc_weights or {}
    intra_by_name = {}
    for bk, idxs in ref.bucket_indices.items():
        if bk in intra:
            for j, i in enumerate(idxs):
                intra_by_name[ref.assets[i].name] = float(intra[bk][j] / intra[bk].sum())

    pd.DataFrame([{
        "Calibração": ref.calibration_name, "Classe": s.name,
        "Default Vol": round(float(stats.vols[s.name]), 4),
        "Max Weight": by_name[s.name].max_weight, "Max RC": by_name[s.name].max_rc,
        "RC Intra-Bucket": intra_by_name.get(s.name),
    } for s in specs]).to_csv(out / "parametros.csv", index=False, encoding="utf-8-sig")

    pairs = correlation_pairs(specs, stats.corr)
    pairs.to_csv(out / "correlacoes.csv", index=False, encoding="utf-8-sig")
    cov = stats.coverage.assign(proxy=[s.proxy for s in specs])
    cov.to_csv(out / "cobertura.csv", encoding="utf-8-sig")

    ref_corr = pd.DataFrame(ref.full_corr, index=ref.class_names, columns=ref.class_names)
    comp = pd.DataFrame({
        "vol_ref": [by_name[s.name].default_vol for s in specs],
        "vol_nova": [float(stats.vols[s.name]) for s in specs],
    }, index=[s.name for s in specs])
    comp.round(4).to_csv(out / "comparacao.csv", encoding="utf-8-sig")
    pairs["Rho ref"] = [round(float(ref_corr.loc[a, b]), 4) for a, b in zip(pairs["Classe A"], pairs["Classe B"])]
    pairs.to_csv(out / "correlacoes_vs_ref.csv", index=False, encoding="utf-8-sig")

    print(f"Referência: {ref.calibration_name} ({ref.calibration_status}); método {args.metodo}; "
          f"maior ajuste PSD {stats.psd_adjustment:.4f}")
    print(cov.to_string())
    print((comp * 100).round(1).to_string())

    if args.espectro:
        ref.assets = [AssetClass(a.name, a.bucket, a.proxy, float(stats.vols[a.name]), a.max_weight, a.max_rc)
                      for a in ref.assets]
        ref.full_corr = stats.corr.loc[ref.class_names, ref.class_names].values
        with warnings.catch_warnings(record=True) as wl:
            warnings.simplefilter("always")
            res = build_spectrum(ref)
        for w in wl:
            print("WARN:", w.message)
        w = pd.DataFrame({f"P{p}": res["weights"][p] for p in res["weights"]}, index=res["classes"])
        w.loc["vol"] = [res["vol_realized"][p] / 100 for p in res["weights"]]
        w.loc["convergiu"] = [float(res["converged_per_profile"][p]) for p in res["weights"]]
        w.round(4).to_csv(out / "espectro.csv", encoding="utf-8-sig")
        print((w * 100).round(1).to_string())
    print(f"→ {out}/")


if __name__ == "__main__":
    main()
