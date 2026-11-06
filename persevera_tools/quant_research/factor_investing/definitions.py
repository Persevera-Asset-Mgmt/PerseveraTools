"""Factor definitions and asset taxonomy from Fibery."""

from __future__ import annotations

import logging
from typing import Mapping, Optional, Sequence

import pandas as pd

from ...db.fibery import read_fibery
from .config import DEFAULT_MAX_AGE_BY_FREQUENCY

logger = logging.getLogger(__name__)

_FACTOR_TABLE = "Inv-Rsrch-Quant/Definições dos Fatores"
_ASSETS_TABLE = "Inv-Taxonomia/Ativos"

_DEFINITIONS_CACHE: Optional[pd.DataFrame] = None
_ASSETS_CACHE: Optional[pd.DataFrame] = None

_KEEP_COLS = ["Name", "Alias", "Descrição", "Maior Melhor", "Estilo", "Frequência"]

# Name rules for derived factors whose Fibery ``Frequência`` is empty.
_DAILY_PREFIXES = (
    "momentum_", "delta_", "median_", "volatility_", "rsi_", "beta", "price_range",
)
_QUARTERLY_MARKERS = (
    "_margin", "_growth_1y", "_variability", "_trend_deviation",
    "accruals_", "operating_leverage", "roe", "roic", "roa", "roce",
)
_FALLBACK_FREQUENCY = "Trimestral"
_ASSET_KEEP_COLS = ["Name", "Denominação", "Instrumento"]
_DEFAULT_INSTRUMENTS = ("Ação",)


def clear_factor_definitions_cache() -> None:
    """Drop cached Fibery factor-definition and asset-taxonomy rows."""
    global _DEFINITIONS_CACHE, _ASSETS_CACHE
    _DEFINITIONS_CACHE = None
    _ASSETS_CACHE = None


def load_factor_definitions(*, force_reload: bool = False) -> pd.DataFrame:
    """
    Load active factor definitions from Fibery.

    Returns columns: Name, Alias, Descrição, Maior Melhor, Estilo, Frequência.
    """
    global _DEFINITIONS_CACHE
    if _DEFINITIONS_CACHE is not None and not force_reload:
        return _DEFINITIONS_CACHE.copy()

    df = read_fibery(
        table_name=_FACTOR_TABLE,
        include_fibery_fields=False,
        where_filter=["=", ["workflow/state", "enum/name"], "$state"],
        params={"$state": "Ativo"},
    )
    if df.empty:
        _DEFINITIONS_CACHE = pd.DataFrame(columns=_KEEP_COLS)
        return _DEFINITIONS_CACHE.copy()

    if "state" in df.columns:
        df = df[df["state"] == "Ativo"]

    missing = [c for c in _KEEP_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Fibery table {_FACTOR_TABLE!r} missing required columns: {missing}"
        )

    out = df[_KEEP_COLS].copy()
    _DEFINITIONS_CACHE = out
    return out.copy()


def load_asset_taxonomy(*, force_reload: bool = False) -> pd.DataFrame:
    """
    Load equity taxonomy from Fibery ``Inv-Taxonomia/Ativos``.

    Returns columns: Name (ticker), Denominação (e.g. BRL/USD), Instrumento.
    ``read_fibery`` resolves relation enums to display names.
    """
    global _ASSETS_CACHE
    if _ASSETS_CACHE is not None and not force_reload:
        return _ASSETS_CACHE.copy()

    df = read_fibery(
        table_name=_ASSETS_TABLE,
        include_fibery_fields=False,
        where_filter=[
            "=",
            ["Inv-Taxonomia/Classificação Instrumento", "Inv-Taxonomia/Name"],
            "$instrument",
        ],
        params={"$instrument": "Ação"},
    )
    if df.empty:
        _ASSETS_CACHE = pd.DataFrame(columns=_ASSET_KEEP_COLS)
        return _ASSETS_CACHE.copy()

    # read_fibery typically exposes relation display names as short aliases.
    rename = {}
    if "Classificação Denominação" in df.columns and "Denominação" not in df.columns:
        rename["Classificação Denominação"] = "Denominação"
    if "Classificação Instrumento" in df.columns and "Instrumento" not in df.columns:
        rename["Classificação Instrumento"] = "Instrumento"
    if rename:
        df = df.rename(columns=rename)

    missing = [c for c in _ASSET_KEEP_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Fibery table {_ASSETS_TABLE!r} missing required columns: {missing}. "
            f"Available: {list(df.columns)}"
        )

    out = df[_ASSET_KEEP_COLS].copy()
    out["Name"] = out["Name"].astype(str)
    _ASSETS_CACHE = out
    return out.copy()


def get_codes_by_denomination(
    denomination: str = "BRL",
    instruments: Sequence[str] = _DEFAULT_INSTRUMENTS,
    assets: Optional[pd.DataFrame] = None,
) -> list[str]:
    """
    Return tickers from ``Inv-Taxonomia/Ativos`` for a currency denomination.

    Used to separate e.g. VALE3 (BRL) from VALE (USD) before ADTV filtering.
    """
    df = load_asset_taxonomy() if assets is None else assets
    if df.empty:
        return []

    mask = df["Denominação"].astype(str) == str(denomination)
    if instruments:
        mask = mask & df["Instrumento"].astype(str).isin(list(instruments))
    return sorted(df.loc[mask, "Name"].dropna().astype(str).unique().tolist())


def get_factor_options(definitions: Optional[pd.DataFrame] = None) -> dict[str, str]:
    """Return ``{Alias: Name}`` for every active factor."""
    defs = load_factor_definitions() if definitions is None else definitions
    if defs.empty:
        return {}
    return defs.set_index("Alias")["Name"].to_dict()


def get_factor_components(
    style: str,
    definitions: Optional[pd.DataFrame] = None,
) -> dict[str, str]:
    """
    Return ``{Alias: Name}`` for factors tagged with ``style`` in ``Estilo``.

    ``Estilo`` may be a list (multi-select) or a scalar string.
    """
    defs = load_factor_definitions() if definitions is None else definitions
    if defs.empty or "Estilo" not in defs.columns:
        return {}

    def _has_style(styles: object) -> bool:
        if isinstance(styles, list):
            return style in styles
        if styles is None or (isinstance(styles, float) and pd.isna(styles)):
            return False
        return str(styles).strip() == style

    mask = defs["Estilo"].apply(_has_style)
    subset = defs.loc[mask]
    if subset.empty:
        return {}
    return subset.set_index("Alias")["Name"].to_dict()


def get_higher_is_better_map(
    definitions: Optional[pd.DataFrame] = None,
) -> dict[str, bool]:
    """Return ``{Name: bool}`` from the ``Maior Melhor`` column."""
    defs = load_factor_definitions() if definitions is None else definitions
    if defs.empty:
        return {}
    return defs.set_index("Name")["Maior Melhor"].astype(bool).to_dict()


def infer_frequency(name: str) -> Optional[str]:
    """Guess ``Frequência`` from a factor mnemonic; ``None`` when no rule applies."""
    text = str(name)
    if text.endswith("_fwd"):
        return "Consenso"
    if text.startswith(_DAILY_PREFIXES):
        return "Diário"
    if any(marker in text for marker in _QUARTERLY_MARKERS):
        return "Trimestral"
    return None


def get_component_max_age(
    components: Sequence[str],
    definitions: Optional[pd.DataFrame] = None,
    *,
    max_age_by_frequency: Optional[Mapping[str, int]] = None,
    overrides: Optional[Mapping[str, int]] = None,
) -> dict[str, int]:
    """
    Return ``{component: max age in calendar days}``.

    Precedence: ``overrides`` → Fibery ``Frequência`` → ``infer_frequency`` →
    ``Trimestral`` (logged as a warning, being the loosest bounded limit).
    """
    by_freq = {**DEFAULT_MAX_AGE_BY_FREQUENCY, **dict(max_age_by_frequency or {})}
    defs = load_factor_definitions() if definitions is None else definitions
    freq_map: dict[str, object] = {}
    if not defs.empty and "Frequência" in defs.columns:
        freq_map = defs.set_index("Name")["Frequência"].to_dict()

    out: dict[str, int] = {}
    unresolved: list[str] = []
    for comp in components:
        if overrides and comp in overrides:
            out[comp] = int(overrides[comp])
            continue
        freq = freq_map.get(comp)
        if not isinstance(freq, str) or freq not in by_freq:
            freq = infer_frequency(comp)
        if freq is None:
            freq = _FALLBACK_FREQUENCY
            unresolved.append(comp)
        out[comp] = int(by_freq[freq])
    if unresolved:
        logger.warning(
            "No Frequência for %s; using %s age limit (%d days)",
            unresolved,
            _FALLBACK_FREQUENCY,
            by_freq[_FALLBACK_FREQUENCY],
        )
    return out


def resolve_component_names(
    style: Optional[str],
    components: Optional[tuple[str, ...]],
    definitions: Optional[pd.DataFrame] = None,
) -> tuple[str, ...]:
    """Resolve scoring mnemonics: explicit components override style lookup."""
    if components:
        return tuple(components)
    if not style:
        raise ValueError("Either style or components must be provided")
    names = tuple(get_factor_components(style, definitions).values())
    if not names:
        raise ValueError(
            f"No components found for style {style!r}; "
            "pass components explicitly or check Fibery Estilo tags."
        )
    return names
