"""One-off history backfill of factor_zoo raw inputs (run on the Bloomberg PC).

Why
---
* Per-share / consensus history before 2020 was never refreshed after later
  splits and bonus issues (the daily run starts in 2020).
* Quarterly rows without ANNOUNCEMENT_DT were dated at the period end; the new
  rule dates them period end + 90 days, so old rows sit on wrong dates.
* ``price_close_split_adj`` is new and needs full history, including the
  delisted codes (with the per-share consensus fields their yields divide).

How
---
Jobs = (part, category, ticker batch). Each job downloads through the
production code path (pinned adjustment elements) and then, in ONE
transaction per job, replaces the stored history of every (code, field) it
returned: archive old rows to ``factor_zoo_removed`` (reason ``backfill``),
delete them, insert the new rows. A (code, field) Bloomberg returns nothing
for is left untouched.

Progress is checkpointed in ``examples/backfill_state.json``; rerunning skips
finished jobs, so the run can be spread over several days. A Bloomberg
capacity/limit error stops the run cleanly — rerun the next day.

Usage::

    python scripts/factor_zoo_backfill.py plan                 # volumes, no Bloomberg
    python scripts/factor_zoo_backfill.py check                # first quarterly batch, no writes
    python scripts/factor_zoo_backfill.py run --max-jobs 2   # trial
    python scripts/factor_zoo_backfill.py run                # everything, in priority order
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import sqlalchemy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from persevera_tools.data.lookups import get_securities_by_exchange  # noqa: E402
from persevera_tools.data.providers.bloomberg import BloombergProvider, _bdh  # noqa: E402
from persevera_tools.db.connection import get_db_engine  # noqa: E402
from persevera_tools.db.operations import read_sql  # noqa: E402

STATE = ROOT / "examples" / "backfill_state.json"
DELISTED_CHECK = ROOT / "examples" / "validation_out" / "delisted_check.csv"
HISTORY_START = "2000-01-01"
TODAY = pd.Timestamp.today().normalize()

# Active codes: categories whose stored history is stale or newly defined,
# in priority order (a capacity stop leaves the most important ones done).
# The daily run rewrites everything from 2020 on every day, so daily
# per-share categories only need 2000-2019; quarterly categories need the
# full span (the announcement-date rule changed for every year).
# market / technicals / volatility / beta / options / short_interest / yield
# are left out: market already downloads from 1980 every day, and the others
# are Bloomberg-computed series not affected by the adjustment changes.
PRE_DAILY_RUN_END = "2019-12-31"
ACTIVE_CATEGORIES: dict[str, dict] = {
    "balance_sheet": {"batch": 100},
    "income_statement": {"batch": 100},
    "cash_flow": {"batch": 100},
    "margins": {"batch": 100},
    "ratios": {"batch": 100},
    "leverage": {"batch": 100},
    "market_split_adj": {"start": "1980-01-01", "batch": 50},
    "num_of_shares": {"batch": 50, "end": PRE_DAILY_RUN_END},
    "dividend": {"batch": 50, "end": PRE_DAILY_RUN_END},
    "analyst_sentiment": {"batch": 50, "end": PRE_DAILY_RUN_END},
    "consensus": {"batch": 10, "end": PRE_DAILY_RUN_END, "best_fperiod_override": "1BF"},
}
# Delisted jobs run between market_split_adj and the remaining active ones.
DELISTED_AFTER = "market_split_adj"

# Delisted codes: only what per-share yields need, on a consistent basis.
DELISTED_JOBS: dict[str, dict] = {
    "market_split_adj": {},
    "consensus": {
        "best_fperiod_override": "1BF",
        "custom_fields": {"BEST_EPS": "earnings_per_share_fwd", "BEST_BPS": "book_value_per_share_fwd"},
    },
}
DELISTED_OK = {"ok", "level_shift", "mismatch"}
# Ero Copper: stored price is the Toronto line in CAD while fundamentals are
# USD; re-download its prices from Toronto converted to USD.
TICKER_OVERRIDES = {"ERO": {"ticker": "ERO CN Equity", "currency": "USD", "extra_price_close": True}}
BATCH_DELISTED = 50

CAPACITY_MARKERS = ("capacity", "limit", "quota", "daily")


@dataclass
class Job:
    part: str
    category: str
    exchange: str
    tickers: dict[str, str]
    start: str
    end: str
    kwargs: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        codes = sorted(self.tickers.values())
        return f"{self.part}|{self.category}|{self.exchange}|{self.start}|{codes[0]}..{codes[-1]}|{len(codes)}"


def _batches(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def active_jobs() -> list[Job]:
    jobs = []
    for exchange in ("BZ", "US"):
        secs = get_securities_by_exchange(exchange=exchange)
        items = sorted(secs.items())
        for cat, cfg in ACTIVE_CATEGORIES.items():
            kw = {k: v for k, v in cfg.items() if k not in ("start", "end", "batch")}
            for chunk in _batches(items, cfg["batch"]):
                jobs.append(Job("active", cat, exchange, dict(chunk), cfg.get("start", HISTORY_START),
                                cfg.get("end", TODAY.strftime("%Y-%m-%d")), kw))
    order = list(ACTIVE_CATEGORIES)
    return sorted(jobs, key=lambda j: order.index(j.category))


def ordered(active: list[Job], delisted: list[Job]) -> list[Job]:
    """Active jobs up to DELISTED_AFTER, then delisted, then the rest."""
    cut = list(ACTIVE_CATEGORIES).index(DELISTED_AFTER)
    head = [j for j in active if list(ACTIVE_CATEGORIES).index(j.category) <= cut]
    tail = [j for j in active if list(ACTIVE_CATEGORIES).index(j.category) > cut]
    return head + delisted + tail


def delisted_jobs() -> list[Job]:
    d = pd.read_csv(DELISTED_CHECK, parse_dates=["first_date", "last_date"])
    d = d[d["status"].isin(DELISTED_OK) | d["code"].isin(TICKER_OVERRIDES)]
    aliased = set(read_sql("SELECT old_code FROM factor_zoo_alias", raise_errors=True)["old_code"])
    d = d[~d["code"].isin(aliased)]
    jobs = []
    # Group by the year the code's history starts, so each request spans
    # roughly the life of its tickers.
    d["start"] = d["first_date"].dt.year.astype(str) + "-01-01"
    for (exchange, start), grp in d.groupby(["exchange", "start"]):
        end = (grp["last_date"].max() + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        regular = grp[~grp["code"].isin(TICKER_OVERRIDES)]
        for chunk in _batches(list(zip(regular["ticker"], regular["code"])), BATCH_DELISTED):
            for cat, kw in DELISTED_JOBS.items():
                jobs.append(Job("delisted", cat, exchange, dict(chunk), start, end, dict(kw)))
    for code, ov in TICKER_OVERRIDES.items():
        row = d[d["code"] == code]
        if row.empty:
            continue
        start = row["first_date"].iloc[0].strftime("%Y-01-01")
        end = (row["last_date"].iloc[0] + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        tick = {ov["ticker"]: code}
        cur = {"currency": ov["currency"]}
        jobs.append(Job("delisted", "market_split_adj", "US", tick, start, end, dict(cur)))
        if ov.get("extra_price_close"):
            jobs.append(Job("delisted", "market", "US", tick, start, end,
                            {**cur, "custom_fields": {"PX_LAST": "price_close"}}))
        jobs.append(Job("delisted", "consensus", "US", tick, start, end, dict(DELISTED_JOBS["consensus"])))
    return jobs


def _fields_of(bp: BloombergProvider, job: Job) -> list[str]:
    if "custom_fields" in job.kwargs:
        return list(job.kwargs["custom_fields"].values())
    return [f for f in bp.field_mappings[job.category].values() if f.upper() != "ANNOUNCEMENT_DT"]


def plan(jobs: list[Job], bp: BloombergProvider | None) -> pd.DataFrame:
    rows = []
    for j in jobs:
        n_fields = len(_fields_of(bp, j)) if bp else 1
        days = len(pd.bdate_range(j.start, j.end))
        quarterly = bp is not None and bp.frequencies.get(j.category) == "quarterly"
        points = len(j.tickers) * n_fields * (days // 63 if quarterly else days)
        rows.append({"part": j.part, "category": j.category, "tickers": len(j.tickers),
                     "fields": n_fields, "points_upper_bound": points})
    out = pd.DataFrame(rows).groupby(["part", "category"], sort=False).sum()
    out["cumulative"] = out["points_upper_bound"].cumsum()
    return out


_REPLACE = """
WITH scope AS (SELECT DISTINCT code, field FROM _bf)
, archived AS (
    INSERT INTO factor_zoo_removed (code, date, field, value, from_table, reason)
    SELECT z.code, z.date, z.field, z.value, 'factor_zoo', :reason
    FROM factor_zoo z JOIN scope s USING (code, field)
    WHERE z.date BETWEEN :start AND :end
      AND NOT EXISTS (SELECT 1 FROM _bf b WHERE b.code = z.code AND b.field = z.field
                      AND b.date = z.date AND b.value = z.value)
    RETURNING 1
)
DELETE FROM factor_zoo z USING scope s
WHERE z.code = s.code AND z.field = s.field AND z.date BETWEEN :start AND :end
"""


def replace_history(df: pd.DataFrame, job: Job) -> tuple[int, int]:
    """Archive + delete stored rows of the returned (code, field) pairs, insert new."""
    df = df.dropna(subset=["code", "date", "field", "value"])
    df = df[np.isfinite(pd.to_numeric(df["value"], errors="coerce"))]
    df = df.drop_duplicates(["code", "date", "field"], keep="last")[["code", "date", "field", "value"]]
    if df.empty:
        return 0, 0
    engine = get_db_engine()
    try:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(
                "CREATE TEMP TABLE _bf (code text, date date, field text, value double precision) ON COMMIT DROP"))
            df.to_sql("_bf", conn, if_exists="append", index=False, method="multi", chunksize=5000)
            deleted = conn.execute(sqlalchemy.text(_REPLACE), {
                "reason": f"backfill {job.category}", "start": job.start, "end": job.end}).rowcount
            inserted = conn.execute(sqlalchemy.text(
                "INSERT INTO factor_zoo (code, date, field, value) SELECT code, date, field, value FROM _bf")).rowcount
        return deleted, inserted
    finally:
        engine.dispose()


def run(jobs: list[Job], bp: BloombergProvider, max_jobs: int | None) -> int:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    todo = [j for j in jobs if j.key not in state]
    print(f"{len(jobs)} jobs, {len(jobs) - len(todo)} already done, {len(todo)} to run", flush=True)
    for i, j in enumerate(todo[:max_jobs] if max_jobs else todo, 1):
        t0 = time.time()
        bp.start_date = j.start
        kw = dict(j.kwargs)
        try:
            df = bp.get_data(category=j.category, data_type="company", exchanges=[j.exchange],
                             use_fund_currency=True, custom_tickers=j.tickers, **kw)
        except Exception as exc:
            msg = str(exc)
            if any(m in msg.lower() for m in CAPACITY_MARKERS):
                print(f"STOP: Bloomberg capacity/limit reached ({msg[:200]}). Rerun later.", flush=True)
                return 2
            print(f"FAILED {j.key}: {msg[:300]}", flush=True)
            continue
        df = df[pd.to_datetime(df["date"]) <= pd.Timestamp(j.end)]
        deleted, inserted = replace_history(df, j)
        state[j.key] = {"rows": int(len(df)), "deleted": deleted, "inserted": inserted,
                        "codes_returned": int(df["code"].nunique()) if len(df) else 0,
                        "at": pd.Timestamp.now().isoformat(timespec="seconds")}
        STATE.write_text(json.dumps(state, indent=1))
        print(f"[{i}/{len(todo)}] {j.key}: {len(df):,} rows, -{deleted:,} +{inserted:,} "
              f"({time.time() - t0:.0f}s)", flush=True)
    return 0


def check(jobs: list[Job], bp: BloombergProvider, sample: str = "BBAS3") -> int:
    """Download the first quarterly job without writing; show how dates come out."""
    import logging
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    bp.logger.addHandler(handler)
    bp.logger.setLevel(logging.INFO)
    job = next(j for j in jobs if bp.frequencies.get(j.category) == "quarterly")
    tickers, fields = list(job.tickers), list(bp.field_mappings[job.category])
    print(f"Checking {job.key} (nothing is written)", flush=True)
    raw = _bdh(tickers=tickers, flds=fields, start_date=job.start,
               EQY_FUND_CRNCY=bp.COUNTRY_CURRENCIES[job.exchange], FILING_STATUS="OR",
               **bp._ADJUST_SPLIT_ONLY)
    long = bp._bdh_to_long(raw, tickers=tickers, fields=fields)
    print(f"raw type={type(raw).__name__} shape={getattr(raw, 'shape', None)}; long rows={len(long):,}")
    ann = long[long["field"].astype(str).str.upper() == "ANNOUNCEMENT_DT"]
    v = ann["value"]
    num = pd.to_numeric(v, errors="coerce")
    in_range = num.between(19000101, 21001231)
    print(f"ANNOUNCEMENT_DT rows={len(v):,} non-null={int(v.notna().sum()):,} "
          f"YYYYMMDD-like={int(in_range.sum()):,} other={int((v.notna() & ~in_range).sum()):,}")
    print(f"value types: {v.dropna().map(lambda x: type(x).__name__).value_counts().to_dict()}")
    odd = ann[v.notna() & ~in_range]
    if len(odd):
        print("sample of non-YYYYMMDD values:")
        print(odd.groupby("code_bloomberg")["value"].agg(["size", "first"]).head(15).to_string())
    s = f"{sample} {job.exchange} Equity"
    # Raw rows of the sample ticker: where do its announcement dates land?
    cols = [c for c in raw.columns if isinstance(c, tuple) and c[0] == s]
    if cols:
        r = raw[cols].copy()
        r.columns = [c[1] for c in cols]
        keep = [c for c in r.columns if c.upper() in ("ANNOUNCEMENT_DT", fields[0].upper())]
        r = r[keep].dropna(how="all")
        # The raw index may hold date objects or strings depending on xbbg.
        r.index = pd.to_datetime(pd.Index(r.index).astype(str), errors="coerce")
        print(f"\nraw rows of {s} 2016-2019 (columns: {keep})")
        print(r[(r.index >= "2016-01-01") & (r.index <= "2019-12-31")].to_string())
    # Value rows without a same-date announcement: is there one nearby?
    is_ann = long["field"].astype(str).str.upper() == "ANNOUNCEMENT_DT"
    vals = long[~is_ann & long["value"].notna()][["code_bloomberg", "date"]].drop_duplicates()
    anns = long[is_ann & long["value"].notna()][["code_bloomberg", "date"]].drop_duplicates()
    m = vals.merge(anns.assign(same=True), on=["code_bloomberg", "date"], how="left")
    missing = m[m["same"].isna()][["code_bloomberg", "date"]]
    near = pd.merge_asof(missing.sort_values("date"), anns.sort_values("date").rename(columns={"date": "ann_row"}),
                         left_on="date", right_on="ann_row", by="code_bloomberg",
                         direction="nearest", tolerance=pd.Timedelta(days=10))
    print(f"\nvalue rows: {len(vals):,} | with same-date announcement: {int(m['same'].notna().sum()):,} "
          f"| without: {len(missing):,}, of which an announcement row within 10 days: "
          f"{int(near['ann_row'].notna().sum()):,}")
    print("days between value row and nearest announcement row:",
          (near["ann_row"] - near["date"]).dt.days.value_counts().head(8).to_dict())
    print("dates in the raw index by day of month:",
          pd.Series(pd.DatetimeIndex(raw.index).day).value_counts().head(8).to_dict())

    first_field = next(f for f in fields if f.upper() != "ANNOUNCEMENT_DT")
    # Carry the bdh period end through the re-dating to pair rows correctly.
    adj = bp._adjust_quarterly_dates(long.assign(period_end=long["date"]))
    sel = (adj["code_bloomberg"] == s) & (adj["field"].astype(str).str.upper() == first_field.upper())
    print()
    print(f"{s} {first_field}: period end (bdh) -> stored date")
    print(adj.loc[sel & adj["value"].notna(), ["period_end", "date", "value"]]
          .rename(columns={"date": "stored_date"}).sort_values("period_end")
          .query("period_end >= '2016-01-01' and period_end <= '2019-12-31'").to_string(index=False))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["plan", "check", "run"])
    p.add_argument("--part", choices=["active", "delisted", "all"], default="all")
    p.add_argument("--max-jobs", type=int, default=None)
    args = p.parse_args(argv)

    active = active_jobs() if args.part in ("active", "all") else []
    delisted = delisted_jobs() if args.part in ("delisted", "all") else []
    jobs = ordered(active, delisted)

    bp = BloombergProvider(start_date=HISTORY_START)  # loads Fibery mappings; no Bloomberg call
    if args.command == "check":
        return check(jobs, bp)
    if args.command == "plan":
        tbl = plan(jobs, bp)
        print(tbl.to_string())
        print(f"\nTotal jobs: {len(jobs)} | upper bound on data points: {tbl['points_upper_bound'].sum():,}")
        return 0
    return run(jobs, bp, args.max_jobs)


if __name__ == "__main__":
    sys.exit(main())
