"""One-off full-history recompute of the derived factor_zoo fields (no Bloomberg).

Runs every derived category from 2000 with the current transforms, in
dependency order, and replaces each output field wholesale: a field produced
by a category is deleted from factor_zoo and rewritten (one transaction per
category), so rows the new code no longer produces (non-session liquidity
dates, stale quarterly carries, dead codes) do not linger as an upsert would
leave them.

Before the first write, the derived fields currently in factor_zoo from
``--backup-from`` on (default 2024-01-01) are copied to
``factor_zoo_derived_bak_<YYYYMMDD>`` (CREATE TABLE AS SELECT) for a
before/after comparison; earlier history is not backed up.

Usage::

    python scripts/factor_zoo_recompute_derived.py                 # dry run: counts only
    python scripts/factor_zoo_recompute_derived.py --apply
    python scripts/factor_zoo_recompute_derived.py --apply --category liquidity
"""

from __future__ import annotations

import argparse
import gc
import io
import sys
import time
from pathlib import Path

import pandas as pd
import sqlalchemy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from persevera_tools.data.factor_zoo.fields import preload_factor_definitions  # noqa: E402
from persevera_tools.data.factor_zoo.pipeline import (  # noqa: E402
    DERIVED_DEPENDENT_ORDER,
    DERIVED_INDEPENDENT_ORDER,
    process_category,
)
from persevera_tools.db.connection import get_db_engine  # noqa: E402
from persevera_tools.db.operations import read_sql  # noqa: E402

SQL_MIN_DATE = "1999-12-31"  # load everything stored (factor_zoo starts in 2000)
ORDER = list(DERIVED_INDEPENDENT_ORDER) + list(DERIVED_DEPENDENT_ORDER)
COPY_CHUNK = 1_000_000


def derived_fields_in_db() -> list[str]:
    """Fields in Definições dos Fatores without a Bloomberg code (i.e. derived)."""
    df = preload_factor_definitions()
    return sorted(df.loc[df["Código Bloomberg"].isna(), "Name"].astype(str).unique())


def existing_counts(fields: list[str]) -> pd.Series:
    if not fields:
        return pd.Series(dtype="int64")
    r = read_sql("SELECT field, count(*) AS n FROM factor_zoo WHERE field = ANY(:f) GROUP BY field",
                 params={"f": fields}, raise_errors=True)
    return r.set_index("field")["n"]


def backup(engine, fields: list[str], since: str) -> str:
    name = f"factor_zoo_derived_bak_{pd.Timestamp.today():%Y%m%d}"
    with engine.begin() as conn:
        exists = conn.execute(sqlalchemy.text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name}).scalar()
        if exists:
            print(f"Backup table {name} already exists; keeping it.", flush=True)
            return name
        conn.execute(sqlalchemy.text(
            f"CREATE TABLE {name} AS SELECT * FROM factor_zoo WHERE field = ANY(:f) AND date >= :d"),
            {"f": fields, "d": since})
        n = conn.execute(sqlalchemy.text(f"SELECT count(*) FROM {name}")).scalar()
    print(f"Backup: {n:,} rows copied to {name}", flush=True)
    return name


def replace_fields(engine, df: pd.DataFrame) -> tuple[int, int]:
    """Delete every row of df's fields, COPY df in — one transaction."""
    fields = sorted(df["field"].unique())
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("CREATE TEMP TABLE _dv (code text, date date, field text, value double precision) ON COMMIT DROP")
        # COPY in chunks: one CSV buffer for ~30M rows exhausts memory.
        cols = df[["code", "date", "field", "value"]]
        for i in range(0, len(cols), COPY_CHUNK):
            buf = io.StringIO()
            cols.iloc[i:i + COPY_CHUNK].to_csv(buf, index=False, header=False, date_format="%Y-%m-%d")
            buf.seek(0)
            cur.copy_expert("COPY _dv (code, date, field, value) FROM STDIN WITH (FORMAT csv)", buf)
            del buf
        cur.execute("DELETE FROM factor_zoo WHERE field = ANY(%s)", (fields,))
        deleted = cur.rowcount
        cur.execute("INSERT INTO factor_zoo (code, date, field, value) SELECT code, date, field, value FROM _dv")
        inserted = cur.rowcount
        raw.commit()
        return deleted, inserted
    except BaseException:
        raw.rollback()
        raise
    finally:
        raw.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="Write (default: dry run).")
    p.add_argument("--category", action="append", choices=ORDER, help="Only these categories.")
    p.add_argument("--backup-from", default="2024-01-01", help="Back up derived rows from this date on.")
    args = p.parse_args(argv)
    cats = [c for c in ORDER if not args.category or c in args.category]
    engine = get_db_engine()
    try:
        if args.apply:
            backup(engine, derived_fields_in_db(), args.backup_from)
        summary = []
        for cat in cats:
            t0 = time.time()
            # upload=False: compute only; output_min_date=None keeps the full history.
            df = process_category(cat, sql_min_date=SQL_MIN_DATE, output_min_date=None, upload=False)
            n_raw = len(df)
            df = df.drop_duplicates(["code", "date", "field"], keep="last")
            if len(df) < n_raw:
                print(f"{cat}: dropped {n_raw - len(df):,} duplicate (code, date, field) rows", flush=True)
            fields = sorted(df["field"].unique()) if len(df) else []
            before = existing_counts(fields)
            new = df.groupby("field").size() if len(df) else pd.Series(dtype="int64")
            for f in fields:
                summary.append({"category": cat, "field": f, "rows_now": int(before.get(f, 0)),
                                "rows_new": int(new.get(f, 0)), "codes_new": int(df.loc[df.field == f, "code"].nunique())})
            msg = f"{cat}: {len(df):,} rows, {len(fields)} fields, computed in {time.time() - t0:.0f}s"
            if args.apply and len(df):
                deleted, inserted = replace_fields(engine, df)
                msg += f" | -{deleted:,} +{inserted:,}"
            print(msg, flush=True)
            del df
            gc.collect()
        s = pd.DataFrame(summary)
        if len(s):
            pd.set_option("display.width", 200); pd.set_option("display.max_rows", 300)
            s["change"] = (s.rows_new / s.rows_now.replace(0, pd.NA) - 1).round(3)
            print(s.to_string(index=False))
            print(s.groupby("category")[["rows_now", "rows_new"]].sum().to_string())
        if not args.apply:
            print("Dry run: nothing written (use --apply).")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
