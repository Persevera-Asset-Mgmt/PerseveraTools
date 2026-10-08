"""Write paths for ``factor_zoo``: changed-only upserts and history replacement."""

from __future__ import annotations

import io
import logging

import numpy as np
import pandas as pd

from ...db.connection import get_db_engine
from ...db.operations import to_sql

logger = logging.getLogger(__name__)

TABLE = "factor_zoo"
KEYS = ["code", "date", "field"]
# factor_zoo is range-partitioned by year from 2000; earlier rows cannot be stored.
HISTORY_FLOOR = pd.Timestamp("2000-01-01")
_COPY_CHUNK = 1_000_000


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Drop NaN/±inf, rows before the partition floor and duplicate keys."""
    if df.empty:
        return df[["code", "date", "field", "value"]] if set(KEYS) <= set(df.columns) else df
    out = df[["code", "date", "field", "value"]].copy()
    out["date"] = pd.to_datetime(out["date"])
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna(subset=["code", "date", "field", "value"])
    out = out[np.isfinite(out["value"]) & (out["date"] >= HISTORY_FLOOR)]
    return out.drop_duplicates(KEYS, keep="last")


def upsert_changed(df: pd.DataFrame, *, batch_size: int = 5000) -> int:
    """Insert new rows and update only rows whose value changed."""
    df = clean(df)
    if df.empty:
        return 0
    to_sql(df.reset_index(drop=True), table_name=TABLE, primary_keys=KEYS,
           update=True, batch_size=batch_size, only_changed=True)
    return len(df)


def replace_history(df: pd.DataFrame, start: str | pd.Timestamp, end: str | pd.Timestamp) -> tuple[int, int]:
    """Replace stored rows of every (code, field) present in ``df`` within [start, end].

    One transaction: stored rows of those pairs in the window are deleted and
    ``df`` is inserted, so rows that moved date (re-dated quarters, restated
    adjusted prices) do not linger. A (code, field) absent from ``df`` is left
    untouched. Nothing is archived (the disk runs close to full).
    """
    df = clean(df)
    df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
    if df.empty:
        return 0, 0
    engine = get_db_engine()
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("CREATE TEMP TABLE _rh (code text, date date, field text, value double precision) ON COMMIT DROP")
        for i in range(0, len(df), _COPY_CHUNK):
            buf = io.StringIO()
            df.iloc[i:i + _COPY_CHUNK].to_csv(buf, index=False, header=False, date_format="%Y-%m-%d")
            buf.seek(0)
            cur.copy_expert("COPY _rh (code, date, field, value) FROM STDIN WITH (FORMAT csv)", buf)
        cur.execute(
            f"""DELETE FROM {TABLE} z USING (SELECT DISTINCT code, field FROM _rh) s
                WHERE z.code = s.code AND z.field = s.field AND z.date BETWEEN %s AND %s""",
            (pd.Timestamp(start).date(), pd.Timestamp(end).date()),
        )
        deleted = cur.rowcount
        cur.execute(f"INSERT INTO {TABLE} (code, date, field, value) SELECT code, date, field, value FROM _rh")
        inserted = cur.rowcount
        raw.commit()
        return deleted, inserted
    except BaseException:
        raw.rollback()
        raise
    finally:
        raw.close()
        engine.dispose()
