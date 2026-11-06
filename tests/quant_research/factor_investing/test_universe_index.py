"""Index universe: members on the latest stored session; stale composition fails."""

import pandas as pd
import pytest

from persevera_tools.quant_research.factor_investing.config import BacktestConfig
from persevera_tools.quant_research.factor_investing.data import (
    densify_prices,
    index_members_at,
    resolve_universe_codes,
)
from persevera_tools.quant_research.factor_investing.engine import _form_rebalance_books

AS_OF = pd.Timestamp("2022-06-10")
ADTV = "median_dollar_volume_traded_21d"
FACTOR = "momentum_9m"
CODES = ["AAA3", "BBB3", "CCC3", "DDD3", "EEE3", "FFF3"]


def _membership(rows):
    df = pd.DataFrame(rows, columns=["date", "code"])
    df["date"] = pd.to_datetime(df["date"])
    return df


MEMBERS = _membership(
    [("2022-06-08", c) for c in ["AAA3", "BBB3", "CCC3"]]
    + [("2022-06-09", c) for c in ["AAA3", "BBB3", "DDD3", "EEE3"]]
    + [("2022-06-13", c) for c in CODES]  # after AS_OF: must not leak
)


def _panel(field, values):
    frame = pd.DataFrame([values], index=[AS_OF], columns=CODES, dtype=float)
    frame.columns = pd.MultiIndex.from_product([frame.columns, [field]], names=["ticker", "descriptor"])
    return frame


def test_members_come_from_latest_session_without_look_ahead():
    got = index_members_at(MEMBERS, AS_OF, max_age_days=10)
    assert sorted(got) == ["AAA3", "BBB3", "DDD3", "EEE3"]


def test_stale_or_missing_composition_fails():
    with pytest.raises(ValueError, match="stale"):
        index_members_at(MEMBERS, pd.Timestamp("2022-06-30") - pd.Timedelta(days=0), max_age_days=10)
    with pytest.raises(ValueError, match="No index composition"):
        index_members_at(MEMBERS, pd.Timestamp("2022-06-01"), max_age_days=10)


def test_config_normalizes_universe_index():
    cfg = BacktestConfig(start_date=AS_OF, end_date=AS_OF, components=[FACTOR], universe_index="IBX100")
    assert cfg.universe_index == ("IBX100",)
    with pytest.raises(ValueError, match="universe_index"):
        BacktestConfig(start_date=AS_OF, end_date=AS_OF, components=[FACTOR], universe_index=[" "])


def test_candidate_pool_is_window_members_intersected_with_codes():
    cfg = BacktestConfig(start_date=AS_OF, end_date=AS_OF, components=[FACTOR],
                         universe_index="IBOV", codes=["AAA3", "FFF3", "ZZZ3"])
    assert resolve_universe_codes(cfg, MEMBERS) == ["AAA3", "FFF3"]


def test_rebalance_keeps_only_members_and_logs_count():
    config = BacktestConfig(
        start_date=AS_OF,
        end_date=AS_OF,
        components=[FACTOR],
        construction="long_only",
        selection_mode="top_n",
        top_n=10,
        adtv_min=1_000_000,
        min_names=1,
        rebalance_dates=[AS_OF],
        one_class_per_issuer=False,
        universe_index="IBOV",
    )
    raw = pd.DataFrame([[10.0] * len(CODES)], index=[AS_OF], columns=CODES)
    _scores, weights, rebals, _events, _components = _form_rebalance_books(
        config,
        rebalance_dates=pd.DatetimeIndex([AS_OF]),
        adtv_panel=_panel(ADTV, [2_000_000.0] * len(CODES)),
        component_panel=_panel(FACTOR, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
        prices=densify_prices(raw, start=AS_OF, end=AS_OF, ffill_limit=5),
        components=(FACTOR,),
        higher_is_better={FACTOR: True},
        membership=MEMBERS,
    )
    held = set(weights.columns[weights.iloc[-1] > 0])
    assert held == {"AAA3", "BBB3", "DDD3", "EEE3"}
    assert rebals.iloc[-1]["n_index_members"] == 4
