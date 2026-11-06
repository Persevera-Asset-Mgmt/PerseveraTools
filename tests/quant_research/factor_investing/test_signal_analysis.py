"""Signal diagnostics on synthetic panels with a known signal."""

import numpy as np
import pandas as pd
import pytest

from persevera_tools.quant_research.factor_investing.result import BacktestResult
from persevera_tools.quant_research.factor_investing.signal_analysis import (
    analyze_signal,
    fama_macbeth,
    forward_returns,
    information_coefficient,
    monotonicity_test,
    quantile_returns,
)


def _synthetic(strength: float, n_dates: int = 60, n_names: int = 80, seed: int = 0):
    """Monthly scores; next-month return = strength * score + noise."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2015-01-01", periods=n_dates * 21 + 1)
    dates = days[::21][:n_dates]
    codes = [f"C{i:03d}" for i in range(n_names)]
    scores = pd.DataFrame(rng.normal(size=(n_dates, n_names)), index=dates, columns=codes)
    noise = rng.normal(scale=0.05, size=(n_dates, n_names))
    period_ret = strength * scores.to_numpy() + noise
    # Spread each period return evenly over its 21 sessions.
    daily = np.repeat((1 + period_ret) ** (1 / 21) - 1, 21, axis=0)
    px = np.vstack([np.ones(n_names), np.cumprod(1 + daily, axis=0)])
    prices = pd.DataFrame(px[: len(days)], index=days[: len(px)], columns=codes)
    comp = pd.concat(
        {
            "good": scores,
            "noise": pd.DataFrame(rng.normal(size=scores.shape), index=dates, columns=codes),
        },
        axis=1,
    ).stack(level=1, future_stack=True)
    comp.index.names = ["date", "code"]
    return scores, prices, comp


def _result(scores, prices, comp=None) -> BacktestResult:
    return BacktestResult(
        nav=pd.Series(dtype=float),
        returns=pd.Series(dtype=float),
        weights=pd.DataFrame(),
        scores=scores,
        prices=prices,
        component_scores=comp,
    )


def test_forward_returns_match_period_returns():
    scores, prices, _ = _synthetic(0.02)
    fwd = forward_returns(prices, scores.index, 1)
    expected = prices.loc[scores.index[1]] / prices.loc[scores.index[0]] - 1
    pd.testing.assert_series_equal(fwd.iloc[0], expected, check_names=False)
    assert fwd.iloc[-1].isna().all()
    fwd3 = forward_returns(prices, scores.index, 3)
    assert fwd3.iloc[-3:].isna().all().all()


def test_strong_signal_is_detected():
    scores, prices, comp = _synthetic(0.02)
    report = analyze_signal(_result(scores, prices, comp), controls=None, n_boot=300)
    s = report.summary
    assert s["rank_ic_mean"] > 0.3
    assert s["rank_ic_tstat"] > 5
    assert s["spread_tstat"] > 5
    assert s["monotonic_pvalue"] < 0.05
    assert s["fm_coef"] == pytest.approx(0.02, rel=0.25)
    assert s["fm_tstat"] > 5
    assert report.component_ic.loc["good", "mean"] > 0.3
    assert abs(report.component_ic.loc["noise", "mean"]) < 0.05
    assert abs(report.component_correlation.loc["good", "noise"]) < 0.1
    # Independent draws every month -> no persistence.
    assert abs(s["score_autocorrelation"]) < 0.1
    # Signal only predicts the next period: IC decays with the horizon.
    assert report.ic_decay.loc[1, "mean"] > report.ic_decay.loc[6, "mean"]


def test_noise_signal_is_not_significant():
    scores, prices, _ = _synthetic(0.0, seed=1)
    report = analyze_signal(_result(scores, prices), controls=None, n_boot=300)
    s = report.summary
    assert abs(s["rank_ic_tstat"]) < 3
    assert abs(s["fm_tstat"]) < 3


def test_inverted_signal_is_not_monotonic_increasing():
    scores, prices, _ = _synthetic(-0.02, seed=1)
    report = analyze_signal(_result(scores, prices), controls=None, n_boot=300)
    assert report.summary["monotonic_pvalue"] > 0.5
    assert report.summary["spread_tstat"] < -5


def test_fama_macbeth_control_absorbs_a_proxy_signal():
    scores, prices, _ = _synthetic(0.02, seed=2)
    rng = np.random.default_rng(5)
    # The "signal" is only a noisy proxy of the true driver (the control).
    proxy = scores + pd.DataFrame(
        rng.normal(scale=0.3, size=scores.shape), index=scores.index, columns=scores.columns
    )
    fwd = forward_returns(prices, scores.index, 1)
    uni, _ = fama_macbeth(proxy, fwd)
    ctl, _ = fama_macbeth(proxy, fwd, {"driver": scores})
    assert uni.loc["signal", "tstat"] > 5
    assert abs(ctl.loc["signal", "tstat"]) < 3
    assert ctl.loc["driver", "tstat"] > 5


def test_quantiles_and_monotonicity_shapes():
    scores, prices, _ = _synthetic(0.02, seed=3)
    fwd = forward_returns(prices, scores.index, 1)
    q = quantile_returns(scores, fwd, 5)
    assert list(q.columns) == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert q.mean().is_monotonic_increasing
    mono = monotonicity_test(q, n_boot=200)
    assert mono["n_increasing_steps"] == 4
    ic = information_coefficient(scores, fwd)
    assert set(ic.columns) == {"ic", "rank_ic", "n"}
