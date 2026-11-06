"""Investable universe requires a recent trade; factor values may stay stale."""

import pandas as pd

from persevera_tools.quant_research.factor_investing.config import BacktestConfig
from persevera_tools.quant_research.factor_investing.data import densify_prices
from persevera_tools.quant_research.factor_investing.engine import _form_rebalance_books
from persevera_tools.quant_research.factor_investing.scoring import (
    snapshot_components,
    snapshot_series,
)

AS_OF = pd.Timestamp("2022-06-10")
LAST_TRADE = pd.Timestamp("2022-05-20")
FIELD = "median_dollar_volume_traded_21d"
FACTOR = "momentum_9m"


def _sessions() -> pd.DatetimeIndex:
    return pd.bdate_range(LAST_TRADE, AS_OF)


def _adtv_panel(sessions: pd.DatetimeIndex) -> pd.DataFrame:
    frame = pd.DataFrame(index=sessions, columns=["DEAD", "STALE_ADTV", "LIVE", "STALE_FACTOR"], dtype=float)
    frame.loc[LAST_TRADE, ["DEAD", "STALE_ADTV"]] = 2_000_000.0
    frame.loc[AS_OF, ["LIVE", "STALE_FACTOR"]] = 2_000_000.0
    stacked = frame.stack(future_stack=True)
    stacked.index.names = ["date", "ticker"]
    wide = stacked.unstack("ticker")
    wide.columns = pd.MultiIndex.from_product(
        [wide.columns, [FIELD]], names=["ticker", "descriptor"]
    )
    return wide


def _factor_panel(sessions: pd.DatetimeIndex) -> pd.DataFrame:
    frame = pd.DataFrame(index=sessions, columns=["DEAD", "STALE_ADTV", "LIVE", "STALE_FACTOR"], dtype=float)
    frame.loc[LAST_TRADE, :] = [80.0, 10.0, 1.0, 40.0]
    frame.loc[AS_OF, "LIVE"] = 5.0
    stacked = frame.stack(future_stack=True)
    stacked.index.names = ["date", "ticker"]
    wide = stacked.unstack("ticker")
    wide.columns = pd.MultiIndex.from_product(
        [wide.columns, [FACTOR]], names=["ticker", "descriptor"]
    )
    return wide


def _prices(sessions: pd.DatetimeIndex) -> pd.DataFrame:
    raw = pd.DataFrame(index=sessions, columns=["DEAD", "STALE_ADTV", "LIVE", "STALE_FACTOR"], dtype=float)
    raw.loc[LAST_TRADE, "DEAD"] = 10.0
    raw.loc[AS_OF, ["STALE_ADTV", "LIVE", "STALE_FACTOR"]] = [20.0, 21.0, 22.0]
    return densify_prices(raw, start=LAST_TRADE, end=AS_OF, ffill_limit=5)


def test_adtv_snapshot_drops_stale_print_and_keeps_it_without_a_limit():
    panel = _adtv_panel(_sessions())

    fresh = snapshot_series(panel, AS_OF, FIELD, max_age=5)
    assert pd.isna(fresh["DEAD"])
    assert pd.isna(fresh["STALE_ADTV"])
    assert fresh["LIVE"] == 2_000_000.0

    carried = snapshot_series(panel, AS_OF, FIELD)
    assert carried["DEAD"] == 2_000_000.0
    assert carried["STALE_ADTV"] == 2_000_000.0


def test_factor_snapshot_still_carries_a_stale_value():
    cross = snapshot_components(_factor_panel(_sessions()), AS_OF, [FACTOR])
    assert cross.loc["STALE_FACTOR", FACTOR] == 40.0
    assert cross.loc["DEAD", FACTOR] == 80.0


def test_rebalance_excludes_names_that_no_longer_trade():
    sessions = _sessions()
    config = BacktestConfig(
        start_date=LAST_TRADE,
        end_date=AS_OF,
        components=[FACTOR],
        construction="long_only",
        selection_mode="top_n",
        top_n=5,
        adtv_min=1_000_000,
        price_ffill_limit=5,
        min_names=1,
        rebalance_dates=[AS_OF],
    )
    _scores, weights, _rebals, _events, _components = _form_rebalance_books(
        config,
        rebalance_dates=pd.DatetimeIndex([AS_OF]),
        adtv_panel=_adtv_panel(sessions),
        component_panel=_factor_panel(sessions),
        prices=_prices(sessions),
        components=(FACTOR,),
        higher_is_better={FACTOR: True},
    )

    held = set(weights.columns[weights.iloc[-1] > 0])
    assert "DEAD" not in held
    assert "STALE_ADTV" not in held
    assert held == {"LIVE", "STALE_FACTOR"}
