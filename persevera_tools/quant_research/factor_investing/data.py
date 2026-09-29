"""Historical panel loaders for the factor backtest engine."""

from __future__ import annotations

import re
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


_RADICAL_RE = re.compile(r"^([A-Z0-9]{4})\d{1,2}[A-Z]?$")


def ticker_radical(code: str) -> str:
    """
    B3 issuer root of a ticker (``PETR4`` → ``PETR``, ``TAEE11`` → ``TAEE``).

    Codes that do not follow the B3 pattern (e.g. USD listings) are their own root.
    """
    text = str(code).strip().upper()
    match = _RADICAL_RE.match(text)
    return match.group(1) if match else text


def most_liquid_per_radical(adtv: pd.Series) -> pd.Index:
    """
    Keep the highest-ADTV ticker of each radical; ties break alphabetically.

    ``adtv`` is indexed by ticker; names with missing ADTV are dropped.
    """
    clean = adtv.dropna()
    if clean.empty:
        return pd.Index([])
    frame = pd.DataFrame(
        {
            "code": clean.index.astype(str),
            "adtv": clean.to_numpy(dtype=float),
        }
    )
    frame["radical"] = frame["code"].map(ticker_radical)
    frame = frame.sort_values(["radical", "adtv", "code"], ascending=[True, False, True])
    keep = frame.drop_duplicates("radical", keep="first")["code"]
    return pd.Index(clean.index[clean.index.astype(str).isin(keep)])


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
    holidays: Optional[Sequence[DateLike]] = None,
    drop_empty_sessions: bool = True,
) -> pd.DataFrame:
    """
    Reindex prices to a business-day calendar and forward-fill short gaps.

    Prevents multi-day price moves from being attributed to a single session
    when intermediate dates are missing for some (or all) tickers.

    ``holidays`` (e.g. ``utils.dates.get_holidays()``) are removed from the
    weekday calendar. Prices printed on a listed holiday are dropped; the move
    is still captured by the next session's return.

    ``drop_empty_sessions`` also removes calendar days on which no ticker in
    ``prices`` has a print (B3-only closures such as 24/12, 31/12, 25/01 that
    are absent from the ANBIMA list). With a small ``prices`` panel a day on
    which every name is suspended is removed too.
    """
    if prices.empty:
        return prices

    start_ts = _to_timestamp(start)
    end_ts = _to_timestamp(end)
    cal_start = min(pd.Timestamp(prices.index.min()).normalize(), start_ts)
    cal_end = max(pd.Timestamp(prices.index.max()).normalize(), end_ts)
    if holidays:
        calendar = pd.bdate_range(
            cal_start,
            cal_end,
            freq="C",
            holidays=[_to_timestamp(h) for h in holidays],
        )
    else:
        calendar = pd.bdate_range(cal_start, cal_end)

    dense = prices.reindex(calendar)
    if drop_empty_sessions:
        dense = dense.loc[dense.notna().any(axis=1)]
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
