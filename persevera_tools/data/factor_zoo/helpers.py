"""Shared panel utilities for derived factor computations."""

from __future__ import annotations

import numpy as np
import pandas as pd


def daily_ffilled_panel(
    observation_panel: pd.DataFrame,
    *,
    ffill_limit: int,
    calendar_freq: str = "D",
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """
    Dense calendar grid merged with irregular observations.

    Observation rows take precedence over filler rows when timestamps coincide.
    Numeric columns are forward-filled after deduplication.

    Parameters
    ----------
    observation_panel
        Date index (DatetimeIndex); columns are tickers/units without ``position``.
    ffill_limit
        Max consecutive NaNs to propagate (pandas ``ffill(limit=…)``).

    Returns
    -------
    filled_panel
        Full daily (or ``calendar_freq``) panel with fills applied.
    original_dates
        Sorted observation dates — use to subset outputs onto native calendar.
    """
    if observation_panel.empty:
        return observation_panel.copy(), pd.DatetimeIndex([])

    obs_sorted = observation_panel.sort_index().copy()
    idx = pd.DatetimeIndex(obs_sorted.index)
    if not idx.is_monotonic_increasing:
        idx = idx.sort_values()
        obs_sorted = obs_sorted.loc[idx]

    coded = obs_sorted.assign(_pz_position_=0)
    filler_ix = pd.date_range(start=idx.min(), end=idx.max(), freq=calendar_freq)
    filler = pd.DataFrame(np.nan, index=filler_ix, columns=coded.columns, dtype=float)
    filler["_pz_position_"] = 1

    combined = pd.concat([coded, filler])
    combined = combined.reset_index(names="_pz_date_")
    combined = combined.sort_values(["_pz_date_", "_pz_position_"]).drop_duplicates(
        subset=["_pz_date_"], keep="first"
    )
    combined = combined.set_index("_pz_date_").sort_index()

    numeric_block = combined.drop(columns=["_pz_position_"]).apply(pd.to_numeric, errors="coerce")
    filled = numeric_block.ffill(limit=ffill_limit)
    return filled, pd.DatetimeIndex(idx)

