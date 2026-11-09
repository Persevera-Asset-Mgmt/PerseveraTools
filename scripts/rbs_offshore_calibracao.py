"""Gera os CSVs de uma calibração offshore do RBS para importar no Fibery.

Lê o histórico dos proxies na tabela ``indicadores``; NÃO escreve no banco nem
no Fibery. Os arquivos vão para ``examples/rbs_offshore/`` (gitignored):

``classes.csv``       Classes de Ativo (Name, Livro, Classificação Layer 1, Proxy).
``parametros.csv``    Parâmetros de Classe: Default Vol estimada (sugestão),
                      Max Weight, Max RC e RC Intra-Bucket.
``correlacoes.csv``   Correlação Cross-Classe, todos os pares (Classe A no
                      bucket de menor índice: RF < Alt < RV).
``rc_targets.csv``    RC Target por Bucket (RC P1, RC P10, Curvatura RC).
``calibracao.txt``    Sigma Min/Max, N Profiles, Min Weight Threshold e a
                      janela usada na estimação.

Os valores de Max Weight, Max RC, RC Intra-Bucket e RC Target são as decisões
de desenho registradas em docs/risk_budgeting_spectrum/offshore.md; Default
Vol e Rho são estimados. Revise antes de importar.

Uso::

    python scripts/rbs_offshore_calibracao.py
    python scripts/rbs_offshore_calibracao.py --start 2019-06-01 --nome "Calibração Offshore - 2026-11-09"
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from persevera_tools.db.operations import read_sql
from persevera_tools.quant_research.risk_budgeting_spectrum.estimation import (
    ClassSpec, correlation_pairs, estimate_class_stats)

RF, ALT, RV = "Renda Fixa", "Alternativos", "Renda Variável"
LIVRO = "Offshore"

# (classe, bucket, proxy em `indicadores`, Max Weight, Max RC, RC Intra-Bucket)
CLASSES = [
    ("Off Pós-fixado",       RF,  "USFR",        0.40, None, None),
    ("Off Treasury Curto",   RF,  "SHY",         1.00, None, None),
    ("Off Treasury 7-10",    RF,  "IEF",         1.00, None, None),
    ("Off TIPS",             RF,  "STIP",        1.00, None, None),
    ("Off Crédito IG",       RF,  "SLQD",        1.00, None, None),
    ("Off Crédito HY",       RF,  "ANGL",        0.15, None, None),
    ("Off MBS",              RF,  "MBB",         0.20, None, None),
    ("Off Trend",            ALT, "DBMF",        0.20, None, None),
    ("Off Ouro",             ALT, "gold",        0.15, None, None),
    ("Off Bitcoin",          ALT, "bitcoin_usd", 0.05, 0.20, None),
    ("Off Ações EUA",        RV,  "SPYM",        0.60, None, 0.60),
    ("Off Ações DM ex-EUA",  RV,  "IEFA",        0.30, None, 0.22),
    ("Off Ações EM",         RV,  "FRDM",        0.20, None, 0.12),
    ("Off Imobiliário",      RV,  "REET",        0.10, None, 0.06),
]
RC_TARGETS = [  # bucket, RC P1, RC P10, Curvatura RC
    (RF, 1.00, 0.05, 0.5),
    (ALT, 0.00, 0.15, 1.2),
    (RV, 0.00, 0.80, 1.2),
]
SIGMA_MIN, SIGMA_MAX, N_PROFILES, MIN_WEIGHT = 0.02, 0.14, 10, 0.02


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default="2019-06-01", help="início da janela de estimação")
    ap.add_argument("--end", default=None, help="fim da janela (default: último dado)")
    ap.add_argument("--nome", default=None, help="nome da Calibração de Mercado")
    ap.add_argument("--out", default="examples/rbs_offshore", help="pasta de saída")
    args = ap.parse_args()

    specs = [ClassSpec(n, b, p) for n, b, p, *_ in CLASSES]
    codes = ",".join(f"'{s.proxy}'" for s in specs)
    raw = read_sql(
        f"SELECT date, code, value FROM indicadores WHERE field = 'close' AND code IN ({codes})"
    )
    prices = raw.pivot(index="date", columns="code", values="value")
    stats = estimate_class_stats(specs, prices, start=args.start, end=args.end)

    nome = args.nome or f"Calibração Offshore - {pd.Timestamp.today():%Y-%m-%d}"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(
        [{"Name": n, "Livro": LIVRO, "Classificação Layer 1": b, "Proxy": p}
         for n, b, p, *_ in CLASSES]
    ).to_csv(out / "classes.csv", index=False, encoding="utf-8-sig")

    pd.DataFrame(
        [{"Calibração": nome, "Classe": n,
          "Default Vol": round(float(stats.vols[n]), 4),
          "Max Weight": mw, "Max RC": mrc, "RC Intra-Bucket": intra}
         for n, _, _, mw, mrc, intra in CLASSES]
    ).to_csv(out / "parametros.csv", index=False, encoding="utf-8-sig")

    corr = correlation_pairs(specs, stats.corr)
    corr.insert(0, "Calibração", nome)
    corr.to_csv(out / "correlacoes.csv", index=False, encoding="utf-8-sig")

    pd.DataFrame(
        [{"Calibração": nome, "Classificação Layer 1": b, "RC P1": p1, "RC P10": p10,
          "Curvatura RC": g} for b, p1, p10, g in RC_TARGETS]
    ).to_csv(out / "rc_targets.csv", index=False, encoding="utf-8-sig")

    (out / "calibracao.txt").write_text(
        f"Name: {nome}\nLivro: {LIVRO}\n"
        f"Sigma Min: {SIGMA_MIN}\nSigma Max: {SIGMA_MAX}\n"
        f"N Profiles: {N_PROFILES}\nMin Weight Threshold: {MIN_WEIGHT}\n"
        f"Janela: {stats.start:%Y-%m-%d} a {stats.end:%Y-%m-%d} ({stats.n_weeks} semanas)\n",
        encoding="utf-8",
    )
    print(f"{len(CLASSES)} classes, {len(corr)} pares, janela "
          f"{stats.start:%Y-%m-%d} a {stats.end:%Y-%m-%d} → {out}/")


if __name__ == "__main__":
    main()
