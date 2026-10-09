"""
Smoke test do WarrenOneProvider contra a API real (somente leitura).

Usa as credenciais PERSEVERA_WARRENONE_* do ~/.persevera/.env (ou do ambiente) e confere
que os endpoints de performance são consistentes entre si para uma carteira.

    uv run python scripts/warren_one_smoke.py --portfolio SAFI --date 2026-06-30 --env stg
"""

import argparse
import sys

import numpy as np

from persevera_tools.data.providers import WarrenOneProvider


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FALHA'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", required=True, help="Nome exato da carteira no One")
    parser.add_argument("--date", required=True, help="Data de referência YYYY-MM-DD")
    parser.add_argument("--env", default=None, help="prd | stg (default: PERSEVERA_WARRENONE_ENV)")
    args = parser.parse_args()

    one = WarrenOneProvider(env=args.env)
    portfolios = one.get_portfolios(search=args.portfolio)
    match = portfolios[portfolios["name"] == args.portfolio]
    if match.empty:
        print(f"Carteira '{args.portfolio}' não encontrada. Candidatas: {list(portfolios['name'])}")
        return 1
    p = match.iloc[0]
    pid = p["portfolio_id"]
    print(f"{p['name']} ({p['portfolio_type']}) id={pid} PL={p['total_net_value']:,.2f} "
          f"início={p['initial_date']:%Y-%m-%d} última posição={p['last_position_date']:%Y-%m-%d}\n")

    returns = one.get_returns(pid, args.date, benchmarks=["CDI", "IBOVESPA"], compare_to="CDI")
    port = returns[returns["series_type"] == "portfolio"].set_index("window")["value"]
    print(returns.pivot_table(index="series", columns="window", values="value", sort=False).round(5), "\n")

    monthly = one.get_monthly_returns(pid, args.date, benchmarks=["CDI"])
    acc = one.get_accumulated_returns(pid, args.date, benchmarks=["CDI"])
    acc3m = one.get_accumulated_returns(pid, args.date, period="3m")
    attribution = one.get_performance_attribution(pid, args.date)
    detail = one.get_performance_attribution_detail(pid, args.date, window="ytd")
    institutions = one.get_performance_attribution_detail(pid, args.date, window="ytd", by="institution")

    results = []
    year = int(args.date[:4])
    m = monthly[(monthly["series_type"] == "portfolio") & (monthly["year"] == year)]["value"].dropna()
    results.append(check("produto dos meses = retorno do ano",
                         np.isclose((1 + m).prod() - 1, port["ytd"], atol=1e-6),
                         f"{(1 + m).prod() - 1:.6f} vs {port['ytd']:.6f}"))

    acc_last = acc[acc["series_type"] == "portfolio"].sort_values("date")["value"].iloc[-1]
    results.append(check("último ponto do acumulado = retorno desde o início",
                         np.isclose(acc_last, port["inception"], atol=1e-6),
                         f"{acc_last:.6f} vs {port['inception']:.6f}"))

    acc3m_last = acc3m[acc3m["series_type"] == "portfolio"].sort_values("date")["value"].iloc[-1]
    if not np.isnan(port["3m"]):
        results.append(check("acumulado 3m = retorno 3m", np.isclose(acc3m_last, port["3m"], atol=1e-6),
                             f"{acc3m_last:.6f} vs {port['3m']:.6f}"))

    start = acc3m[acc3m["series_type"] == "portfolio"]["date"].min()
    rebased = one.get_accumulated_returns(pid, args.date, start=start)
    rebased_last = rebased[rebased["series_type"] == "portfolio"].sort_values("date")["value"].iloc[-1]
    results.append(check("rebase desde o início do 3m = acumulado 3m",
                         np.isclose(rebased_last, acc3m_last, atol=1e-6),
                         f"{rebased_last:.6f} vs {acc3m_last:.6f}"))

    ytd = attribution[attribution["window"] == "ytd"]
    contrib_sum = ytd.loc[~ytd["is_total"], "contribution"].sum()
    results.append(check("contribuições por classe somam o ano",
                         np.isclose(contrib_sum, port["ytd"], atol=1e-4), f"{contrib_sum:.6f} vs {port['ytd']:.6f}"))

    results.append(check("result_share por classe soma 1", np.isclose(detail["result_share"].sum(), 1.0, atol=1e-6)))
    results.append(check("resultado em R$ por classe = por instituição",
                         np.isclose(detail["result_value"].sum(), institutions["result_value"].sum(), atol=0.05),
                         f"{detail['result_value'].sum():,.2f} vs {institutions['result_value'].sum():,.2f}"))

    print(f"\n{sum(results)}/{len(results)} verificações OK")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
