"""
Factor investing backtest engine.

Typical usage::

    from persevera_tools.quant_research.factor_investing import (
        BacktestConfig,
        run_backtest,
    )

    config = BacktestConfig(
        start_date="2020-01-01",
        end_date="2024-12-31",
        style="Momentum",
        construction="long_only",
        rebalance_freq="BME",
        adtv_min=8_000_000,
        trading_cost_bps=10.0,
        borrow_rate=0.0,
    )
    result = run_backtest(config)
    print(result.summary)
"""

from .config import BacktestConfig
from .definitions import (
    clear_factor_definitions_cache,
    get_codes_by_denomination,
    get_factor_components,
    get_factor_options,
    get_higher_is_better_map,
    load_asset_taxonomy,
    load_factor_definitions,
)
from .engine import run_backtest
from .portfolio import scores_to_weights
from .result import BacktestDiagnostics, BacktestResult
from .scoring import calculate_factor_exposure

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "BacktestDiagnostics",
    "run_backtest",
    "calculate_factor_exposure",
    "scores_to_weights",
    "load_factor_definitions",
    "load_asset_taxonomy",
    "clear_factor_definitions_cache",
    "get_factor_options",
    "get_factor_components",
    "get_higher_is_better_map",
    "get_codes_by_denomination",
]
