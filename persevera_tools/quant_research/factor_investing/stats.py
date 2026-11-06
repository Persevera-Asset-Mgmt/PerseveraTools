"""Small inference helpers shared by the backtest summary and signal analysis."""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm


def newey_west_lags(n_obs: int) -> int:
    """Newey & West (1994) rule of thumb: ``floor(4 * (T / 100) ** (2/9))``."""
    if n_obs <= 1:
        return 0
    return int(math.floor(4.0 * (n_obs / 100.0) ** (2.0 / 9.0)))


def hac_mean_test(
    values: pd.Series,
    lags: Optional[int] = None,
) -> dict[str, float]:
    """
    Mean of a time series with a Newey-West (HAC) standard error.

    ``lags=None`` uses :func:`newey_west_lags`. Overlapping observations (a
    ``h``-period return sampled every period) need ``lags >= h - 1``.
    Returns ``mean``, ``se``, ``tstat``, ``pvalue``, ``n`` and ``lags``.
    """
    x = pd.Series(values, dtype=float).dropna()
    n = int(len(x))
    out = {
        "mean": float("nan"),
        "se": float("nan"),
        "tstat": float("nan"),
        "pvalue": float("nan"),
        "n": n,
        "lags": float("nan"),
    }
    if n == 0:
        return out
    out["mean"] = float(x.mean())
    if n < 3 or float(x.std()) == 0.0:
        return out
    use_lags = newey_west_lags(n) if lags is None else int(lags)
    use_lags = max(0, min(use_lags, n - 2))
    fit = sm.OLS(x.to_numpy(), np.ones((n, 1))).fit(
        cov_type="HAC", cov_kwds={"maxlags": use_lags}
    )
    out.update(
        {
            "se": float(fit.bse[0]),
            "tstat": float(fit.tvalues[0]),
            "pvalue": float(fit.pvalues[0]),
            "lags": use_lags,
        }
    )
    return out


def hac_regression(
    y: pd.Series,
    x: pd.DataFrame | pd.Series,
    lags: Optional[int] = None,
) -> dict[str, pd.Series | float | int]:
    """
    Time-series OLS of ``y`` on ``x`` plus a constant, with HAC errors.

    Returns ``params``, ``tstats`` and ``pvalues`` (Series indexed by
    ``const`` and the ``x`` columns), ``r2``, ``n`` and ``lags``.
    """
    frame = pd.concat(
        [pd.Series(y, dtype=float).rename("__y__"), pd.DataFrame(x).astype(float)],
        axis=1,
        join="inner",
    ).dropna()
    n = int(len(frame))
    cols = [c for c in frame.columns if c != "__y__"]
    empty = pd.Series(float("nan"), index=["const", *cols])
    if n <= len(cols) + 2:
        return {"params": empty, "tstats": empty, "pvalues": empty, "r2": float("nan"), "n": n, "lags": 0}
    use_lags = newey_west_lags(n) if lags is None else int(lags)
    exog = sm.add_constant(frame[cols], has_constant="add")
    fit = sm.OLS(frame["__y__"], exog).fit(cov_type="HAC", cov_kwds={"maxlags": use_lags})
    return {
        "params": fit.params,
        "tstats": fit.tvalues,
        "pvalues": fit.pvalues,
        "r2": float(fit.rsquared),
        "n": n,
        "lags": use_lags,
    }
