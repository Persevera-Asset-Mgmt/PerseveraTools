"""Diagnostic: how Bloomberg returns ANNOUNCEMENT_DT for quarterly fields (read-only).

Prints the raw bdh output for BBAS3 (announcement dates not applied by the
backfill) and CMIN3 (applied), with the request the pipeline makes today and
with an explicit quarterly / fiscal periodicity. Writes nothing to the
database; output goes to the screen and examples/validation_out/diag_announcement_dt.txt.

    python scripts/diag_announcement_dt.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from persevera_tools.data.providers.bloomberg import BloombergProvider, _bdh  # noqa: E402

OUT = ROOT / "examples" / "validation_out" / "diag_announcement_dt.txt"
TICKERS = ["BBAS3 BZ Equity", "CMIN3 BZ Equity"]
FIELDS = ["BS_TOT_ASSET", "ANNOUNCEMENT_DT"]
BASE = dict(start_date="2017-10-01", end_date="2019-12-31", FILING_STATUS="OR",
            EQY_FUND_CRNCY="BRL", **BloombergProvider._ADJUST_SPLIT_ONLY)

VARIANTS = {
    "A. pipeline today (daily default)": {},
    "B. quarterly + fiscal periods": {"periodicitySelection": "QUARTERLY", "periodicityAdjustment": "FISCAL"},
    "C. daily, all calendar days": {"nonTradingDayFillOption": "ALL_CALENDAR_DAYS"},
}

lines: list[str] = []


def log(s: str = "") -> None:
    print(s, flush=True)
    lines.append(s)


def main() -> int:
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 400)
    import xbbg
    log(f"xbbg {getattr(xbbg, '__version__', '?')}")
    for name, extra in VARIANTS.items():
        log(f"\n===== {name} {extra}")
        for ticker in TICKERS:
            try:
                raw = _bdh(tickers=[ticker], flds=FIELDS, **BASE, **extra)
                log(f"-- {ticker}: type={type(raw).__name__} shape={getattr(raw, 'shape', None)}")
                log(f"   columns={list(getattr(raw, 'columns', []))[:6]}")
                if hasattr(raw, "dtypes"):
                    log(f"   dtypes={dict(raw.dtypes)}")
                log(raw.to_string() if hasattr(raw, "to_string") else repr(raw))
            except Exception as exc:
                log(f"-- {ticker}: ERROR {type(exc).__name__}: {exc}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    log(f"\nSaved to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
