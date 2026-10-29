"""Bloomberg load of B3 index composition into ``b3_index_composition``.

Daily step of the factor_zoo run. Requires a Bloomberg Terminal.

``INDX_MWEIGHT_HIST`` with ``END_DATE_OVERRIDE`` returns every member of an
index on a date (delisted names included) and its weight in percent. The table
keeps one row per member per B3 session, so readers can take any rebalance
date. Sessions are ANBIMA business days minus days without an Ibovespa close
(B3-only closures such as 24/12, 31/12 and the São Paulo holidays):

* **Window** — per index, from the last stored session (re-fetched) to the
  last session before today. An index with no rows starts at ``HISTORY_FLOOR``.
* **Snapshots** — fetched on every month-end session, the window's first and
  last session, and, where two consecutive snapshots have different members,
  by bisection down to the session the membership changed. Membership is
  therefore exact on every session; weights are exact on fetched sessions and
  carried forward in between (a daily run fetches every session).
* **Codes** — Bloomberg reports past members under their ticker at fetch time
  (CCRO3 in 2013 comes back as ``MOTV3 BZ`` → ``MOTV3``), so later ticker
  changes leave stored rows behind. As in factor_zoo, one security keeps one
  code: rows are translated through ``factor_zoo_alias`` (respecting
  ``effective_date``) before writing, and ``factor_zoo.aliases --apply``
  renames stored rows when a new alias appears. Members delisted without a
  ticker come back as Bloomberg ids (``0927042D``) and match nothing.
* **Coverage** — index members on the last session whose ``price_close`` in
  factor_zoo is older than ``MAX_STALE_SESSIONS`` sessions are printed as
  alerts (dropped from ``Ações Ativas``, ticker change without an alias, no
  trading), since backtests silently skip them.
* **Writes** — each index's window is replaced in one transaction.

Exits with status 1 if an index fails, a snapshot comes back empty after the
index's inception, or an index is still more than ``MAX_STALE_SESSIONS``
sessions behind after the run. Weights not summing to ~100% and large
membership swings are printed as alerts.

Examples::

    python -m persevera_tools.data.index_composition_update
    python -m persevera_tools.data.index_composition_update --index IBOV SMLL --dry-run
    python -m persevera_tools.data.index_composition_update --index IBRA --start 2012-01-01
"""

from __future__ import annotations

import argparse
import sys
import time
from functools import lru_cache
from typing import Callable, Optional, Sequence

import pandas as pd

TABLE = "b3_index_composition"
# Table field -> Bloomberg index ticker. Field names match the rows loaded
# before the 2026 migration (IBX100, SMLL, MLCX), so existing readers keep working.
INDICES: dict[str, str] = {
    "IBOV": "IBOV Index",
    "IBX100": "IBX Index",
    "IBX50": "IBX50 Index",
    "IBRA": "IBRA Index",
    "SMLL": "SMLLBV Index",
    "MLCX": "MLCXBV Index",
}
HISTORY_FLOOR = pd.Timestamp("2000-01-03")
MAX_STALE_SESSIONS = 5
WEIGHT_SUM_TOLERANCE = 0.5  # percentage points around 100
MEMBER_CHANGE_ALERT = 0.10  # share of members entering/leaving between snapshots
# Old and new codes listed together for at most this many sessions are one
# security on its ticker-change day; longer, they are two share classes
# (AMBV3/AMBV4 next to ABEV3 until 2013) and their weights add up.
TICKER_CHANGE_OVERLAP = 5
FETCH_ATTEMPTS = 3

Snapshot = pd.Series  # code -> weight (%), indexed by code
Fetcher = Callable[[pd.Timestamp], Snapshot]


@lru_cache(maxsize=1)
def _anbima_holidays() -> tuple:
    from persevera_tools.utils.dates import get_holidays

    return tuple(get_holidays())


@lru_cache(maxsize=1)
def _ibov_closes() -> pd.DatetimeIndex:
    from persevera_tools.data import get_series

    ibov = get_series("br_ibovespa", start_date=HISTORY_FLOOR, end_date=pd.Timestamp.today())
    return pd.DatetimeIndex(ibov.squeeze().dropna().index).normalize()


def drop_closed(cal: pd.DatetimeIndex, closes: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Remove days of ``cal`` with no close in ``closes``, up to the last close.

    Days after the last close stay (yesterday's close may not be loaded yet).
    """
    if closes.empty:
        return cal
    closed = cal[(cal <= closes.max()) & ~cal.isin(closes)]
    return cal.difference(closed)


def sessions(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """B3 sessions in ``[start, end]``: ANBIMA business days with an Ibovespa close."""
    cal = pd.bdate_range(start, end, freq="C", holidays=list(_anbima_holidays()))
    return drop_closed(cal, _ibov_closes())


def parse_bds(raw: pd.DataFrame) -> Snapshot:
    """``INDX_MWEIGHT_HIST`` frame → weights indexed by B3 code.

    Takes the text column with the most distinct values as the member
    (``PETR4 BZ``; a constant ``IBOV Index`` ticker column is skipped) and the
    first numeric column as the weight; xbbg column names vary across versions.
    """
    if raw is None or raw.empty:
        return pd.Series(dtype=float, name="weight")
    text_cols = [
        c for c in raw.columns
        if raw[c].dtype == object or pd.api.types.is_string_dtype(raw[c])
    ]
    if not text_cols:
        raise ValueError(f"No member column in {list(raw.columns)}")
    member_col = max(text_cols, key=lambda c: raw[c].nunique())
    weights = None
    for col in raw.columns:
        if col == member_col:
            continue
        num = pd.to_numeric(raw[col], errors="coerce")
        if num.notna().any():
            weights = num
            break
    if weights is None:
        raise ValueError(f"No numeric weight column in {list(raw.columns)}")
    codes = raw[member_col].astype(str).str.strip().str.split().str[0].str.upper()
    out = pd.Series(weights.to_numpy(dtype=float), index=codes.to_numpy(), name="weight")
    out = out[out.index != ""]
    return out.groupby(level=0).sum().sort_index()


def bloomberg_fetcher(bbg_ticker: str) -> Fetcher:
    """Fetch one index's composition on a date, with retries."""
    from importlib.metadata import version

    from persevera_tools.data.providers.bloomberg import _get_blp

    blp = _get_blp()
    # xbbg 0.x warns that bds defaults flip to narwhals/long in 1.0; 1.x bds
    # has no ``format`` argument (it would be sent as an override).
    output = {"backend": "pandas"}
    if int(version("xbbg").split(".")[0]) < 1:
        output["format"] = "wide"

    def fetch(date: pd.Timestamp) -> Snapshot:
        last = None
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            try:
                raw = blp.bds(
                    bbg_ticker,
                    "INDX_MWEIGHT_HIST",
                    END_DATE_OVERRIDE=pd.Timestamp(date).strftime("%Y%m%d"),
                    **output,
                )
                return parse_bds(raw)
            except Exception as exc:  # retried, then re-raised
                last = exc
                print(f"  {bbg_ticker} {date.date()}: attempt {attempt} failed: {exc}", flush=True)
                time.sleep(5 * attempt)
        raise last

    return fetch


def _members(snap: Snapshot) -> frozenset:
    return frozenset(snap.index)


def find_snapshots(fetch: Fetcher, cal: pd.DatetimeIndex) -> dict[pd.Timestamp, Snapshot]:
    """Fetch anchors (month-ends, first and last session) and bisect membership changes.

    Every session on which the member set differs from the previous session
    ends up as a key of the result, so carrying snapshots forward reproduces
    membership exactly.
    """
    if cal.empty:
        return {}
    month_ends = pd.Series(cal, index=cal).groupby([cal.year, cal.month]).max()
    anchors = sorted({cal[0], cal[-1], *month_ends.tolist()})
    snaps: dict[pd.Timestamp, Snapshot] = {d: fetch(d) for d in anchors}
    pos = {d: i for i, d in enumerate(cal)}

    def bisect(a: pd.Timestamp, b: pd.Timestamp) -> None:
        if _members(snaps[a]) == _members(snaps[b]):
            return
        i, j = pos[a], pos[b]
        if j - i <= 1:
            return
        m = cal[(i + j) // 2]
        snaps[m] = fetch(m)
        bisect(a, m)
        bisect(m, b)

    for a, b in zip(anchors[:-1], anchors[1:]):
        bisect(a, b)
    return dict(sorted(snaps.items()))


def densify(snaps: dict[pd.Timestamp, Snapshot], cal: pd.DatetimeIndex, field: str) -> pd.DataFrame:
    """One row per member per session, carrying the last snapshot forward."""
    if not snaps:
        return pd.DataFrame(columns=["code", "date", "field", "value"])
    keys = pd.DatetimeIndex(sorted(snaps))
    owner = keys[keys.searchsorted(cal, side="right") - 1]
    frames = []
    for snap_date, days in pd.Series(cal, index=owner).groupby(level=0):
        snap = snaps[snap_date]
        if snap.empty:
            continue
        frames.append(
            pd.DataFrame(
                {
                    "code": snap.index.repeat(len(days)),
                    "date": list(days.to_numpy()) * len(snap),
                    "field": field,
                    "value": snap.to_numpy().repeat(len(days)),
                }
            )
        )
    if not frames:
        return pd.DataFrame(columns=["code", "date", "field", "value"])
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values(["date", "code"]).reset_index(drop=True)


def load_aliases() -> pd.DataFrame:
    from persevera_tools.db.operations import read_sql

    return read_sql("SELECT old_code, new_code, effective_date FROM factor_zoo_alias",
                    date_columns=["effective_date"], raise_errors=True)


def translate(df: pd.DataFrame, aliases: pd.DataFrame) -> pd.DataFrame:
    """Rename old codes to their successors (``factor_zoo.aliases`` rule).

    A row is renamed when its date is on/after the alias ``effective_date``
    (or the alias has none); a successor's row dated before the
    ``effective_date`` gets the old code back. Chains (A→B→C) are followed. When the old and new
    codes are both listed on a day, the successor's row is kept, unless the
    old code shares an index with its successor on more than
    ``TICKER_CHANGE_OVERLAP`` sessions: those are distinct share classes and
    their weights are added.
    """
    if df.empty or aliases.empty:
        return df
    out = df.copy()
    out["_succ"] = False
    out["_from"] = out["code"]
    # Before an alias's effective_date factor_zoo keeps the old code, but
    # Bloomberg reports the current ticker (AXIA6 in 2002 is ELET6 there).
    dated = aliases.dropna(subset=["effective_date"])
    for r in dated.itertuples(index=False):
        back = (out["code"] == r.new_code) & (out["date"] < r.effective_date)
        out.loc[back, "code"] = r.old_code
    amap = aliases.set_index("old_code")
    for _ in range(5):
        hit = out["code"].isin(amap.index)
        if hit.any():
            eff = out.loc[hit, "code"].map(amap["effective_date"])
            in_scope = eff.isna() | (out.loc[hit, "date"] >= eff)
            idx = in_scope[in_scope].index
        else:
            idx = []
        if len(idx) == 0:
            break
        out.loc[idx, "code"] = out.loc[idx, "code"].map(amap["new_code"])
        out.loc[idx, "_succ"] = True
    keys = ["code", "date", "field"]
    clash = out.duplicated(keys, keep=False)
    renamed = out[clash & out["_succ"]]
    overlap = renamed.groupby(["_from", "code", "field"])["date"].nunique()
    classes = overlap[overlap > TICKER_CHANGE_OVERLAP].reset_index()[["_from", "code", "field"]]
    parts = []
    if not classes.empty:
        tagged = renamed.merge(classes, on=["_from", "code", "field"])[keys].drop_duplicates()
        in_sum = out.set_index(keys).index.isin(tagged.set_index(keys).index)
        parts.append(out[in_sum].groupby(keys, as_index=False)["value"].sum())
        out = out[~in_sum]
    # Ticker-change days: rows that were already the successor come first and win.
    parts.append(out.sort_values("_succ", kind="stable").drop_duplicates(keys, keep="first"))
    out = pd.concat([p[keys + ["value"]] for p in parts], ignore_index=True)
    return out[list(df.columns)].sort_values(["date", "code"]).reset_index(drop=True)


def stale_members(members: dict[str, list[str]], end: pd.Timestamp) -> pd.DataFrame:
    """Members whose factor_zoo ``price_close`` is older than ``MAX_STALE_SESSIONS`` sessions.

    ``members`` maps table field → codes on ``end``. Returns code, fields and
    last price date (NaT when there is none in the last ~3 months).
    """
    from persevera_tools.db.operations import read_sql

    codes = sorted({c for cs in members.values() for c in cs})
    if not codes:
        return pd.DataFrame(columns=["code", "fields", "last_price"])
    since = end - pd.Timedelta(days=90)
    last = read_sql(
        "SELECT code, max(date) AS last_price FROM factor_zoo "
        "WHERE field = 'price_close' AND code = ANY(:c) AND date >= :s GROUP BY code",
        params={"c": codes, "s": since.date()}, date_columns=["last_price"], raise_errors=True,
    ).set_index("code")["last_price"]
    window = sessions(since, end)
    cutoff = window[-(MAX_STALE_SESSIONS + 1)] if len(window) > MAX_STALE_SESSIONS else window[0]
    rows = []
    for code in codes:
        lp = last.get(code, pd.NaT)
        if pd.isna(lp) or lp < cutoff:
            fields = ",".join(f for f, cs in members.items() if code in cs)
            rows.append({"code": code, "fields": fields, "last_price": lp})
    return pd.DataFrame(rows, columns=["code", "fields", "last_price"])


def validate(snaps: dict[pd.Timestamp, Snapshot], field: str) -> list[str]:
    """Problems that fail the run (an empty composition after inception).

    Weight sums away from 100% and large membership swings are only printed:
    on a ticker change B3 lists the old and new codes on the same day with the
    same weight (ELET3 and AXIA3 on 2025-10-31, sum 108%), which ``translate``
    collapses before writing.
    """
    problems = []
    seen_members = False
    prev: Optional[Snapshot] = None
    for date, snap in snaps.items():
        if snap.empty:
            if seen_members:
                problems.append(f"{field} {date.date()}: empty composition after inception")
            continue
        seen_members = True
        total = float(snap.sum())
        if abs(total - 100.0) > WEIGHT_SUM_TOLERANCE:
            print(f"  ALERT {field} {date.date()}: weights sum to {total:.2f}%", flush=True)
        if prev is not None and not prev.empty:
            swing = len(_members(snap) ^ _members(prev)) / max(len(prev), 1)
            if swing > MEMBER_CHANGE_ALERT:
                print(f"  ALERT {field} {date.date()}: {swing:.0%} of members changed "
                      f"({len(prev)} -> {len(snap)})", flush=True)
        prev = snap
    return problems


def last_stored_dates() -> dict[str, pd.Timestamp]:
    from persevera_tools.db.operations import read_sql

    df = read_sql(f"SELECT field, max(date) AS d FROM {TABLE} GROUP BY field",
                  date_columns=["d"], raise_errors=True)
    return dict(zip(df["field"], df["d"]))


def replace_window(df: pd.DataFrame, field: str, start: pd.Timestamp, end: pd.Timestamp) -> int:
    """Delete ``field`` rows in ``[start, end]`` and insert ``df``, in one transaction."""
    import psycopg2.extras

    from persevera_tools.db.connection import get_db_engine

    engine = get_db_engine()
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute(
            f"DELETE FROM {TABLE} WHERE field = %s AND date BETWEEN %s AND %s",
            (field, start.date(), end.date()),
        )
        rows = [(r.code, r.date.date(), r.field, float(r.value)) for r in df.itertuples(index=False)]
        psycopg2.extras.execute_values(
            cur, f"INSERT INTO {TABLE} (code, date, field, value) VALUES %s", rows, page_size=5000
        )
        raw.commit()
        return len(rows)
    except BaseException:
        raw.rollback()
        raise
    finally:
        raw.close()
        engine.dispose()


def run(fields: Sequence[str], *, start: Optional[pd.Timestamp] = None,
        dry_run: bool = False, fetcher_factory: Callable[[str], Fetcher] = bloomberg_fetcher) -> int:
    today = pd.Timestamp.today().normalize()
    end_cal = sessions(today - pd.Timedelta(days=15), today - pd.Timedelta(days=1))
    end = end_cal[-1]
    stored = last_stored_dates()
    aliases = load_aliases()
    failed: dict[str, str] = {}
    members_at_end: dict[str, list[str]] = {}

    for field in fields:
        t0 = time.time()
        try:
            first = start if start is not None else stored.get(field, HISTORY_FLOOR)
            cal = sessions(pd.Timestamp(first), end)
            if cal.empty:
                print(f"{field}: up to date ({stored.get(field)})", flush=True)
                continue
            snaps = find_snapshots(fetcher_factory(INDICES[field]), cal)
            with_members = [d for d, s in snaps.items() if not s.empty]
            if not with_members:
                raise ValueError(f"Bloomberg returned no members from {cal[0].date()}")
            if with_members[0] > cal[0]:
                # Stored rows before Bloomberg's first composition stay untouched.
                print(f"  {field}: Bloomberg history starts {with_members[0].date()}; "
                      f"rows before it are kept", flush=True)
                cal = cal[cal >= with_members[0]]
                snaps = {d: s for d, s in snaps.items() if d >= with_members[0]}
            problems = validate(snaps, field)
            if problems:
                raise ValueError("; ".join(problems))
            df = translate(densify(snaps, cal, field), aliases)
            if cal[-1] == end:
                members_at_end[field] = df.loc[df["date"] == end, "code"].tolist()
            n_changes = sum(
                1 for a, b in zip(list(snaps.values())[:-1], list(snaps.values())[1:])
                if _members(a) != _members(b)
            )
            msg = f"{len(snaps)} snapshots, {n_changes} membership changes, {len(df):,} rows"
            if not dry_run:
                replace_window(df, field, cal[0], cal[-1])
                msg += " written"
            print(f"{field}: {cal[0].date()} -> {cal[-1].date()}: {msg} ({time.time() - t0:.0f}s)", flush=True)
        except Exception as exc:
            print(f"FAILED {field}: {exc}", flush=True)
            failed[field] = str(exc)

    stale = stale_members(members_at_end, end)
    for r in stale.itertuples(index=False):
        last = r.last_price.date() if pd.notna(r.last_price) else "none in 90 days"
        print(f"  ALERT {r.code} ({r.fields}): index member on {end.date()}, "
              f"last factor_zoo price_close {last}", flush=True)

    if not dry_run:
        stored = last_stored_dates()
        for field in fields:
            last = stored.get(field)
            behind = len(sessions(last, end)) - 1 if last is not None else None
            if behind is None or behind > MAX_STALE_SESSIONS:
                failed.setdefault(field, f"stale: last stored {last}, {behind} sessions behind {end.date()}")
                print(f"STALE {field}: last stored {last}", flush=True)

    if failed:
        print(f"Failed: {sorted(failed)}", flush=True)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    from persevera_tools.utils.logging import initialize as _initialize_logging

    _initialize_logging()
    p = argparse.ArgumentParser(description="Load B3 index composition from Bloomberg.")
    p.add_argument("--index", nargs="+", choices=sorted(INDICES), default=list(INDICES),
                   help="Table field names to load (default: all).")
    p.add_argument("--start", type=pd.Timestamp, default=None,
                   help="Reload from this date (default: last stored session per index).")
    p.add_argument("--dry-run", action="store_true", help="Fetch and validate; write nothing.")
    args = p.parse_args(argv)
    return run(args.index, start=args.start, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
