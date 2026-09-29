"""Cross-sectional factor scoring (screener recipe, without Streamlit)."""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

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


def snapshot_components(
    panel: pd.DataFrame,
    as_of: pd.Timestamp,
    components: Sequence[str],
) -> pd.DataFrame:
    """
    Build a codes × components DataFrame using last values on or before ``as_of``.

    ``panel`` columns are MultiIndex ``(ticker, descriptor)``.
    """
    if panel.empty:
        return pd.DataFrame(columns=list(components))

    hist = panel.loc[:as_of]
    if hist.empty:
        return pd.DataFrame(columns=list(components))

    snapshot = hist.ffill().iloc[-1]
    if not isinstance(snapshot.index, pd.MultiIndex):
        raise TypeError(
            "Expected MultiIndex columns (ticker, descriptor) on the factor panel"
        )

    frames: dict[str, pd.Series] = {}
    for comp in components:
        try:
            frames[comp] = snapshot.xs(comp, level=-1)
        except KeyError:
            continue
    if not frames:
        return pd.DataFrame(columns=list(components))
    return pd.DataFrame(frames)


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
