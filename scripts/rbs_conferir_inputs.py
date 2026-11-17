"""Confere as Default Vol e as correlações de uma calibração do RBS contra o cálculo da página
Capital Market Assumptions do Hub, a partir dos proxies da tabela ``indicadores``.

Só leitura (Fibery e banco). Para cada classe do livro com Proxy: vol EWMA (λ = 0,995,
diária, √252; ``estimation.ewma_vols``), vol semanal simples da janela e a Default Vol da
calibração. Marca as classes cuja vol da calibração difere da EWMA em mais de ``--tolerancia``
e as correlações que diferem das de longo prazo (``estimation.cma_correlation``). Classes sem
Proxy são listadas. Código de saída 1 se houver divergência.

Uso::

    python scripts/rbs_conferir_inputs.py --livro Onshore --status Rascunho
    python scripts/rbs_conferir_inputs.py --livro Offshore --tolerancia 0.05
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

from persevera_tools.db.fibery import read_fibery
from persevera_tools.db.operations import read_sql
from persevera_tools.quant_research.risk_budgeting_spectrum.estimation import (
    CMA_LOOKBACK_YEARS, cma_correlation, ewma_vols)
from persevera_tools.quant_research.risk_budgeting_spectrum.loaders import load_from_fibery


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--livro", default="Onshore", choices=["Onshore", "Offshore"])
    ap.add_argument("--status", default=None)
    ap.add_argument("--anos", type=int, default=CMA_LOOKBACK_YEARS, help="janela, em anos")
    ap.add_argument("--tolerancia", type=float, default=0.10,
                    help="diferença relativa aceita na vol (default 0,10)")
    ap.add_argument("--tol-corr", type=float, default=0.10, help="diferença absoluta aceita na correlação")
    args = ap.parse_args()

    cfg = load_from_fibery(status=args.status, livro=args.livro)
    cl = read_fibery("Inv-Rsrch-Quant/Classes de Ativo", fields=["Name", "Livro", "Proxy"])
    cl = cl[cl["Livro"].fillna("Onshore") == args.livro].set_index("Name")["Proxy"]
    classes = {a.name: cl.get(a.name) for a in cfg.assets}
    sem_proxy = [n for n, p in classes.items() if not isinstance(p, str)]
    proxies = sorted({p for p in classes.values() if isinstance(p, str)})

    inicio = (date.today() - timedelta(days=args.anos * 365)).isoformat()
    codes = ",".join(f"'{p}'" for p in proxies)
    raw = read_sql(f"SELECT code, date, value FROM indicadores WHERE field = 'close' "
                   f"AND code IN ({codes}) AND date >= '{inicio}'")
    prices = raw.pivot(index="date", columns="code", values="value")
    prices.index = pd.to_datetime(prices.index)
    faltam = [p for p in proxies if p not in prices.columns]
    prices = prices.drop(columns=faltam)

    ew = ewma_vols(prices)
    semanal = prices.resample("W").last().pct_change(fill_method=None).std() * np.sqrt(52)
    corr = cma_correlation(prices)
    ultimo = prices.apply(lambda s: s.last_valid_index())

    rows = []
    for a in cfg.assets:
        p = classes[a.name]
        if p in ew.index:
            rel = a.default_vol / ew[p] - 1
            rows.append({"classe": a.name, "proxy": p, "vol_calibração_%": a.default_vol * 100,
                         "vol_EWMA_%": ew[p] * 100, "vol_semanal_%": semanal[p] * 100,
                         "dif_vs_EWMA": rel, "último_dado": ultimo[p].date(),
                         "ALERTA": abs(rel) > args.tolerancia})
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(f"{cfg.calibration_name} [{cfg.calibration_status}], janela desde {inicio}")
    print(df.round(3).to_string(index=False))
    if sem_proxy or faltam:
        print(f"\nSem Proxy no Fibery: {sem_proxy}; proxy sem dado no banco: {faltam}")

    ref = pd.DataFrame(cfg.full_corr, index=cfg.class_names, columns=cfg.class_names)
    achados = []
    for i, a in enumerate(cfg.class_names):
        for b in cfg.class_names[i + 1:]:
            pa, pb = classes[a], classes[b]
            if pa in corr.index and pb in corr.index:
                d = ref.loc[a, b] - corr.loc[pa, pb]
                if abs(d) > args.tol_corr:
                    achados.append((a, b, ref.loc[a, b], corr.loc[pa, pb], d))
    print(f"\nCorrelações que diferem mais de {args.tol_corr}: {len(achados)}")
    for a, b, r, c, d in sorted(achados, key=lambda x: -abs(x[4]))[:15]:
        print(f"  {a} × {b}: calibração {r:.2f}, longo prazo {c:.2f} (Δ {d:+.2f})")
    return 1 if (df["ALERTA"].any() or achados) else 0


if __name__ == "__main__":
    sys.exit(main())
