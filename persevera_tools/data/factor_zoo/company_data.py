"""Incremental Bloomberg download of company data into ``factor_zoo``.

Daily upstream step before the derived pipeline. Requires a Bloomberg
Terminal. Instead of re-downloading full histories every day:

* **Daily categories** — last ``DAILY_WINDOW_DAYS`` calendar days, written as
  changed-only upserts (unchanged rows are not rewritten).
* **Quarterly categories** — request ``QUARTERLY_REQUEST_DAYS`` of periods and
  replace the stored rows dated in the last ``QUARTERLY_REPLACE_DAYS`` (a
  release re-dated from the +90d fallback to its announcement date must not
  leave the old row behind). The request reaches further back than the
  replace window, so every replaced row's period is in the request.
* **Restated history** — Bloomberg re-adjusts the whole price history on an
  ex-date. On the overlapping days of the window, a changed ``price_close``
  means a dividend (the code's ``market`` history is re-downloaded from 2000);
  a changed ``price_close_split_adj`` means a split or bonus issue (the code's
  split-sensitive categories are re-downloaded too).
* **New codes** (in ``Ações Ativas`` without any ``price_close``) get their
  full history in every category.

Codes whose history was replaced are written to ``examples/factor_zoo_refetched.json``
so the derived step can recompute their full history.

Exits with status 1 if any category fails.

Examples::

    python -m persevera_tools.data.factor_zoo.company_data
    python -m persevera_tools.data.factor_zoo.company_data --category market
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

EXCHANGES = ["BZ", "US"]
HISTORY_FLOOR = "2000-01-01"
DAILY_WINDOW_DAYS = 30  # covers ~3 weeks of missed runs (holidays, PC off)
QUARTERLY_REQUEST_DAYS = 400
QUARTERLY_REPLACE_DAYS = 200
RESTATEMENT_TOLERANCE = 1e-6
FETCH_ATTEMPTS = 3

REFETCHED_FILE = Path(__file__).resolve().parents[3] / "examples" / "factor_zoo_refetched.json"

# Fibery ``Categoria Independente`` values required by the derived pipeline.
CATEGORIES: dict[str, dict] = {
    "market": {},
    "market_split_adj": {},
    "analyst_sentiment": {},
    "balance_sheet": {},
    "beta": {},
    "cash_flow": {},
    "consensus": {"best_fperiod_override": "1BF"},
    "dividend": {},
    "income_statement": {},
    "leverage": {},
    "margins": {},
    "num_of_shares": {},
    "options": {},
    "ratios": {},
    "short_interest": {},
    "technicals": {},
    "volatility": {},
    "yield": {},
}
# Categories whose history Bloomberg restates on a split / bonus issue
# (prices, volumes, per-share fields, share counts).
SPLIT_SENSITIVE = ["market", "market_split_adj", "consensus", "dividend",
                   "num_of_shares", "analyst_sentiment", "balance_sheet", "income_statement"]


def _today() -> pd.Timestamp:
    return pd.Timestamp.today().normalize()


class _Downloader:
    """BloombergProvider wrapper: active universe, code subsets, retries."""

    def __init__(self) -> None:
        from persevera_tools.data.lookups import get_securities_by_exchange
        from persevera_tools.data.providers.bloomberg import BloombergProvider

        self.bp = BloombergProvider(start_date=HISTORY_FLOOR)
        self.universe = {ex: get_securities_by_exchange(exchange=ex) for ex in EXCHANGES}

    @property
    def codes(self) -> set[str]:
        return {c for secs in self.universe.values() for c in secs.values()}

    def is_quarterly(self, category: str) -> bool:
        return self.bp.frequencies.get(category) == "quarterly"

    def fetch(self, category: str, start: str | pd.Timestamp,
              codes: Iterable[str] | None = None) -> pd.DataFrame:
        wanted = None if codes is None else set(codes)
        frames = []
        for ex, secs in self.universe.items():
            tickers = {t: c for t, c in secs.items() if wanted is None or c in wanted}
            if not tickers:
                continue
            frames.append(self._fetch_one(category, start, ex, tickers))
        frames = [f for f in frames if len(f)]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["code", "date", "field", "value"])

    def _fetch_one(self, category, start, exchange, tickers) -> pd.DataFrame:
        self.bp.start_date = pd.Timestamp(start).strftime("%Y-%m-%d")
        last = None
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            try:
                return self.bp.get_data(category=category, data_type="company", exchanges=[exchange],
                                        use_fund_currency=True, custom_tickers=tickers,
                                        **CATEGORIES[category])
            except Exception as exc:  # retried, then re-raised
                last = exc
                print(f"  {category}/{exchange}: attempt {attempt} failed: {exc}", flush=True)
                time.sleep(10 * attempt)
        raise last


def restated_codes(new: pd.DataFrame, field: str, before: pd.Timestamp) -> set[str]:
    """Codes whose freshly downloaded ``field`` differs from the stored one on a past date."""
    from persevera_tools.db.operations import read_sql

    new = new[(new["field"] == field) & (pd.to_datetime(new["date"]) < before)]
    if new.empty:
        return set()
    start = pd.to_datetime(new["date"]).min()
    old = read_sql(
        "SELECT code, date, value FROM factor_zoo WHERE field = :f AND date >= :s AND date < :b AND code = ANY(:c)",
        params={"f": field, "s": start.date(), "b": before.date(), "c": sorted(new["code"].unique())},
        date_columns=["date"], raise_errors=True,
    )
    m = new.assign(date=pd.to_datetime(new["date"])).merge(old, on=["code", "date"], suffixes=("_new", "_old"))
    m = m[m["value_old"].abs() > 0]
    changed = (m["value_new"] / m["value_old"] - 1).abs() > RESTATEMENT_TOLERANCE
    return set(m.loc[changed, "code"])


def codes_without_history(codes: Iterable[str]) -> set[str]:
    from persevera_tools.db.operations import read_sql

    codes = sorted(codes)
    have = read_sql(
        "SELECT DISTINCT code FROM factor_zoo WHERE field = 'price_close' AND code = ANY(:c)",
        params={"c": codes}, raise_errors=True,
    )
    return set(codes) - set(have["code"])


def run(categories: Sequence[str]) -> int:
    from persevera_tools.data.factor_zoo.storage import replace_history, upsert_changed

    today = _today()
    dl = _Downloader()
    failed: dict[str, str] = {}
    restated: dict[str, set[str]] = {"dividend": set(), "split": set()}
    new_codes = codes_without_history(dl.codes)
    if new_codes:
        print(f"New codes without history (full download): {sorted(new_codes)}", flush=True)

    for cat in categories:
        t0 = time.time()
        try:
            if dl.is_quarterly(cat):
                df = dl.fetch(cat, today - pd.Timedelta(days=QUARTERLY_REQUEST_DAYS))
                deleted, inserted = replace_history(df, today - pd.Timedelta(days=QUARTERLY_REPLACE_DAYS), today)
                msg = f"-{deleted:,} +{inserted:,} (last {QUARTERLY_REPLACE_DAYS}d replaced)"
            else:
                df = dl.fetch(cat, today - pd.Timedelta(days=DAILY_WINDOW_DAYS))
                if cat == "market":
                    restated["dividend"] |= restated_codes(df, "price_close", today) - new_codes
                if cat == "market_split_adj":
                    restated["split"] |= restated_codes(df, "price_close_split_adj", today) - new_codes
                n = upsert_changed(df)
                msg = f"{n:,} rows upserted (changed only)"
            print(f"{cat}: {len(df):,} rows downloaded, {msg} ({time.time() - t0:.0f}s)", flush=True)
        except Exception as exc:
            print(f"FAILED {cat}: {exc}", flush=True)
            failed[cat] = str(exc)

    # Full-history replacements: restated prices and brand-new codes.
    full: dict[str, set[str]] = {}
    for code in restated["dividend"] | restated["split"]:
        full.setdefault("market", set()).add(code)
    for code in restated["split"]:
        for cat in SPLIT_SENSITIVE:
            full.setdefault(cat, set()).add(code)
    for code in new_codes:
        for cat in categories:
            full.setdefault(cat, set()).add(code)
    print(f"Restated history: dividends {sorted(restated['dividend'])}, splits {sorted(restated['split'])}", flush=True)
    for cat, codes in full.items():
        if cat not in categories or cat in failed:
            continue
        try:
            df = dl.fetch(cat, HISTORY_FLOOR, codes)
            deleted, inserted = replace_history(df, HISTORY_FLOOR, today)
            print(f"full history {cat} for {len(codes)} codes: -{deleted:,} +{inserted:,}", flush=True)
        except Exception as exc:
            print(f"FAILED full history {cat}: {exc}", flush=True)
            failed[f"full:{cat}"] = str(exc)

    refetched = sorted(set().union(*full.values())) if full else []
    REFETCHED_FILE.parent.mkdir(parents=True, exist_ok=True)
    REFETCHED_FILE.write_text(json.dumps({"date": today.strftime("%Y-%m-%d"), "codes": refetched}, indent=1))

    if failed:
        print(f"FAILED ({len(failed)}): {', '.join(sorted(failed))}", flush=True)
        return 1
    print("All categories completed.", flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    from persevera_tools.utils.logging import initialize as _initialize_logging

    _initialize_logging()
    parser = argparse.ArgumentParser(description="Incremental Bloomberg download into factor_zoo.")
    parser.add_argument("--category", action="append", dest="categories",
                        help="Only run this category (repeatable). Default: all categories.")
    args = parser.parse_args(argv)
    cats = list(CATEGORIES)
    if args.categories:
        missing = set(args.categories) - set(cats)
        if missing:
            parser.error(f"Unknown category(ies): {', '.join(sorted(missing))}")
        cats = [c for c in cats if c in args.categories]
    return run(cats)


if __name__ == "__main__":
    sys.exit(main())
