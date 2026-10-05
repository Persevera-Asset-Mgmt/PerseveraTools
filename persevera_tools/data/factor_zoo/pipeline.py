"""Load factor_zoo slices, derive fields, optionally upsert."""


from __future__ import annotations

import logging
import multiprocessing
import time
from typing import Dict, Iterable, Literal, Mapping, Sequence

import pandas as pd

from ...db.operations import read_sql, to_sql
from .fields import (
    clear_factor_definitions_cache,
    get_fields_for_category,
    preload_factor_definitions,
    sanitize_sql_in_literals,
)
from .transforms import (
    compute_accruals,
    compute_liquidity,
    compute_operating_leverage,
    compute_price_momentum,
    compute_price_range,
    compute_quality_variability,
    compute_ratios_growth,
    compute_short_selling,
    compute_value,
    compute_value_timeseries,
)

DerivedCategory = Literal[
    "price_momentum",
    "price_range",
    "liquidity",
    "ratios_growth",
    "value",
    "accruals",
    "operating_leverage",
    "value_timeseries",
    "short_selling",
    "quality_variability",
]

DERIVED_INDEPENDENT_ORDER: tuple[DerivedCategory, ...] = (
    "price_momentum",
    "price_range",
    "liquidity",
    "ratios_growth",
    "value",
    "accruals",
    "operating_leverage",
)

DERIVED_DEPENDENT_ORDER: tuple[DerivedCategory, ...] = (
    "value_timeseries",
    "short_selling",
    "quality_variability",
)

logger = logging.getLogger(__name__)

_VALID_DERIVED: frozenset[str] = frozenset(DERIVED_INDEPENDENT_ORDER + DERIVED_DEPENDENT_ORDER)


def _run_transform(chunk: pd.DataFrame, category: str, extras: Mapping[str, object]) -> pd.DataFrame:
    if category == "price_momentum":
        return compute_price_momentum(chunk)
    if category == "price_range":
        return compute_price_range(chunk)
    if category == "liquidity":
        return compute_liquidity(chunk)
    if category == "ratios_growth":
        return compute_ratios_growth(chunk)
    if category == "value":
        return compute_value(chunk)
    if category == "accruals":
        return compute_accruals(chunk)
    if category == "operating_leverage":
        return compute_operating_leverage(
            chunk,
            rolling_quarters=int(extras.get("rolling_quarters", 8)),
            min_obs_quarters=int(extras.get("min_obs_quarters", 4)),
            clip_dol=float(extras.get("clip_dol", 20.0)),
            min_rev_change_pct=float(extras.get("min_rev_change_pct", 1.0)),
        )
    if category == "value_timeseries":
        return compute_value_timeseries(
            chunk,
            rolling_window_years=int(extras.get("rolling_window_years", 10)),
        )
    if category == "short_selling":
        return compute_short_selling(chunk)
    if category == "quality_variability":
        return compute_quality_variability(
            chunk,
            rolling_window_years=int(extras.get("rolling_window_years", 5)),
            min_obs_quarters=int(extras.get("min_obs_quarters", 8)),
        )
    raise ValueError(f"Unsupported derived category: {category}")


def load_factor_slice(
    fields: Sequence[str], *, sql_min_date: str = "2000-01-01"
) -> pd.DataFrame:
    if not fields:
        return pd.DataFrame(columns=["code", "date", "field", "value"])
    in_clause = sanitize_sql_in_literals(sorted(set(fields)))
    query = (
        "SELECT code, date, field, value FROM factor_zoo "
        f"WHERE field IN {in_clause} AND date > :md "
        "ORDER BY date, field, code"
    )
    # raise_errors: a failed read must not be mistaken for "no rows" and skipped.
    return read_sql(
        query, params={"md": sql_min_date}, date_columns=["date"], raise_errors=True
    )


def finalize_derived(df: pd.DataFrame, *, output_min_date: str | None = "2024-01-01") -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.sort_values(["date", "field", "code"]).copy()
    out["value"] = out["value"].round(5)
    if output_min_date:
        thresh = pd.Timestamp(output_min_date)
        out = out[out["date"] > thresh]
    return out[["code", "date", "field", "value"]]


def process_category(
    category: DerivedCategory | str,
    *,
    fields: Sequence[str] | None = None,
    sql_min_date: str = "2000-01-01",
    output_min_date: str | None = "2024-01-01",
    upload: bool = True,
    table_name: str = "factor_zoo",
    primary_keys: Sequence[str] = ("code", "date", "field"),
    batch_size: int = 5000,
    compute_extras: Mapping[str, object] | None = None,
) -> pd.DataFrame:
    """
    Read ``factor_zoo`` rows for mnemonics configured in Fibery
    ``Definições dos Fatores``, compute derived fields for ``category``, optionally upsert.
    """
    cat = str(category)
    if cat not in _VALID_DERIVED:
        raise ValueError(f"Unknown category {cat!r}. Expected one of {sorted(_VALID_DERIVED)}.")

    if fields is None:
        fields_seq = get_fields_for_category(cat)
    else:
        fields_seq = list(fields)

    chunk = load_factor_slice(fields_seq, sql_min_date=sql_min_date)
    if chunk.empty:
        logger.warning(
            "No factor_zoo rows for category=%s (sample fields %s…) after sql date filter %s",
            cat,
            fields_seq[:10],
            sql_min_date,
        )
        return chunk

    extras = compute_extras or {}
    derived = _run_transform(chunk, cat, extras)
    finalized = finalize_derived(derived, output_min_date=output_min_date)

    if upload and not finalized.empty:
        to_sql(
            finalized.reset_index(drop=True),
            table_name=table_name,
            primary_keys=list(primary_keys),
            update=True,
            batch_size=batch_size,
        )
    elif upload and finalized.empty:
        logger.info("Skip upload — no rows remain after derivation/filters (%s)", cat)

    return finalized


def process_categories_in_order(
    categories: Iterable[DerivedCategory | str],
    *,
    sql_min_date: str = "2000-01-01",
    output_min_date: str | None = "2024-01-01",
    upload: bool = True,
    compute_extras: Mapping[str, object] | None = None,
) -> Dict[str, pd.DataFrame]:
    """Run categories sequentially."""
    preload_factor_definitions()
    outs: Dict[str, pd.DataFrame] = {}
    for cat in categories:
        outs[str(cat)] = process_category(
            cat,
            sql_min_date=sql_min_date,
            output_min_date=output_min_date,
            upload=upload,
            compute_extras=compute_extras,
        )
    return outs


def _process_category_worker(
    category: str,
    *,
    sql_min_date: str,
    output_min_date: str | None,
    upload: bool,
    compute_extras: Mapping[str, object] | None,
    batch_size: int,
) -> tuple[str, int]:
    """Multiprocessing worker: one derived category end-to-end."""
    df = process_category(
        category,
        sql_min_date=sql_min_date,
        output_min_date=output_min_date,
        upload=upload,
        compute_extras=compute_extras,
        batch_size=batch_size,
    )
    return category, len(df)


def run_independent_derived_factors(
    *,
    sql_min_date: str = "2000-01-01",
    output_min_date: str | None = "2024-01-01",
    upload: bool = True,
    compute_extras: Mapping[str, object] | None = None,
    parallel: bool = False,
    max_workers: int | None = None,
    stagger_seconds: float = 10.0,
    batch_size: int = 5000,
) -> Dict[str, pd.DataFrame] | Dict[str, int]:
    """First-stage categories (sequential by default, optional multiprocessing)."""
    if not parallel:
        return process_categories_in_order(
            DERIVED_INDEPENDENT_ORDER,
            sql_min_date=sql_min_date,
            output_min_date=output_min_date,
            upload=upload,
            compute_extras=compute_extras,
        )

    categories = list(DERIVED_INDEPENDENT_ORDER)
    workers = max_workers or min(len(categories), multiprocessing.cpu_count())
    logger.info(
        "Running %d independent categories in parallel (%d workers, %.0fs stagger)",
        len(categories),
        workers,
        stagger_seconds,
    )

    worker_kw = dict(
        sql_min_date=sql_min_date,
        output_min_date=output_min_date,
        upload=upload,
        compute_extras=compute_extras,
        batch_size=batch_size,
    )

    pending: list[tuple[str, multiprocessing.pool.AsyncResult[tuple[str, int]]]] = []
    with multiprocessing.Pool(processes=workers) as pool:
        for i, cat in enumerate(categories):
            if i > 0:
                time.sleep(stagger_seconds)
            logger.info("Starting worker for category=%s", cat)
            pending.append(
                (cat, pool.apply_async(_process_category_worker, (cat,), worker_kw))
            )

        row_counts: Dict[str, int] = {}
        errors: list[tuple[str, BaseException]] = []
        for cat, result in pending:
            try:
                done_cat, n_rows = result.get()
                row_counts[done_cat] = n_rows
                logger.info("Category %s finished (%d rows)", done_cat, n_rows)
            except Exception as exc:
                logger.error("Category %s failed: %s", cat, exc, exc_info=True)
                errors.append((cat, exc))

    if errors:
        failed = ", ".join(c for c, _ in errors)
        raise RuntimeError(
            f"Independent parallel run failed for: {failed}"
        ) from errors[0][1]

    return row_counts


def run_dependent_derived_factors(
    *,
    sql_min_date: str = "2000-01-01",
    output_min_date: str | None = "2024-01-01",
    upload: bool = True,
    compute_extras: Mapping[str, object] | None = None,
) -> Dict[str, pd.DataFrame]:
    """Categories that consume earlier derived mnemonics."""
    return process_categories_in_order(
        DERIVED_DEPENDENT_ORDER,
        sql_min_date=sql_min_date,
        output_min_date=output_min_date,
        upload=upload,
        compute_extras=compute_extras,
    )


def run_all_derived_factors_sequentially(
    *,
    sql_min_date: str = "2000-01-01",
    output_min_date: str | None = "2024-01-01",
    upload: bool = True,
    compute_extras: Mapping[str, object] | None = None,
    parallel_independent: bool = False,
    max_workers: int | None = None,
    stagger_seconds: float = 10.0,
) -> Dict[str, pd.DataFrame] | Dict[str, int | pd.DataFrame]:
    """Runs independent phases then dependents."""
    clear_factor_definitions_cache()
    try:
        out: Dict[str, int | pd.DataFrame] = dict(
            run_independent_derived_factors(
                sql_min_date=sql_min_date,
                output_min_date=output_min_date,
                upload=upload,
                compute_extras=compute_extras,
                parallel=parallel_independent,
                max_workers=max_workers,
                stagger_seconds=stagger_seconds,
            )
        )
        out.update(
            run_dependent_derived_factors(
                sql_min_date=sql_min_date,
                output_min_date=output_min_date,
                upload=upload,
                compute_extras=compute_extras,
            )
        )
        return out
    finally:
        clear_factor_definitions_cache()


def _main(argv: Sequence[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="Derive factor_zoo analytics from persisted Bloomberg snapshots."
    )
    p.add_argument(
        "--category",
        choices=sorted(_VALID_DERIVED),
        help="Run a single derived category.",
    )
    p.add_argument(
        "--phase",
        choices=("independent", "dependent", "all"),
        help='Batch preset (ignored if --category is used). Defaults to "all".',
    )
    p.add_argument("--no-upload", action="store_true")
    p.add_argument("--sql-from", dest="sql_min_date", default="2000-01-01")
    p.add_argument(
        "--output-from",
        dest="output_min_date",
        default="2024-01-01",
        help='Truncate output rows strictly after this ISO date (empty string disables trimming).',
    )
    p.add_argument(
        "--parallel",
        action="store_true",
        help="Run independent categories in parallel (multiprocessing).",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Max worker processes for --parallel (default: min(categories, cpu_count)).",
    )
    p.add_argument(
        "--stagger-seconds",
        type=float,
        default=10.0,
        help="Delay between launching parallel workers (default: 10).",
    )
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO)

    out_min: str | None = args.output_min_date or None
    base_kw = dict(
        sql_min_date=args.sql_min_date,
        output_min_date=out_min,
        upload=not args.no_upload,
    )

    if args.category:
        process_category(args.category, **base_kw)
        return

    phase = args.phase or "all"
    parallel_kw = dict(
        parallel=args.parallel,
        max_workers=args.workers,
        stagger_seconds=args.stagger_seconds,
    )
    if phase == "independent":
        run_independent_derived_factors(**base_kw, **parallel_kw)
    elif phase == "dependent":
        if args.parallel:
            p.error("--parallel applies only to the independent phase")
        run_dependent_derived_factors(**base_kw)
    else:
        run_all_derived_factors_sequentially(
            **base_kw,
            parallel_independent=args.parallel,
            max_workers=args.workers,
            stagger_seconds=args.stagger_seconds,
        )
