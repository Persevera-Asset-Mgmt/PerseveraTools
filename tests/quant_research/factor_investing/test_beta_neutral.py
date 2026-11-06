"""Beta-neutral long/short: leg grosses resized so the book's beta is zero."""

import pandas as pd
import pytest

from persevera_tools.quant_research.factor_investing.config import BacktestConfig
from persevera_tools.quant_research.factor_investing.data import densify_prices
from persevera_tools.quant_research.factor_investing.engine import _form_rebalance_books
from persevera_tools.quant_research.factor_investing.portfolio import beta_neutralize

AS_OF = pd.Timestamp("2022-06-10")
ADTV = "median_dollar_volume_traded_21d"
FACTOR = "volatility_12m"
CODES = ["LOW1", "LOW2", "MID1", "MID2", "HIGH1", "HIGH2"]


def _panel(field: str, values: list[float]) -> pd.DataFrame:
    frame = pd.DataFrame([values], index=[AS_OF], columns=CODES, dtype=float)
    frame.columns = pd.MultiIndex.from_product(
        [frame.columns, [field]], names=["ticker", "descriptor"]
    )
    return frame


def test_beta_neutralize_zeroes_net_beta_and_keeps_gross():
    weights = pd.Series({"A": 0.5, "B": 0.5, "C": -0.5, "D": -0.5})
    betas = pd.Series({"A": 0.4, "B": 0.6, "C": 1.2, "D": 1.8})

    out, info = beta_neutralize(weights, betas)

    assert info["beta_long"] == pytest.approx(0.5)
    assert info["beta_short"] == pytest.approx(1.5)
    assert info["gross_long"] == pytest.approx(1.5)
    assert info["gross_short"] == pytest.approx(0.5)
    assert (out * betas).sum() == pytest.approx(0.0)
    assert info["net_beta"] == pytest.approx(0.0)
    assert out.abs().sum() == pytest.approx(2.0)
    # relative weights inside each leg are unchanged
    assert out["A"] == pytest.approx(out["B"])
    assert out["C"] == pytest.approx(out["D"])


def test_beta_neutralize_fills_missing_beta_with_median():
    weights = pd.Series({"A": 0.5, "B": 0.5, "C": -0.5, "D": -0.5})
    betas = pd.Series({"A": 0.5, "B": None, "C": 1.0, "D": 1.5})

    _out, info = beta_neutralize(weights, betas)

    assert info["beta_long"] == pytest.approx((0.5 + 1.0) / 2)


def test_beta_neutralize_leaves_book_when_a_leg_beta_is_not_positive():
    weights = pd.Series({"A": 1.0, "C": -1.0})
    betas = pd.Series({"A": -0.2, "C": 1.0})

    out, info = beta_neutralize(weights, betas)

    pd.testing.assert_series_equal(out, weights)
    assert info["gross_long"] == pytest.approx(1.0)
    assert info["net_beta"] == pytest.approx(-1.2)


def test_beta_neutral_requires_long_short():
    with pytest.raises(ValueError, match="beta_neutral"):
        BacktestConfig(
            start_date=AS_OF, end_date=AS_OF, components=[FACTOR], beta_neutral=True
        )


def test_rebalance_resizes_legs_and_logs_betas():
    config = BacktestConfig(
        start_date=AS_OF,
        end_date=AS_OF,
        components=[FACTOR],
        construction="long_short",
        selection_mode="top_n",
        top_n=2,
        adtv_min=1_000_000,
        min_names=1,
        rebalance_dates=[AS_OF],
        one_class_per_issuer=False,
        beta_neutral=True,
    )
    raw = pd.DataFrame([[10.0] * len(CODES)], index=[AS_OF], columns=CODES)
    _scores, weights, rebals, _events, _components = _form_rebalance_books(
        config,
        rebalance_dates=pd.DatetimeIndex([AS_OF]),
        adtv_panel=_panel(ADTV, [2_000_000.0] * len(CODES)),
        component_panel=_panel(FACTOR, [0.10, 0.12, 0.25, 0.30, 0.50, 0.60]),
        prices=densify_prices(raw, start=AS_OF, end=AS_OF, ffill_limit=5),
        components=(FACTOR,),
        higher_is_better={FACTOR: False},
        beta_panel=_panel("beta", [0.5, 0.5, 1.0, 1.0, 1.5, 1.5]),
    )

    w = weights.iloc[-1]
    assert set(w[w > 0].index) == {"LOW1", "LOW2"}
    assert set(w[w < 0].index) == {"HIGH1", "HIGH2"}
    assert w[w > 0].sum() == pytest.approx(1.5)
    assert -w[w < 0].sum() == pytest.approx(0.5)
    row = rebals.iloc[-1]
    assert row["net_beta"] == pytest.approx(0.0)
    assert row["beta_short"] == pytest.approx(1.5)
