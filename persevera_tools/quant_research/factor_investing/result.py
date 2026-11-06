"""Backtest result container and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from ..metrics import (
    calculate_annualized_return,
    calculate_annualized_volatility,
    calculate_max_drawdown,
)
from .stats import hac_regression


@dataclass
class BacktestDiagnostics:
    """
    Diagnostic tables for a backtest run.

    ``rebals``
        One row per formed rebalance: universe size, coverage, turnover, etc.
    ``events``
        Sparse event log (``price_gap``, ``renorm``, ``dropped_at_rebal``) with
        columns ``date``, ``code``, ``event``, ``detail``.
    ``costs``
        Daily cost drag as a fraction of NAV, indexed by date, with columns
        ``trading_cost`` and ``borrow_cost``.
    """

    rebals: pd.DataFrame = field(default_factory=pd.DataFrame)
    events: pd.DataFrame = field(default_factory=pd.DataFrame)
    costs: pd.DataFrame = field(default_factory=pd.DataFrame)

    def __repr__(self) -> str:
        n_rebals = len(self.rebals)
        n_events = len(self.events)
        return f"BacktestDiagnostics(n_rebals={n_rebals}, n_events={n_events})"


@dataclass
class BacktestResult:
    """
    Outputs of ``run_backtest``.

    ``prices`` is the dense close panel the P&L used (date x ticker).
    ``component_scores`` holds the sign-aligned component z-scores behind
    ``scores``, indexed by ``(date, code)`` with one column per component.
    Both feed ``signal_analysis.analyze_signal``.
    """

    nav: pd.Series
    returns: pd.Series
    weights: pd.DataFrame
    scores: pd.DataFrame
    summary: dict[str, Any] = field(default_factory=dict)
    diagnostics: BacktestDiagnostics = field(default_factory=BacktestDiagnostics)
    config: Optional[Any] = None
    components: tuple[str, ...] = ()
    prices: Optional[pd.DataFrame] = None
    component_scores: Optional[pd.DataFrame] = None

    def __repr__(self) -> str:
        n = len(self.returns.dropna())
        sharpe = self.summary.get("sharpe")
        ann = self.summary.get("annualized_return")
        return (
            f"BacktestResult(n_obs={n}, "
            f"ann_return={ann!r}, sharpe={sharpe!r})"
        )


_TRADING_DAYS = 252


def _index_returns(levels: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """Daily returns of a cumulative index on ``dates`` (last level on or before each date)."""
    lv = pd.Series(levels, dtype=float).dropna().sort_index()
    lv.index = pd.DatetimeIndex(lv.index).normalize()
    lv = lv[~lv.index.duplicated(keep="last")]
    if lv.empty or len(dates) < 2:
        return pd.Series(float("nan"), index=dates[1:], dtype=float)
    aligned = lv.reindex(lv.index.union(dates)).ffill().reindex(dates)
    return aligned.pct_change(fill_method=None).iloc[1:]


def _annualize(daily: pd.Series, n_obs: int) -> float:
    if n_obs <= 0:
        return float("nan")
    return float((1.0 + daily.fillna(0.0)).prod() ** (_TRADING_DAYS / n_obs) - 1.0)


def build_summary(
    nav: pd.Series,
    risk_free_rate: float = 0.0,
    *,
    risk_free_index: Optional[pd.Series] = None,
    benchmark_index: Optional[pd.Series] = None,
    subtract_risk_free: bool = True,
) -> dict[str, Any]:
    """
    Performance stats from a NAV series.

    Risk-free leg: ``risk_free_index`` (cumulative index such as the CDI,
    aligned to the NAV dates) or the constant annualized ``risk_free_rate``.
    It enters the Sharpe only when ``subtract_risk_free`` (long-only books);
    a self-financed long/short return is already an excess return.

    ``benchmark_index`` (cumulative, e.g. Ibovespa) adds a daily regression of
    the strategy excess return on the benchmark excess return (both over the
    risk-free leg; the strategy's only when ``subtract_risk_free``):
    ``beta``, ``alpha_annualized`` (intercept x 252) and its Newey-West
    ``alpha_tstat``, plus ``correlation``. With ``subtract_risk_free`` it also
    gives ``active_return`` (annualized return minus the benchmark's),
    ``tracking_error`` and ``information_ratio``.
    """
    nan = float("nan")
    clean = nav.dropna()
    out: dict[str, Any] = {
        "annualized_return": nan,
        "annualized_volatility": nan,
        "max_drawdown": nan,
        "sharpe": nan,
        "total_return": nan,
        "n_obs": 0,
        "risk_free_annualized_return": nan,
        "excess_return": nan,
    }
    if benchmark_index is not None:
        out.update(
            {
                "benchmark_annualized_return": nan,
                "beta": nan,
                "alpha_annualized": nan,
                "alpha_tstat": nan,
                "correlation": nan,
                "active_return": nan,
                "tracking_error": nan,
                "information_ratio": nan,
            }
        )
    if clean.empty:
        return out

    clean = clean.copy()
    clean.index = pd.DatetimeIndex(clean.index).normalize()
    dates = clean.index
    strat = clean.pct_change(fill_method=None).iloc[1:]
    n_obs = int(strat.notna().sum())
    ann = calculate_annualized_return(clean)
    vol = calculate_annualized_volatility(clean, frequency="daily")

    if risk_free_index is not None:
        rf_daily = _index_returns(risk_free_index, dates)
        out["risk_free_coverage"] = float(rf_daily.notna().mean()) if len(rf_daily) else nan
        rf_daily = rf_daily.fillna(0.0)
    elif risk_free_rate:
        rf_daily = pd.Series(
            (1.0 + float(risk_free_rate)) ** (1.0 / _TRADING_DAYS) - 1.0,
            index=strat.index,
        )
    else:
        rf_daily = pd.Series(0.0, index=strat.index)
    rf_ann = _annualize(rf_daily, n_obs)
    rf_used = rf_ann if subtract_risk_free else 0.0

    out.update(
        {
            "annualized_return": ann,
            "annualized_volatility": vol,
            "max_drawdown": calculate_max_drawdown(clean),
            "sharpe": (ann - rf_used) / vol if vol and vol > 0 else nan,
            "total_return": float(clean.iloc[-1] / clean.iloc[0] - 1.0),
            "n_obs": n_obs,
            "risk_free_annualized_return": rf_ann,
            "excess_return": ann - rf_used,
        }
    )

    if benchmark_index is None:
        return out

    bench = _index_returns(benchmark_index, dates)
    out["benchmark_coverage"] = float(bench.notna().mean()) if len(bench) else nan
    y = strat - (rf_daily if subtract_risk_free else 0.0)
    x = (bench - rf_daily).rename("benchmark")
    reg = hac_regression(y, x)
    params, tstats = reg["params"], reg["tstats"]
    out["beta"] = float(params.get("benchmark", nan))
    out["alpha_annualized"] = float(params.get("const", nan)) * _TRADING_DAYS
    out["alpha_tstat"] = float(tstats.get("const", nan))
    pair = pd.concat([strat, bench], axis=1).dropna()
    if len(pair) > 2:
        out["correlation"] = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
    bench_n = int(bench.notna().sum())
    out["benchmark_annualized_return"] = _annualize(bench.dropna(), bench_n)
    if subtract_risk_free and len(pair) > 2:
        active = pair.iloc[:, 0] - pair.iloc[:, 1]
        te = float(active.std() * (_TRADING_DAYS ** 0.5))
        active_ret = _annualize(pair.iloc[:, 0], len(pair)) - _annualize(pair.iloc[:, 1], len(pair))
        out["active_return"] = active_ret
        out["tracking_error"] = te
        out["information_ratio"] = active_ret / te if te > 0 else nan
    return out


def summarize_diagnostics(diagnostics: BacktestDiagnostics) -> dict[str, Any]:
    """Aggregate diagnostic counters for ``BacktestResult.summary``."""
    events = diagnostics.events
    rebals = diagnostics.rebals
    out: dict[str, Any] = {
        "n_price_gap_events": 0,
        "n_renorm_events": 0,
        "n_dropped_at_rebal_events": 0,
        "pct_days_with_renorm": float("nan"),
        "avg_names": float("nan"),
        "avg_turnover": float("nan"),
    }
    if not events.empty and "event" in events.columns:
        counts = events["event"].value_counts()
        out["n_price_gap_events"] = int(counts.get("price_gap", 0))
        out["n_renorm_events"] = int(counts.get("renorm", 0))
        out["n_dropped_at_rebal_events"] = int(counts.get("dropped_at_rebal", 0))

    out.update(summarize_costs(diagnostics.costs))
    if rebals.empty:
        return out
    n_long = rebals["n_long"] if "n_long" in rebals.columns else 0
    n_short = rebals["n_short"] if "n_short" in rebals.columns else 0
    names = pd.Series(n_long).fillna(0) + pd.Series(n_short).fillna(0)
    if len(names):
        out["avg_names"] = float(names.mean())
    if "turnover" in rebals.columns:
        out["avg_turnover"] = float(rebals["turnover"].mean())
    return out


def summarize_costs(
    costs: pd.DataFrame,
    *,
    n_obs: int = 0,
) -> dict[str, Any]:
    """Aggregate trading/borrow drag for ``BacktestResult.summary``."""
    out: dict[str, Any] = {
        "total_trading_cost": 0.0,
        "total_borrow_cost": 0.0,
        "total_cost_drag": 0.0,
        "ann_trading_drag": float("nan"),
        "ann_borrow_drag": float("nan"),
    }
    if costs is None or costs.empty:
        return out

    trading = (
        float(costs["trading_cost"].fillna(0.0).sum())
        if "trading_cost" in costs.columns
        else 0.0
    )
    borrow = (
        float(costs["borrow_cost"].fillna(0.0).sum())
        if "borrow_cost" in costs.columns
        else 0.0
    )
    out["total_trading_cost"] = trading
    out["total_borrow_cost"] = borrow
    out["total_cost_drag"] = trading + borrow
    if n_obs > 0:
        out["ann_trading_drag"] = trading * 252.0 / int(n_obs)
        out["ann_borrow_drag"] = borrow * 252.0 / int(n_obs)
    return out
