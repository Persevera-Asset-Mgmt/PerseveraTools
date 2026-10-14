from .data import get_emissions, get_series, get_references
from .metrics import calculate_spread, calculate_duration, calculate_duration_stats
from .pipeline import SPREAD_SERIES, DURATION_SERIES, run_spread_pipeline

__all__ = [
    'get_emissions',
    'get_series',
    'get_references',
    'calculate_spread',
    'calculate_duration',
    'calculate_duration_stats',
    'SPREAD_SERIES',
    'DURATION_SERIES',
    'run_spread_pipeline',
]
