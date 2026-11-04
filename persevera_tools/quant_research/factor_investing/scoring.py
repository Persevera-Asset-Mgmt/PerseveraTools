"""Cross-sectional factor scoring (screener recipe, without Streamlit)."""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd


def calculate_factor_exposure(
    df: pd.DataFrame,
    factor_name: str = "factor_score",
    higher_is_better_map: Optional[Mapping[str, bool]] = None,
    zR: float = 3.0,
    zC: float = 3.0,
) -> pd.Series:
    """
    Cross-sectional factor scores for one date (codes × component columns).

    Steps:
      1. Robust MAD winsorization (``zR``)
      2. Classical mean/std winsorization (``zC``)
      3. Equal-weighted unit-std z-scores
      4. Sign alignment via ``higher_is_better_map`` (invert when False)
      5. Equal-weighted average across component columns
    """
    if df.empty or df.shape[1] == 0:
        return pd.Series(dtype=float, name=factor_name)

    work = df.apply(pd.to_numeric, errors="coerce")

    median = work.median()
    robust_std = 1.4826 * (work - median).abs().median()
    robust_std = robust_std.replace(0, pd.NA)

    trimmed = work.apply(
        lambda col: col.clip(
            lower=median[col.name] - zR * robust_std[col.name],
            upper=median[col.name] + zR * robust_std[col.name],
        )
        if pd.notna(robust_std[col.name])
        else col
    )

    mean = trimmed.mean()
    std = trimmed.std().replace(0, pd.NA)
    trimmed = trimmed.apply(
        lambda col: col.clip(
            lower=mean[col.name] - zC * std[col.name],
            upper=mean[col.name] + zC * std[col.name],
        )
        if pd.notna(std[col.name])
        else col
    )

    mean = trimmed.mean()
    std = trimmed.std().replace(0, pd.NA)
    z_scores = trimmed.apply(
        lambda col: (col - mean[col.name]) / std[col.name]
        if pd.notna(std[col.name])
        else col * float("nan")
    )

    if higher_is_better_map:
        for col in z_scores.columns:
            if not higher_is_better_map.get(col, True):
                z_scores[col] = -z_scores[col]

    scores = z_scores.mean(axis=1, skipna=True)
    scores.name = factor_name
    return scores


def last_values_with_age(
    panel: pd.DataFrame,
    as_of: pd.Timestamp,
    components: Sequence[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Last value on or before ``as_of`` and its age in calendar days.

    ``panel`` columns are MultiIndex ``(ticker, descriptor)``. Returns two
    codes × components frames (values, ages) with the same shape; both are NaN
    where a ticker never printed the component.
    """
    empty = pd.DataFrame(columns=list(components))
    if panel.empty:
        return empty, empty.copy()
    if not isinstance(panel.columns, pd.MultiIndex):
        raise TypeError(
            "Expected MultiIndex columns (ticker, descriptor) on the factor panel"
        )

    hist = panel.loc[:as_of]
    hist = hist.loc[:, hist.columns.get_level_values(-1).isin(list(components))]
    if hist.empty or hist.shape[1] == 0:
        return empty, empty.copy()

    vals = hist.to_numpy(dtype=float)
    last_pos = np.where(~np.isnan(vals), np.arange(len(hist))[:, None], -1).max(axis=0)
    has = last_pos >= 0
    last = np.full(vals.shape[1], np.nan)
    last[has] = vals[last_pos[has], np.flatnonzero(has)]
    age = np.full(vals.shape[1], np.nan)
    age[has] = (pd.Timestamp(as_of) - hist.index[last_pos[has]]).days

    present = set(hist.columns.get_level_values(-1))
    order = [c for c in components if c in present]
    values = pd.Series(last, index=hist.columns).unstack(level=-1).reindex(columns=order)
    ages = pd.Series(age, index=hist.columns).unstack(level=-1).reindex(columns=order)
    return values, ages


def apply_max_age(
    values: pd.DataFrame,
    ages: pd.DataFrame,
    max_age_days: Optional[Mapping[str, int]] = None,
) -> pd.DataFrame:
    """Blank values older than their component's limit; components without one pass."""
    if not max_age_days or values.empty:
        return values
    limits = pd.Series(
        {c: float(max_age_days.get(c, np.inf)) for c in values.columns}, dtype=float
    )
    return values.where(~ages.gt(limits, axis=1))


def snapshot_components(
    panel: pd.DataFrame,
    as_of: pd.Timestamp,
    components: Sequence[str],
    max_age_days: Optional[Mapping[str, int]] = None,
) -> pd.DataFrame:
    """
    Build a codes × components DataFrame using last values on or before ``as_of``.

    ``panel`` columns are MultiIndex ``(ticker, descriptor)``. With
    ``max_age_days`` (``{component: calendar days}``) a value older than its
    component's limit is treated as missing.
    """
    values, ages = last_values_with_age(panel, as_of, components)
    return apply_max_age(values, ages, max_age_days)


def snapshot_series(
    panel: pd.DataFrame,
    as_of: pd.Timestamp,
    field: str,
    *,
    max_age: Optional[int] = None,
) -> pd.Series:
    """
    Point-in-time Series indexed by ticker for a single descriptor.

    ``panel`` columns are MultiIndex ``(ticker, descriptor)`` or simple ticker
    columns when the panel was loaded for a single field.

    ``max_age`` caps how many subsequent panel rows may carry the last print
    (``None`` keeps the last value with no age limit). The count is in rows of
    ``panel``, which for ``factor_zoo`` is the union of observation dates.
    """
    if max_age is not None and int(max_age) < 0:
        raise ValueError("max_age must be >= 0")

    if panel.empty:
        return pd.Series(dtype=float, name=field)

    hist = panel.loc[:as_of]
    if hist.empty:
        return pd.Series(dtype=float, name=field)

    if max_age is None:
        snapshot = hist.ffill().iloc[-1]
    else:
        snapshot = hist.ffill(limit=int(max_age)).iloc[-1]
    if isinstance(snapshot.index, pd.MultiIndex):
        try:
            out = snapshot.xs(field, level=-1)
        except KeyError:
            return pd.Series(dtype=float, name=field)
        out.name = field
        return out

    out = snapshot.copy()
    out.name = field
    return out
