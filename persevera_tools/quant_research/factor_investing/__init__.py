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
        trading_cost_bps=10.0,
        borrow_rate=0.0,
    )
    result = run_backtest(config)
    print(result.summary)

The default universe is the IBX100 ∪ SMLL members (``universe_index``) with no
ADTV floor. For the whole BRL pool pass ``universe_index=None`` together with an
``adtv_min`` such as ``8_000_000``.
"""

from .config import DEFAULT_MAX_AGE_BY_FREQUENCY, BacktestConfig
from .definitions import (
    clear_factor_definitions_cache,
    get_codes_by_denomination,
    get_component_max_age,
    get_factor_components,
    get_factor_options,
    get_higher_is_better_map,
    load_asset_taxonomy,
    load_factor_definitions,
)
from .engine import run_backtest
from .portfolio import beta_neutralize, scores_to_weights
from .result import BacktestDiagnostics, BacktestResult
from .scoring import calculate_factor_exposure, snapshot_components

__all__ = [
    "BacktestConfig",
    "DEFAULT_MAX_AGE_BY_FREQUENCY",
    "BacktestResult",
    "BacktestDiagnostics",
    "run_backtest",
    "calculate_factor_exposure",
    "snapshot_components",
    "scores_to_weights",
    "beta_neutralize",
    "load_factor_definitions",
    "load_asset_taxonomy",
    "clear_factor_definitions_cache",
    "get_factor_options",
    "get_factor_components",
    "get_component_max_age",
    "get_higher_is_better_map",
    "get_codes_by_denomination",
]
