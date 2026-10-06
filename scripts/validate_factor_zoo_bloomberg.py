"""One-off validation of the factor_zoo Bloomberg changes (run on the Bloomberg PC).

Reads Bloomberg and the database; writes NOTHING to the database or Fibery.
Results go to ``examples/validation_out/`` (gitignored):

``adjustment_check.csv`` / ``summary.txt``
    1. The pinned bdh adjustment elements are accepted by this xbbg version.
    2. ``price_close`` (total-return) and split-adjusted per-share fields match
       what is stored in ``factor_zoo``.
    3. ``price_close_split_adj`` differs from ``price_close`` only by
       dividends: ratio < 1 in the past, -> 1 today, no jump at the BBAS3
       2:1 split (ex 2024-04-16).

``delisted_check.csv``
    For every code with ``price_close`` in ``factor_zoo`` but not in
    ``Ações Ativas``: does ``<code> BZ|US Equity`` resolve, and does its
    price history match the stored one (ratio ≈ 1 over the overlap)?
    Codes not marked ``ok`` need a manual Bloomberg ticker mapping.

Usage::

    python scripts/validate_factor_zoo_bloomberg.py
    python scripts/validate_factor_zoo_bloomberg.py --skip-delisted
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from persevera_tools.data.providers.bloomberg import BloombergProvider  # noqa: E402
from persevera_tools.db.fibery import read_fibery  # noqa: E402
from persevera_tools.db.operations import read_sql  # noqa: E402

OUT = ROOT / "examples" / "validation_out"
CHECK_CODES = ["BBAS3", "ITUB4", "VALE3", "PETR4"]
CHECK_START = "2015-01-01"
B3_CODE_RE = r"^[A-Z0-9]{4}\d{1,2}B?$"
BATCH = 100
RATIO_TOL = 1e-3

_lines: list[str] = []


def log(msg: str = "") -> None:
    print(msg, flush=True)
    _lines.append(msg)


def stored(codes, fields, start) -> pd.DataFrame:
    return read_sql(
        "SELECT code, date, field, value FROM factor_zoo "
        "WHERE code = ANY(:codes) AND field = ANY(:fields) AND date >= :start",
        params={"codes": list(codes), "fields": list(fields), "start": start},
        date_columns=["date"],
        raise_errors=True,
    )


def fetch(bp: BloombergProvider, category: str, tickers: dict, exchange: str, **kw) -> pd.DataFrame:
    """Production code path (adjustment elements included), without saving."""
    return bp.get_data(
        category=category,
        data_type="company",
        exchanges=[exchange],
        use_fund_currency=True,
        custom_tickers=tickers,
        **kw,
    )


def compare(new: pd.DataFrame, old: pd.DataFrame) -> pd.DataFrame:
    m = new.merge(old, on=["code", "date", "field"], suffixes=("_bbg", "_db"))
    m = m[(m["value_db"] != 0) & m["value_bbg"].notna()]
    m["ratio"] = m["value_bbg"] / m["value_db"]
    g = m.groupby(["code", "field"])["ratio"]
    return pd.DataFrame({
        "n_overlap": g.size(),
        "ratio_median": g.median(),
        "ratio_min": g.min(),
        "ratio_max": g.max(),
        "share_within_tol": g.apply(lambda r: float(((r - 1).abs() <= RATIO_TOL).mean())),
    }).reset_index()


def check_adjustment(bp: BloombergProvider) -> None:
    log("== 1-3. Adjustment elements and stored history")
    tickers = {f"{c} BZ Equity": c for c in CHECK_CODES}
    frames = {}
    for category, kw in (
        ("market", {}),
        ("market_split_adj", {}),
        ("consensus", {"best_fperiod_override": "1BF"}),
    ):
        try:
            bp.start_date = CHECK_START
            frames[category] = fetch(bp, category, tickers, "BZ", **kw)
            log(f"   {category}: OK, {len(frames[category]):,} rows")
        except Exception:
            log(f"   {category}: FAILED - adjustment elements likely rejected by this xbbg")
            log(traceback.format_exc())
    if not frames:
        return

    new = pd.concat(frames.values(), ignore_index=True)
    fields = ["price_close", "num_shares_traded", "market_cap",
              "earnings_per_share_fwd", "book_value_per_share_fwd"]
    cmp = compare(new[new["field"].isin(fields)], stored(CHECK_CODES, fields, CHECK_START))
    cmp.to_csv(OUT / "adjustment_check.csv", index=False)
    log("   Bloomberg now vs factor_zoo (expect ratio ~ 1 for all):")
    log(cmp.round(5).to_string(index=False))

    px = new[new["field"].isin(["price_close", "price_close_split_adj"])].pivot_table(
        index=["code", "date"], columns="field", values="value"
    ).dropna()
    if {"price_close", "price_close_split_adj"} <= set(px.columns):
        px["ratio"] = px["price_close"] / px["price_close_split_adj"]
        yearly = px["ratio"].groupby([
            px.index.get_level_values("code"),
            px.index.get_level_values("date").year,
        ]).median().unstack(0)
        log("   price_close / price_close_split_adj by year (expect < 1 rising to 1):")
        log(yearly.round(3).to_string())
        split = px.xs("BBAS3", level="code")["price_close_split_adj"]
        jump = split.pct_change().abs().loc["2024-04-10":"2024-04-22"].max()
        log(f"   BBAS3 max daily move of price_close_split_adj around the 2024-04-16 split: "
            f"{jump:.1%} (a 2:1 split left unadjusted would show ~50%)")


def check_delisted(bp: BloombergProvider) -> None:
    log("\n== 4. Delisted codes")
    active = set(read_fibery(table_name="Inv-Rsrch-Quant/Ações Ativas")["Name"].astype(str))
    hist = read_sql(
        "SELECT code, min(date) AS first_date, max(date) AS last_date, count(*) AS n_obs "
        "FROM factor_zoo WHERE field = 'price_close' GROUP BY code",
        date_columns=["first_date", "last_date"],
        raise_errors=True,
    )
    d = hist[~hist["code"].isin(active)].copy()
    d["exchange"] = np.where(d["code"].str.match(B3_CODE_RE), "BZ", "US")
    d["ticker"] = d["code"] + " " + d["exchange"] + " Equity"
    log(f"   {len(d)} delisted codes")

    # Overlap window: the last 2 years of each code's stored history, fetched
    # in batches that share a start date to keep requests small.
    d["start"] = (d["last_date"] - pd.DateOffset(years=2)).dt.year.astype(str) + "-01-01"
    results = []
    for (exchange, start), grp in d.groupby(["exchange", "start"]):
        for i in range(0, len(grp), BATCH):
            chunk = grp.iloc[i:i + BATCH]
            tickers = dict(zip(chunk["ticker"], chunk["code"]))
            bp.start_date = start
            try:
                new = fetch(bp, "market", tickers, exchange,
                            custom_fields={"PX_LAST": "price_close"})
            except Exception as exc:
                log(f"   batch {exchange} {start} ({len(chunk)}): FAILED {exc}")
                new = pd.DataFrame(columns=["code", "date", "field", "value"])
            old = stored(chunk["code"], ["price_close"], start)
            old = old[old["date"] <= chunk["last_date"].max()]
            results.append(compare(new, old))
            log(f"   batch {exchange} {start}: {len(chunk)} codes, "
                f"{new['code'].nunique() if len(new) else 0} returned")

    res = pd.concat(results, ignore_index=True) if results else pd.DataFrame()
    out = d.merge(res, on="code", how="left").drop(columns=["field", "start"], errors="ignore")
    out["status"] = np.select(
        [
            out["n_overlap"].isna(),
            out["share_within_tol"] >= 0.95,
            (out["ratio_median"] - 1).abs() <= 0.05,
        ],
        ["no_data", "ok", "level_shift"],
        default="mismatch",
    )
    out.to_csv(OUT / "delisted_check.csv", index=False)
    log("   status counts (ok = ticker resolves and history matches):")
    log(out["status"].value_counts().to_string())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--skip-delisted", action="store_true")
    args = p.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)

    import xbbg
    log(f"xbbg {getattr(xbbg, '__version__', '?')}")
    bp = BloombergProvider(start_date=CHECK_START)
    try:
        check_adjustment(bp)
        if not args.skip_delisted:
            check_delisted(bp)
    finally:
        (OUT / "summary.txt").write_text("\n".join(_lines), encoding="utf-8")
        log(f"\nResults in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
