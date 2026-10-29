"""Portfolio construction from cross-sectional factor scores."""

from __future__ import annotations

from typing import Literal, Optional

import numpy as np
import pandas as pd

Construction = Literal["long_only", "long_short"]
SelectionMode = Literal["quantile", "top_n"]


def _tail_count(
    n: int,
    *,
    selection_mode: SelectionMode = "quantile",
    quantile: float = 0.2,
    top_n: Optional[int] = None,
) -> int:
    """Number of names in each tail; at least 1 when n >= 1."""
    if n <= 0:
        return 0
    if selection_mode == "top_n":
        if top_n is None or top_n < 1:
            raise ValueError("top_n must be >= 1 when selection_mode='top_n'")
        return min(int(top_n), n)
    k = int(np.floor(n * quantile))
    return max(k, 1)


def scores_to_weights(
    scores: pd.Series,
    construction: Construction = "long_only",
    *,
    selection_mode: SelectionMode = "quantile",
    quantile: float = 0.2,
    top_n: Optional[int] = None,
) -> pd.Series:
    """
    Map a cross-sectional score Series to portfolio weights.

    Selection:
      - ``quantile``: take fraction ``quantile`` of ranked names per tail
      - ``top_n``: take a fixed ``top_n`` names per tail

    Construction:
      - ``long_only``: equal-weight the top tail
      - ``long_short``: equal-weight long top tail and short bottom tail
        (dollar-neutral: long gross = short gross = 1)
    """
    clean = scores.dropna().sort_values(ascending=False)
    if clean.empty:
        return pd.Series(dtype=float)

    n = len(clean)
    k = _tail_count(
        n,
        selection_mode=selection_mode,
        quantile=quantile,
        top_n=top_n,
    )
    if k <= 0:
        return pd.Series(dtype=float)

    weights = pd.Series(0.0, index=clean.index, dtype=float)

    if construction == "long_only":
        longs = clean.index[:k]
        weights.loc[longs] = 1.0 / len(longs)
        return weights[weights != 0.0]

    if construction == "long_short":
        longs = clean.index[:k]
        shorts = clean.index[-k:]
        overlap = set(longs).intersection(shorts)
        if overlap:
            mid = n // 2
            if mid == 0:
                return pd.Series(dtype=float)
            longs = clean.index[:mid]
            shorts = clean.index[mid:]
            if len(longs) == 0 or len(shorts) == 0:
                return pd.Series(dtype=float)
        weights.loc[longs] = 1.0 / len(longs)
        weights.loc[shorts] = -1.0 / len(shorts)
        return weights[weights != 0.0]

    raise ValueError(f"Unknown construction: {construction!r}")


def beta_neutralize(
    weights: pd.Series,
    betas: pd.Series,
) -> tuple[pd.Series, dict[str, float]]:
    """
    Resize the legs of a long/short book so its ex-ante beta is zero.

    Within each leg the relative weights are kept; the leg grosses become
    ``g_L = G * b_S / (b_L + b_S)`` and ``g_S = G * b_L / (b_L + b_S)``, where
    ``G`` is the input gross and ``b_L`` / ``b_S`` are the weighted leg betas.
    Missing betas take the median of ``betas``. When either leg beta is not
    positive the book is returned unchanged.

    Returns the new weights and a dict with ``beta_long``, ``beta_short``,
    ``gross_long``, ``gross_short`` and ``net_beta`` (after resizing).
    """
    longs = weights[weights > 0]
    shorts = weights[weights < 0]
    info = {
        "beta_long": float("nan"),
        "beta_short": float("nan"),
        "gross_long": float(longs.sum()),
        "gross_short": float(-shorts.sum()),
        "net_beta": float("nan"),
    }
    if longs.empty or shorts.empty:
        return weights, info

    b = betas.reindex(weights.index).astype(float)
    fill = float(betas.median()) if betas.notna().any() else 1.0
    b = b.fillna(fill)

    beta_long = float((longs * b[longs.index]).sum() / longs.sum())
    beta_short = float((shorts * b[shorts.index]).sum() / shorts.sum())
    info["beta_long"] = beta_long
    info["beta_short"] = beta_short
    if beta_long <= 0 or beta_short <= 0:
        info["net_beta"] = float((weights * b).sum())
        return weights, info

    gross = info["gross_long"] + info["gross_short"]
    gross_long = gross * beta_short / (beta_long + beta_short)
    gross_short = gross * beta_long / (beta_long + beta_short)
    out = weights.copy()
    out.loc[longs.index] = longs / longs.sum() * gross_long
    out.loc[shorts.index] = shorts / (-shorts.sum()) * gross_short
    info["gross_long"] = gross_long
    info["gross_short"] = gross_short
    info["net_beta"] = float((out * b).sum())
    return out, info
