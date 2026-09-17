"""Backtest result container and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from ..metrics import (
    calculate_annualized_return,
    calculate_annualized_volatility,
    calculate_max_drawdown,
    calculate_sharpe_ratio,
)


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
    """Outputs of ``run_backtest``."""

    nav: pd.Series
    returns: pd.Series
    weights: pd.DataFrame
    scores: pd.DataFrame
    summary: dict[str, Any] = field(default_factory=dict)
    diagnostics: BacktestDiagnostics = field(default_factory=BacktestDiagnostics)
    config: Optional[Any] = None
    components: tuple[str, ...] = ()

    def __repr__(self) -> str:
        n = len(self.returns.dropna())
        sharpe = self.summary.get("sharpe")
        ann = self.summary.get("annualized_return")
        return (
            f"BacktestResult(n_obs={n}, "
            f"ann_return={ann!r}, sharpe={sharpe!r})"
        )


def build_summary(
    nav: pd.Series,
    risk_free_rate: float = 0.0,
) -> dict[str, Any]:
    """Compute standard performance stats from a NAV series (price proxy)."""
    clean = nav.dropna()
    if clean.empty:
        return {
            "annualized_return": float("nan"),
            "annualized_volatility": float("nan"),
            "max_drawdown": float("nan"),
            "sharpe": float("nan"),
            "total_return": float("nan"),
            "n_obs": 0,
        }

    total_return = float(clean.iloc[-1] / clean.iloc[0] - 1.0)
    return {
        "annualized_return": calculate_annualized_return(clean),
        "annualized_volatility": calculate_annualized_volatility(clean, frequency="daily"),
        "max_drawdown": calculate_max_drawdown(clean),
        "sharpe": calculate_sharpe_ratio(clean, risk_free_rate=risk_free_rate),
        "total_return": total_return,
        "n_obs": int(len(clean.pct_change(fill_method=None).dropna())),
    }


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
