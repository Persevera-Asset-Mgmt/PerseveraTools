"""Historical panel loaders for the factor backtest engine."""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd

from ...data import get_descriptors
from .config import BacktestConfig, DateLike, _to_timestamp
from .definitions import get_codes_by_denomination


def load_factor_panel(
    fields: Sequence[str],
    start_date: DateLike,
    end_date: DateLike,
    codes: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """
    Load a date × (ticker, descriptor) panel from ``factor_zoo`` via get_descriptors.

    If ``codes`` is ``None``, loads every ticker present in ``factor_zoo`` for the
    requested fields.
    """
    if not fields:
        return pd.DataFrame()

    start = _to_timestamp(start_date)
    end = _to_timestamp(end_date)
    tickers: Optional[list[str]] = list(codes) if codes is not None else None

    result = get_descriptors(
        tickers=tickers,
        descriptors=list(fields),
        start_date=start,
        end_date=end,
    )

    if isinstance(result, pd.Series):
        ticker = (tickers[0] if tickers else result.name) or "unknown"
        field = fields[0]
        frame = result.to_frame(name=(str(ticker), field))
        frame.columns = pd.MultiIndex.from_tuples(
            frame.columns, names=["ticker", "descriptor"]
        )
        return frame

    if isinstance(result.columns, pd.MultiIndex):
        result.columns = result.columns.set_names(["ticker", "descriptor"])
        return result.sort_index()

    if tickers is not None and len(tickers) == 1:
        ticker = tickers[0]
        frame = result.copy()
        frame.columns = pd.MultiIndex.from_product(
            [[ticker], frame.columns], names=["ticker", "descriptor"]
        )
        return frame.sort_index()

    if len(fields) == 1:
        field = fields[0]
        frame = result.copy()
        frame.columns = pd.MultiIndex.from_product(
            [frame.columns, [field]], names=["ticker", "descriptor"]
        )
        return frame.sort_index()

    return result.sort_index()


def resolve_universe_codes(config: BacktestConfig) -> Optional[list[str]]:
    """
    Resolve the candidate ticker pool.

    - If ``config.codes`` is set, use that list (still filtered by ADTV at rebalance).
    - Otherwise, use Fibery ``Inv-Taxonomia/Ativos`` filtered by ``denomination``
      (default BRL) and equity instruments — **not** the live Ações Ativas list —
      so historical names remain available; investability is decided by ADTV.
    """
    if config.codes is not None:
        return list(config.codes)
    return get_codes_by_denomination(config.denomination)


def load_backtest_panels(
    config: BacktestConfig,
    components: Sequence[str],
) -> dict[str, pd.DataFrame]:
    """
    Load component, ADTV and price panels needed for a backtest run.

    Candidate pool comes from ``resolve_universe_codes`` (taxonomy by denomination,
    or an explicit ``codes`` list). The investable universe on each rebalance date
    is names still printing ``price_field`` within ``price_ffill_limit`` business
    days and with ``adtv_field >= adtv_min`` over that same window.
    """
    codes = resolve_universe_codes(config)
    if codes is not None and not codes:
        raise ValueError(
            f"No taxonomy codes for denomination {config.denomination!r}"
        )

    lookback_start = config.start_date - pd.Timedelta(days=365)

    component_fields = list(dict.fromkeys(list(components)))
    all_fields = list(
        dict.fromkeys(component_fields + [config.adtv_field, config.price_field])
    )

    full = load_factor_panel(
        fields=all_fields,
        start_date=lookback_start,
        end_date=config.end_date,
        codes=codes,
    )
    if full.empty:
        raise ValueError("No factor_zoo data returned for the requested codes/fields")

    def _subset(fields: Sequence[str]) -> pd.DataFrame:
        available = [col for col in full.columns if col[1] in fields]
        if not available:
            return pd.DataFrame()
        return full.loc[:, available]

    return {
        "components": _subset(component_fields),
        "adtv": _subset([config.adtv_field]),
        "prices": _subset([config.price_field]),
    }


def densify_prices(
    prices: pd.DataFrame,
    *,
    start: DateLike,
    end: DateLike,
    ffill_limit: int = 5,
) -> pd.DataFrame:
    """
    Reindex prices to a business-day calendar and forward-fill short gaps.

    Prevents multi-day price moves from being attributed to a single session
    when intermediate dates are missing for some (or all) tickers.
    """
    if prices.empty:
        return prices

    start_ts = _to_timestamp(start)
    end_ts = _to_timestamp(end)
    cal_start = min(pd.Timestamp(prices.index.min()).normalize(), start_ts)
    cal_end = max(pd.Timestamp(prices.index.max()).normalize(), end_ts)
    calendar = pd.bdate_range(cal_start, cal_end)

    dense = prices.reindex(calendar)
    if ffill_limit and ffill_limit > 0:
        dense = dense.ffill(limit=int(ffill_limit))
    return dense


def build_rebalance_dates(config: BacktestConfig, calendar: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """
    Build rebalance dates inside ``[start_date, end_date]``.

    Explicit ``rebalance_dates`` are filtered to the available calendar (last
    session on or before each requested date). Otherwise dates are generated
    from ``rebalance_freq`` and snapped to the trading calendar.
    """
    if calendar.empty:
        return pd.DatetimeIndex([])

    cal = pd.DatetimeIndex(pd.to_datetime(calendar)).normalize().unique().sort_values()
    start = config.start_date
    end = config.end_date
    cal = cal[(cal >= start) & (cal <= end)]
    if cal.empty:
        return pd.DatetimeIndex([])

    if config.rebalance_dates is not None:
        snapped = []
        for d in config.rebalance_dates:
            ts = _to_timestamp(d)
            if ts < start or ts > end:
                continue
            eligible = cal[cal <= ts]
            if len(eligible):
                snapped.append(eligible[-1])
        return pd.DatetimeIndex(sorted(set(snapped)))

    raw = pd.date_range(start=start, end=end, freq=config.rebalance_freq)
    snapped = []
    for d in raw:
        eligible = cal[cal <= d.normalize()]
        if len(eligible):
            snapped.append(eligible[-1])
    if not snapped and len(cal):
        snapped = [cal[0]]
    return pd.DatetimeIndex(sorted(set(snapped)))
