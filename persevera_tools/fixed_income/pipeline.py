"""Derive fixed-income spread and duration series and optionally upsert into ``indicadores``."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence, Union

import pandas as pd

from ..db.operations import to_sql
from .metrics import calculate_spread, calculate_duration_stats

logger = logging.getLogger(__name__)

# Built series codes: persevera_ marks house-constructed (not vendor tickers).
SPREAD_SERIES: tuple[Mapping[str, Any], ...] = (
    {
        "code": "persevera_anbima_debentures_spread_di",
        "index_code": "DI",
        "deb_incent_lei_12431": False,
        "calculate_distribution": True,
    },
    {
        "code": "persevera_anbima_debentures_spread_ipca_incent",
        "index_code": "IPCA",
        "deb_incent_lei_12431": True,
        "calculate_distribution": True,
    },
)

DURATION_SERIES: tuple[Mapping[str, Any], ...] = (
    {
        "code": "persevera_anbima_debentures_duration_di",
        "metric": "duration",
        "index_code": "DI",
        "deb_incent_lei_12431": False,
        "calculate_distribution": True,
    },
    {
        "code": "persevera_anbima_debentures_duration_ipca_incent",
        "metric": "duration",
        "index_code": "IPCA",
        "deb_incent_lei_12431": True,
        "calculate_distribution": True,
    },
)

# Configs without a "metric" key default to "spread".
_METRIC_FUNCS = {
    "spread": calculate_spread,
    "duration": calculate_duration_stats,
}


def _spread_to_long(spread: pd.DataFrame, code: str) -> pd.DataFrame:
    """Convert wide spread DataFrame (date index × metrics) to long format."""
    if spread.empty:
        return pd.DataFrame(columns=["date", "code", "field", "value"])

    out = spread.reset_index()
    date_col = out.columns[0]
    if date_col != "date":
        out = out.rename(columns={date_col: "date"})

    long_df = out.melt(id_vars=["date"], var_name="field", value_name="value")
    long_df["code"] = code
    long_df["date"] = pd.to_datetime(long_df["date"], errors="coerce")
    long_df = long_df.dropna(subset=["date", "value"])
    return long_df[["date", "code", "field", "value"]]


def run_spread_pipeline(
    start_date: Optional[Union[str, pd.Timestamp]] = "2000-01-01",
    end_date: Optional[Union[str, pd.Timestamp]] = None,
    *,
    upload: bool = True,
    table_name: str = "indicadores",
    primary_keys: Sequence[str] = ("code", "date", "field"),
    batch_size: int = 5000,
    series: Sequence[Mapping[str, Any]] | None = None,
) -> pd.DataFrame:
    """Calculate configured debenture spread/duration series and optionally upsert to DB.

    Args:
        start_date: Start date passed to ``calculate_spread`` / ``calculate_duration_stats``.
        end_date: Optional end date filter.
        upload: Whether to upsert into ``table_name``.
        table_name: Target table (default ``indicadores``).
        primary_keys: Upsert conflict keys.
        batch_size: Batch size for ``to_sql``.
        series: Optional override of ``SPREAD_SERIES + DURATION_SERIES`` configs.

    Returns:
        Long-format DataFrame with columns ``date``, ``code``, ``field``, ``value``.
    """
    configs = list(series) if series is not None else [*SPREAD_SERIES, *DURATION_SERIES]
    chunks: list[pd.DataFrame] = []

    for cfg in configs:
        code = cfg["code"]
        metric = cfg.get("metric", "spread")
        kwargs = {
            "index_code": cfg["index_code"],
            "start_date": start_date,
            "end_date": end_date,
            "calculate_distribution": cfg.get("calculate_distribution", True),
            "deb_incent_lei_12431": cfg.get("deb_incent_lei_12431"),
        }
        logger.info("Calculating %s series %s (%s)", metric, code, kwargs["index_code"])
        stats = _METRIC_FUNCS[metric](**kwargs)
        long_df = _spread_to_long(stats, code)
        logger.info("Series %s produced %d rows", code, len(long_df))
        if not long_df.empty:
            chunks.append(long_df)

    if not chunks:
        logger.warning("No rows produced; skipping upload")
        return pd.DataFrame(columns=["date", "code", "field", "value"])

    result = pd.concat(chunks, ignore_index=True)
    result = result.drop_duplicates(subset=list(primary_keys), keep="last")

    if upload and not result.empty:
        logger.info("Upserting %d rows into '%s'", len(result), table_name)
        to_sql(
            result.reset_index(drop=True),
            table_name=table_name,
            primary_keys=list(primary_keys),
            update=True,
            batch_size=batch_size,
        )
    elif upload:
        logger.info("Skip upload — no rows after derivation")

    return result
