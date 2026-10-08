"""Download Bloomberg company data into ``factor_zoo`` (raw inputs for derived factors).

Runs the ``Categoria Independente`` slugs from Fibery ``Definições dos Fatores``
that feed the derived ``factor_zoo`` pipeline. Daily upstream step before
``python -m persevera_tools.data.factor_zoo``. Requires a Bloomberg Terminal.

Exits with status 1 if any category fails, so a calling script can stop
before deriving factors from incomplete data.

Examples::

    python -m persevera_tools.data.factor_zoo.company_data
    python -m persevera_tools.data.factor_zoo.company_data --category market
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
import time
from typing import Sequence

START_DATE = "2020-01-01"
# factor_zoo is partitioned by year from 2000; rows before that cannot be stored.
HISTORY_FLOOR = "2000-01-01"
EXCHANGES = ["BZ", "US"]
STAGGER_SECONDS = 10

_COMMON = {
    "data_type": "company",
    "exchanges": EXCHANGES,
    "use_fund_currency": True,
    "save_to_db": True,
    "table_name": "factor_zoo",
}

# Fibery ``Categoria Independente`` values required by the derived pipeline.
CATEGORIES: list[dict] = [
    {"category": "market", "start_date": HISTORY_FLOOR},
    {"category": "market_split_adj", "start_date": HISTORY_FLOOR},
    {"category": "analyst_sentiment"},
    {"category": "balance_sheet"},
    {"category": "beta"},
    {"category": "cash_flow"},
    {"category": "consensus", "best_fperiod_override": "1BF"},
    {"category": "dividend"},
    {"category": "income_statement"},
    {"category": "leverage"},
    {"category": "margins"},
    {"category": "num_of_shares"},
    {"category": "options"},
    {"category": "ratios"},
    {"category": "short_interest"},
    {"category": "technicals"},
    {"category": "volatility"},
    {"category": "yield"},
]


def run_category(start_date: str = START_DATE, **kwargs) -> None:
    """Worker: one ``FinancialDataService`` per process, one category."""
    from persevera_tools.data import FinancialDataService

    FinancialDataService(start_date=start_date).get_bloomberg_data(**kwargs)


def main(argv: Sequence[str] | None = None) -> int:
    from persevera_tools.utils.logging import initialize as _initialize_logging

    _initialize_logging()

    parser = argparse.ArgumentParser(
        description="Download Bloomberg company data into factor_zoo."
    )
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        help="Only run this category (repeatable). Default: all categories.",
    )
    args = parser.parse_args(argv)

    jobs = [{**_COMMON, **c} for c in CATEGORIES]
    if args.categories:
        selected = set(args.categories)
        missing = selected - {j["category"] for j in jobs}
        if missing:
            parser.error(f"Unknown category(ies): {', '.join(sorted(missing))}")
        jobs = [j for j in jobs if j["category"] in selected]

    workers = min(len(jobs), multiprocessing.cpu_count())
    print(f"Created a pool with {workers} worker processes")

    failed: dict[str, str] = {}
    with multiprocessing.Pool(processes=workers) as pool:
        pending = []
        for i, kwargs in enumerate(jobs):
            if i > 0:
                time.sleep(STAGGER_SECONDS)
            print(f"Starting process for: {kwargs['category']}")
            pending.append((kwargs["category"], pool.apply_async(run_category, kwds=kwargs)))

        for category, result in pending:
            try:
                result.get()
                print(f"Category {category} finished")
            except Exception as exc:
                print(f"Error in category {category}: {exc}")
                failed[category] = str(exc)

    if failed:
        print(f"FAILED categories ({len(failed)}/{len(jobs)}): {', '.join(sorted(failed))}")
        return 1
    print("All categories completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
