"""
Motor de Espectro de Alocação (Onshore e Offshore) — Persevera Asset Management.

Estrutura:
  • core    — motor matemático puro (sem dependência de I/O)
  • config  — dataclass SpectrumConfig + validações
  • loaders — fontes de configuração (Fibery, defaults, YAML futuro)

Uso típico:
    from persevera_tools.quant_research.risk_budgeting_spectrum import build_spectrum
    from persevera_tools.quant_research.risk_budgeting_spectrum.loaders import load_from_fibery

    config = load_from_fibery()  # Calibração mais recente em status Aprovada
    result = build_spectrum(config)
"""

from .core import (
    build_spectrum,
    get_profile_summary,
    compute_feasible_vol_range,
    check_feasible_sigma,
)
from .config import (
    SpectrumConfig,
    AssetClass,
    BucketConfig,
    RCTarget,
)

__all__ = [
    "build_spectrum",
    "get_profile_summary",
    "compute_feasible_vol_range",
    "check_feasible_sigma",
    "SpectrumConfig",
    "AssetClass",
    "BucketConfig",
    "RCTarget",
]
