"""Risk-free / benchmark summary and minimum component coverage."""

import math

import numpy as np
import pandas as pd
import pytest

from persevera_tools.quant_research.factor_investing.config import BacktestConfig
from persevera_tools.quant_research.factor_investing.result import build_summary
from persevera_tools.quant_research.factor_investing.scoring import coverage_mask


def _config(**kwargs) -> BacktestConfig:
    params = dict(start_date="2020-01-01", end_date="2020-12-31", components=("a",))
    params.update(kwargs)
    return BacktestConfig(**params)


def test_config_rejects_constant_rate_together_with_series():
    with pytest.raises(ValueError, match="risk_free_series"):
        _config(risk_free_rate=0.1)
    cfg = _config(risk_free_rate=0.1, risk_free_series=None)
    assert cfg.risk_free_series is None


def test_config_coverage_bounds():
    with pytest.raises(ValueError, match="min_component_coverage"):
        _config(min_component_coverage=1.5)
    assert _config().min_component_coverage == 0.5


def test_coverage_mask_counts_only_components_present_on_the_date():
    cross = pd.DataFrame(
        {
            "a": [1.0, 1.0, np.nan, np.nan],
            "b": [1.0, np.nan, 1.0, np.nan],
            "c": [1.0, np.nan, np.nan, np.nan],
            "not_yet": [np.nan] * 4,
        },
        index=["ALL3", "ONE", "ONE_B", "NONE"],
    )
    mask = coverage_mask(cross, 0.5)  # K=3 active -> need 2
    assert mask.to_dict() == {"ALL3": True, "ONE": False, "ONE_B": False, "NONE": False}
    loose = coverage_mask(cross, 0.0)  # at least one component
    assert loose.to_dict() == {"ALL3": True, "ONE": True, "ONE_B": True, "NONE": False}
    assert coverage_mask(cross[["a", "b"]], 0.5).to_dict() == {
        "ALL3": True,
        "ONE": True,
        "ONE_B": True,
        "NONE": False,
    }


def _paths(n: int = 500, seed: int = 0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    rf_d = 0.0004
    bench_r = rng.normal(0.0005, 0.012, n - 1)
    cdi = pd.Series((1 + rf_d) ** np.arange(n), index=idx)
    bench = pd.Series(np.r_[1.0, np.cumprod(1 + bench_r)], index=idx)
    return idx, rf_d, bench_r, cdi, bench


def test_long_only_sharpe_subtracts_cdi_and_reports_benchmark_stats():
    idx, rf_d, bench_r, cdi, bench = _paths()
    strat_r = rf_d + 0.5 * (bench_r - rf_d) + 0.0001
    nav = pd.Series(np.r_[1.0, np.cumprod(1 + strat_r)], index=idx)

    out = build_summary(nav, risk_free_index=cdi, benchmark_index=bench)

    rf_ann = (1 + rf_d) ** 252 - 1
    assert out["risk_free_annualized_return"] == pytest.approx(rf_ann)
    expected = (out["annualized_return"] - rf_ann) / out["annualized_volatility"]
    assert out["sharpe"] == pytest.approx(expected)
    assert out["beta"] == pytest.approx(0.5)
    assert out["alpha_annualized"] == pytest.approx(0.0001 * 252)
    assert out["correlation"] == pytest.approx(1.0)
    assert out["tracking_error"] > 0
    assert not math.isnan(out["information_ratio"])


def test_long_short_sharpe_has_no_risk_free_leg():
    idx, rf_d, bench_r, cdi, bench = _paths(seed=1)
    rng = np.random.default_rng(2)
    strat_r = rng.normal(0.0003, 0.008, len(idx) - 1)
    nav = pd.Series(np.r_[1.0, np.cumprod(1 + strat_r)], index=idx)

    out = build_summary(
        nav, risk_free_index=cdi, benchmark_index=bench, subtract_risk_free=False
    )
    assert out["sharpe"] == pytest.approx(
        out["annualized_return"] / out["annualized_volatility"]
    )
    assert out["excess_return"] == pytest.approx(out["annualized_return"])
    assert math.isnan(out["active_return"])
    assert abs(out["beta"]) < 0.2


def test_constant_rate_matches_old_definition():
    idx, *_ = _paths()
    rng = np.random.default_rng(3)
    nav = pd.Series(np.r_[1.0, np.cumprod(1 + rng.normal(0.0005, 0.01, len(idx) - 1))], index=idx)
    out = build_summary(nav, risk_free_rate=0.10)
    assert out["risk_free_annualized_return"] == pytest.approx(0.10)
    assert out["sharpe"] == pytest.approx(
        (out["annualized_return"] - 0.10) / out["annualized_volatility"]
    )
