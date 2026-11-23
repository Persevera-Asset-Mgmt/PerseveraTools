"""Backtest walk-forward trimestral do espectro RBS (só leitura: Fibery e banco).

A cada trimestre re-estima vols (50% EWMA + 50% janela, Bitcoin só EWMA) e correlações
(15 anos) só com dados até a data, roda o espectro, segura os pesos e mede o σ realizado
contra o σ-alvo. Dados mensais (fechamento do mês de ``indicadores``). Usa a política
(σ min/máx, RC targets, tetos) da calibração pedida em todas as datas.

Onshore: proxies em USD (Bitcoin, Ouro, S&P 500) entram convertidos em BRL por ``brl_usd``
(R$ por US$; no banco o ``usd_brl`` guarda o inverso). Classes sem Proxy (fundos de crédito)
rendem como um substituto: ``--substituto`` (default: IDA-DI e IDA-IPCA, dois cenários).

Uso::

    python scripts/rbs_backtest.py --livro Onshore --status Rascunho --inicio 2012-03-01
    python scripts/rbs_backtest.py --livro Offshore --inicio 2008-03-01 \\
        --max-weight "RF Fundo Crédito GI=0.15"
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

import pandas as pd

from persevera_tools.db.fibery import read_fibery
from persevera_tools.db.operations import read_sql
from persevera_tools.quant_research.risk_budgeting_spectrum import backtest as bt
from persevera_tools.quant_research.risk_budgeting_spectrum.loaders import load_from_fibery

USD_NO_ONSHORE = {"bitcoin_usd", "gold_100oz_futures", "us_sp500"}
CAIXA = {"Onshore": "br_cdi_index"}
SUBSTITUTOS = ("anbima_ida_di", "anbima_ida_ipca")


def _mensal(codes: list[str], inicio: str) -> pd.DataFrame:
    lista = ",".join(f"'{c}'" for c in codes)
    raw = read_sql(f"SELECT code, date, value FROM indicadores WHERE field = 'close' "
                   f"AND code IN ({lista}) AND date >= '{inicio}'")
    px = raw.pivot(index="date", columns="code", values="value")
    px.index = pd.to_datetime(px.index)
    px = px.sort_index().resample("ME").last()
    # o mês corrente está incompleto
    return px.loc[: pd.Timestamp.today().to_period("M").to_timestamp() - pd.Timedelta(days=1)]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--livro", default="Onshore", choices=["Onshore", "Offshore"])
    ap.add_argument("--status", default=None)
    ap.add_argument("--inicio", default="2012-03-01", help="primeiro rebalanceamento")
    ap.add_argument("--sigma-max", type=float, default=None, help="σ máximo (%) a usar")
    ap.add_argument("--max-weight", action="append", default=[], metavar="CLASSE=PESO",
                    help="troca o teto de uma classe (fração); repetível")
    ap.add_argument("--substituto", action="append", default=None,
                    help="código que rende as classes sem Proxy; repetível (default IDA-DI e IDA-IPCA)")
    args = ap.parse_args()

    base = load_from_fibery(status=args.status, livro=args.livro)
    if args.sigma_max is not None:
        base = replace(base, sigma_max_pct=args.sigma_max)
    for kv in args.max_weight:
        nome, v = kv.rsplit("=", 1)
        base = replace(base, assets=[replace(a, max_weight=float(v)) if a.name == nome else a
                                     for a in base.assets])
    cl = read_fibery("Inv-Rsrch-Quant/Classes de Ativo", fields=["Name", "Livro", "Proxy"])
    cl = cl[cl["Livro"].fillna("Onshore") == args.livro].set_index("Name")["Proxy"]
    nomes = [a.name for a in base.assets]
    proxies = {n: (cl.get(n) if isinstance(cl.get(n), str) else None) for n in nomes}
    sem_proxy = [n for n, p in proxies.items() if p is None]
    substitutos = args.substituto or (list(SUBSTITUTOS) if sem_proxy else [])
    if sem_proxy and args.livro == "Offshore" and not args.substituto:
        raise SystemExit(f"classes sem Proxy no Offshore: {sem_proxy}; passe --substituto")

    codes = sorted({p for p in proxies.values() if p} | set(substitutos)
                   | {"brl_usd"} | {CAIXA.get(args.livro)} - {None})
    px = _mensal(codes, "2003-01-01")
    faltam = [c for c in codes if c not in px.columns]
    if faltam:
        raise SystemExit(f"sem dado em indicadores: {faltam}")
    precos = px[sorted({p for p in proxies.values() if p})]
    ret = px.pct_change(fill_method=None)
    inicio = pd.Timestamp(args.inicio)
    caixa = ret[CAIXA[args.livro]] if args.livro in CAIXA else None

    base_ret = pd.DataFrame(index=ret.index)
    for n in nomes:
        p = proxies[n]
        if p is not None:
            r = ret[p]
            if args.livro == "Onshore" and p in USD_NO_ONSHORE:
                r = (1 + r) * (1 + ret["brl_usd"]) - 1
            base_ret[n] = r
    pd.set_option("display.width", 220)
    res0 = None
    for sub in (substitutos or [None]):
        rcc = base_ret.copy()
        for n in sem_proxy:
            rcc[n] = ret[sub]
        rcc = rcc[nomes].loc[rcc.index >= inicio].fillna(0.0)
        if res0 is None:
            res = res0 = bt.rodar(base, proxies, precos, rcc, 12, inicio, 3, so_ewma=("bitcoin_usd",))
        else:
            res = bt.realizar(res0.pesos, res0.alvo, rcc, res0.falhas, res0.excluidas)
        rotulo = f" (classes sem Proxy = {sub})" if sub else ""
        print(f"\n{base.calibration_name} [{base.calibration_status}] · {args.livro}{rotulo}: "
              f"{len(res.datas)} rebalanceamentos desde {res.datas[0].date()}, {len(res.retornos)} meses")
        print(bt.resumo(res, 12, caixa).round(2).to_string())
    print("\nPerfis sem convergência por data:")
    for t, msg in res0.falhas:
        print(f"  {t.date()}: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
