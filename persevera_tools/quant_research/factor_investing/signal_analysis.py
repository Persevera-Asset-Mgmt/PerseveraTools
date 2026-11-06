"""
Signal diagnostics independent of portfolio construction.

Typical usage::

    from persevera_tools.quant_research.factor_investing import (
        BacktestConfig, run_backtest, analyze_signal,
    )

    result = run_backtest(BacktestConfig(...))
    report = analyze_signal(result)          # loads default FM controls
    report.summary                           # headline numbers
    report.ic_decay, report.quantile_summary, report.fama_macbeth

Everything is measured on the names scored at each rebalance (the investable
universe after the ADTV / price / coverage filters), with forward returns
from the rebalance close to the close of a later rebalance date: the same
timing as the backtest, gross of costs.

Methods:
  - Information coefficient (Grinold & Kahn): Pearson and Spearman (rank)
    correlation between the score and the next-period return; mean, ICIR,
    hit rate and Newey-West t-stat. IC decay over several horizons.
  - Quantile portfolios: equal-weighted forward return of each score
    quantile, top-minus-bottom spread and the Patton & Timmermann (2010)
    monotonic relation test (stationary bootstrap).
  - Fama & MacBeth (1973) cross-sectional regressions, univariate and with
    controls (size, beta, value, momentum by default); Newey-West t-stats on
    the time series of slopes.
  - Per-component rank IC and the average cross-sectional correlation
    between components.
  - Score rank autocorrelation between consecutive rebalances (signal
    stability, a proxy for turnover).

Forward returns use the last price on or before each date. A name that stops
trading inside the window is measured to its last print (no delisting
return).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .data import load_factor_panel
from .scoring import component_zscores, snapshot_series
from .stats import hac_mean_test, newey_west_lags

MIN_CROSS_SECTION = 5

# Fama-MacBeth controls: label -> (factor_zoo field, transform).
DEFAULT_CONTROLS: dict[str, tuple[str, str]] = {
    "size": ("market_cap", "log"),
    "beta": ("beta", "level"),
    "value": ("book_yield_ltm", "level"),
    "momentum": ("momentum_12m1", "level"),
}


@dataclass
class SignalReport:
    """Outputs of :func:`analyze_signal`. ``summary`` holds the headline numbers."""

    summary: dict[str, Any] = field(default_factory=dict)
    ic: pd.DataFrame = field(default_factory=pd.DataFrame)
    ic_decay: pd.DataFrame = field(default_factory=pd.DataFrame)
    quantile_returns: pd.DataFrame = field(default_factory=pd.DataFrame)
    quantile_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    monotonicity: dict[str, Any] = field(default_factory=dict)
    fama_macbeth: pd.DataFrame = field(default_factory=pd.DataFrame)
    fama_macbeth_slopes: dict[str, pd.DataFrame] = field(default_factory=dict)
    component_ic: pd.DataFrame = field(default_factory=pd.DataFrame)
    component_correlation: pd.DataFrame = field(default_factory=pd.DataFrame)
    score_autocorrelation: pd.Series = field(default_factory=pd.Series)

    def __repr__(self) -> str:
        s = self.summary
        return (
            f"SignalReport(n_periods={s.get('n_periods')}, "
            f"rank_ic={s.get('rank_ic_mean')!r}, rank_ic_t={s.get('rank_ic_tstat')!r}, "
            f"spread_t={s.get('spread_tstat')!r}, fm_t={s.get('fm_tstat')!r})"
        )


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #


def periods_per_year(dates: pd.DatetimeIndex) -> float:
    """Rebalances per year from the median spacing of ``dates``."""
    if len(dates) < 2:
        return float("nan")
    gap = pd.Series(pd.DatetimeIndex(dates)).diff().dt.days.median()
    return float(365.25 / gap) if gap and gap > 0 else float("nan")


def forward_returns(
    prices: pd.DataFrame,
    dates: pd.DatetimeIndex,
    horizon: int = 1,
) -> pd.DataFrame:
    """
    ``dates x codes`` returns from each date to the date ``horizon`` steps
    later in ``dates`` (NaN for the last ``horizon`` dates).

    Prices are the last print on or before each date.
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    dates = pd.DatetimeIndex(dates)
    px = prices.sort_index().ffill()
    at = px.reindex(px.index.union(dates)).ffill().reindex(dates)
    # Price at the start must be a print still alive on that date.
    alive = prices.sort_index().reindex(prices.index.union(dates)).ffill(limit=5).reindex(dates)
    start = at.where(alive.notna())
    end = at.shift(-horizon)
    return end / start - 1.0


def _cross_corr(a: pd.Series, b: pd.Series, method: str) -> tuple[float, int]:
    pair = pd.concat([a, b], axis=1, join="inner").dropna()
    n = len(pair)
    if n < MIN_CROSS_SECTION or pair.iloc[:, 0].nunique() < 2 or pair.iloc[:, 1].nunique() < 2:
        return float("nan"), n
    return float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method=method)), n


def information_coefficient(scores: pd.DataFrame, fwd: pd.DataFrame) -> pd.DataFrame:
    """Per-date Pearson ``ic``, Spearman ``rank_ic`` and cross-section size ``n``."""
    rows = []
    for dt in scores.index:
        if dt not in fwd.index:
            continue
        s = scores.loc[dt].dropna()
        r = fwd.loc[dt]
        ic, n = _cross_corr(s, r, "pearson")
        ric, _ = _cross_corr(s, r, "spearman")
        rows.append({"date": dt, "ic": ic, "rank_ic": ric, "n": n})
    if not rows:
        return pd.DataFrame(columns=["ic", "rank_ic", "n"])
    return pd.DataFrame(rows).set_index("date")


def summarize_ic(
    ic: pd.Series,
    *,
    ppy: float,
    horizon: int = 1,
) -> dict[str, float]:
    """Mean, std, ICIR (per period and annualized), hit rate and NW t-stat."""
    x = ic.dropna()
    lags = max(newey_west_lags(len(x)), horizon - 1)
    test = hac_mean_test(x, lags=lags)
    std = float(x.std()) if len(x) > 1 else float("nan")
    icir = test["mean"] / std if std and std > 0 else float("nan")
    return {
        "mean": test["mean"],
        "std": std,
        "icir": icir,
        "icir_annualized": icir * np.sqrt(ppy / horizon) if np.isfinite(ppy) else float("nan"),
        "hit_rate": float((x > 0).mean()) if len(x) else float("nan"),
        "tstat": test["tstat"],
        "pvalue": test["pvalue"],
        "n": test["n"],
        "lags": lags,
    }


def quantile_returns(
    scores: pd.DataFrame,
    fwd: pd.DataFrame,
    n_quantiles: int = 5,
) -> pd.DataFrame:
    """
    ``dates x Q1..Qk`` equal-weighted forward return per score quantile.

    ``Qk`` holds the highest scores. Quantiles come from score ranks, so ties
    and uneven counts split as evenly as possible.
    """
    if n_quantiles < 2:
        raise ValueError("n_quantiles must be >= 2")
    labels = [f"Q{i}" for i in range(1, n_quantiles + 1)]
    rows = {}
    for dt in scores.index:
        if dt not in fwd.index:
            continue
        pair = pd.concat([scores.loc[dt].rename("s"), fwd.loc[dt].rename("r")], axis=1).dropna()
        if len(pair) < n_quantiles:
            continue
        bucket = pd.qcut(pair["s"].rank(method="first"), n_quantiles, labels=labels)
        rows[dt] = pair.groupby(bucket, observed=False)["r"].mean().reindex(labels)
    if not rows:
        return pd.DataFrame(columns=labels)
    out = pd.DataFrame(rows).T
    out.index = pd.DatetimeIndex(out.index)
    return out[labels]


def summarize_quantiles(qret: pd.DataFrame, *, ppy: float) -> pd.DataFrame:
    """Mean per period, annualized (x periods/year), NW t-stat, plus the top-minus-bottom spread."""
    if qret.empty:
        return pd.DataFrame(columns=["mean", "annualized", "tstat", "pvalue", "hit_rate", "n"])
    cols = dict(qret.items())
    cols["spread"] = qret.iloc[:, -1] - qret.iloc[:, 0]
    rows = {}
    for name, series in cols.items():
        t = hac_mean_test(series)
        x = series.dropna()
        rows[name] = {
            "mean": t["mean"],
            "annualized": t["mean"] * ppy if np.isfinite(ppy) else float("nan"),
            "tstat": t["tstat"],
            "pvalue": t["pvalue"],
            "hit_rate": float((x > 0).mean()) if len(x) else float("nan"),
            "n": t["n"],
        }
    return pd.DataFrame(rows).T


def _stationary_bootstrap_indices(
    n: int, n_boot: int, mean_block: float, rng: np.random.Generator
) -> np.ndarray:
    """Politis & Romano (1994) stationary bootstrap row indices, shape ``(n_boot, n)``."""
    p = 1.0 / max(mean_block, 1.0)
    idx = np.empty((n_boot, n), dtype=int)
    idx[:, 0] = rng.integers(0, n, n_boot)
    jumps = rng.random((n_boot, n)) < p
    fresh = rng.integers(0, n, (n_boot, n))
    for t in range(1, n):
        idx[:, t] = np.where(jumps[:, t], fresh[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def monotonicity_test(
    qret: pd.DataFrame,
    *,
    n_boot: int = 2000,
    mean_block: Optional[float] = None,
    seed: int = 0,
) -> dict[str, Any]:
    """
    Patton & Timmermann (2010) monotonic relation test, increasing direction.

    ``H0``: some adjacent quantile difference has mean <= 0. ``H1``: returns
    rise strictly from ``Q1`` to ``Qk``. Statistic ``J = min_j mean(Q_{j+1} - Q_j)``;
    the p-value is the share of re-centred stationary-bootstrap statistics at
    or above ``J`` (block length ``T ** (1/3)`` by default). Also reports the
    Spearman correlation between quantile rank and mean return.
    """
    q = qret.dropna()
    out: dict[str, Any] = {
        "statistic": float("nan"),
        "pvalue": float("nan"),
        "n_increasing_steps": 0,
        "n_steps": max(qret.shape[1] - 1, 0),
        "rank_correlation": float("nan"),
        "n": int(len(q)),
    }
    if len(q) < 10 or q.shape[1] < 2:
        return out
    diffs = np.diff(q.to_numpy(), axis=1)
    mean_d = diffs.mean(axis=0)
    stat = float(mean_d.min())
    block = mean_block if mean_block is not None else max(1.0, len(q) ** (1.0 / 3.0))
    rng = np.random.default_rng(seed)
    idx = _stationary_bootstrap_indices(len(q), n_boot, block, rng)
    boot = diffs[idx].mean(axis=1) - mean_d  # (n_boot, k-1)
    out.update(
        {
            "statistic": stat,
            "pvalue": float((boot.min(axis=1) >= stat).mean()),
            "n_increasing_steps": int((mean_d > 0).sum()),
            "rank_correlation": float(
                pd.Series(q.mean().to_numpy()).corr(
                    pd.Series(np.arange(q.shape[1], dtype=float)), method="spearman"
                )
            ),
            "mean_block": float(block),
        }
    )
    return out


def _standardize(frame: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional winsorized z-scores (same recipe as the factor scores)."""
    return component_zscores(frame)


def fama_macbeth(
    scores: pd.DataFrame,
    fwd: pd.DataFrame,
    controls: Optional[Mapping[str, pd.DataFrame]] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Fama & MacBeth (1973): per-date OLS of the forward return on the
    standardized score (and standardized controls), then the time-series mean
    of each slope with a Newey-West t-stat.

    The ``signal`` slope is the period return per one cross-sectional
    standard deviation of the score. Returns ``(summary, slopes)``; summary
    rows are ``const``, ``signal`` and each control, plus ``avg_r2`` and
    ``avg_n`` columns.
    """
    controls = dict(controls or {})
    names = ["signal", *controls.keys()]
    slopes, r2s, ns = {}, {}, {}
    for dt in scores.index:
        if dt not in fwd.index:
            continue
        cols = {"signal": scores.loc[dt]}
        for name, frame in controls.items():
            cols[name] = frame.loc[dt] if dt in frame.index else pd.Series(dtype=float)
        x = pd.DataFrame(cols).reindex(columns=names)
        data = pd.concat([fwd.loc[dt].rename("__r__"), x], axis=1, join="inner")
        data = data.dropna()
        if len(data) < max(MIN_CROSS_SECTION, len(names) + 3):
            continue
        xz = _standardize(data[names])
        if xz.isna().any().any():
            continue
        design = np.column_stack([np.ones(len(xz)), xz.to_numpy()])
        y = data["__r__"].to_numpy()
        beta, *_ = np.linalg.lstsq(design, y, rcond=None)
        resid = y - design @ beta
        tss = float(((y - y.mean()) ** 2).sum())
        slopes[dt] = pd.Series(beta, index=["const", *names])
        r2s[dt] = 1.0 - float((resid ** 2).sum()) / tss if tss > 0 else float("nan")
        ns[dt] = len(xz)
    if not slopes:
        return pd.DataFrame(), pd.DataFrame()
    slope_df = pd.DataFrame(slopes).T
    slope_df.index = pd.DatetimeIndex(slope_df.index)
    rows = {}
    for col in slope_df.columns:
        t = hac_mean_test(slope_df[col])
        rows[col] = {"coef": t["mean"], "tstat": t["tstat"], "pvalue": t["pvalue"], "n_periods": t["n"]}
    summary = pd.DataFrame(rows).T
    summary["avg_r2"] = float(np.nanmean(list(r2s.values())))
    summary["avg_n"] = float(np.mean(list(ns.values())))
    return summary, slope_df


def component_ic(
    component_scores: pd.DataFrame,
    fwd: pd.DataFrame,
    *,
    ppy: float,
) -> pd.DataFrame:
    """Rank IC summary per sign-aligned component (``(date, code)`` x components input)."""
    if component_scores is None or component_scores.empty:
        return pd.DataFrame()
    rows = {}
    for comp in component_scores.columns:
        wide = component_scores[comp].unstack("code")
        ic = information_coefficient(wide, fwd)
        rows[comp] = summarize_ic(ic["rank_ic"], ppy=ppy) if not ic.empty else {}
        rows[comp]["coverage"] = float(component_scores[comp].notna().mean())
    return pd.DataFrame(rows).T


def component_correlation(component_scores: pd.DataFrame) -> pd.DataFrame:
    """Time average of the per-date Spearman correlation matrix between components."""
    if component_scores is None or component_scores.empty or component_scores.shape[1] < 2:
        return pd.DataFrame()
    mats = []
    for _, frame in component_scores.groupby(level="date"):
        if len(frame) >= MIN_CROSS_SECTION:
            mats.append(frame.corr(method="spearman", min_periods=MIN_CROSS_SECTION))
    if not mats:
        return pd.DataFrame()
    stacked = np.stack([m.to_numpy() for m in mats])
    return pd.DataFrame(
        np.nanmean(stacked, axis=0), index=mats[0].index, columns=mats[0].columns
    )


def score_autocorrelation(scores: pd.DataFrame) -> pd.Series:
    """Spearman correlation of scores between consecutive rebalances (common names)."""
    vals = {}
    for prev, curr in zip(scores.index[:-1], scores.index[1:]):
        rho, _ = _cross_corr(scores.loc[prev], scores.loc[curr], "spearman")
        vals[curr] = rho
    return pd.Series(vals, dtype=float, name="score_autocorrelation")


def load_controls(
    codes: Sequence[str],
    dates: pd.DatetimeIndex,
    controls: Mapping[str, tuple[str, str]] = DEFAULT_CONTROLS,
    *,
    exclude_fields: Sequence[str] = (),
) -> dict[str, pd.DataFrame]:
    """
    ``{label: dates x codes}`` control characteristics from ``factor_zoo``.

    Point-in-time: last value on or before each date. ``transform`` is
    ``"level"`` or ``"log"`` (non-positive values become NaN). Controls whose
    field is in ``exclude_fields`` (e.g. the signal's own components) are
    skipped, as are fields with no data.
    """
    wanted = {k: v for k, v in controls.items() if v[0] not in set(exclude_fields)}
    if not wanted or len(dates) == 0:
        return {}
    fields = list(dict.fromkeys(f for f, _ in wanted.values()))
    panel = load_factor_panel(
        fields,
        pd.DatetimeIndex(dates).min() - pd.Timedelta(days=400),
        pd.DatetimeIndex(dates).max(),
        codes=list(codes),
    )
    out: dict[str, pd.DataFrame] = {}
    for label, (fld, transform) in wanted.items():
        rows = {dt: snapshot_series(panel, dt, fld) for dt in dates}
        frame = pd.DataFrame(rows).T
        if frame.empty or frame.notna().sum().sum() == 0:
            continue
        frame = frame.astype(float)
        if transform == "log":
            frame = np.log(frame.where(frame > 0))
        out[label] = frame
    return out


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def analyze_signal(
    result: Any,
    *,
    horizons: Sequence[int] = (1, 3, 6, 12),
    n_quantiles: int = 5,
    controls: Optional[Mapping[str, pd.DataFrame]] | str = "default",
    n_boot: int = 2000,
    seed: int = 0,
) -> SignalReport:
    """
    Signal diagnostics for a :class:`BacktestResult`.

    ``horizons`` are in rebalance periods (with monthly rebalances, months).
    ``controls``: ``"default"`` loads :data:`DEFAULT_CONTROLS` from
    ``factor_zoo`` (skipping fields that are signal components), ``None``
    runs only the univariate Fama-MacBeth, or pass ``{label: dates x codes}``.
    """
    scores: pd.DataFrame = result.scores
    prices: Optional[pd.DataFrame] = getattr(result, "prices", None)
    if prices is None or prices.empty:
        raise ValueError("result.prices is empty; rerun run_backtest with this version")
    dates = pd.DatetimeIndex(scores.index)
    ppy = periods_per_year(dates)
    horizons = sorted({int(h) for h in horizons if int(h) >= 1}) or [1]

    fwd = {h: forward_returns(prices, dates, h) for h in set(horizons) | {1}}

    ic = information_coefficient(scores, fwd[1])
    decay_rows = {}
    for h in horizons:
        ic_h = ic if h == 1 else information_coefficient(scores, fwd[h])
        decay_rows[h] = summarize_ic(ic_h["rank_ic"], ppy=ppy, horizon=h) if not ic_h.empty else {}
        decay_rows[h]["ic_mean"] = float(ic_h["ic"].mean()) if not ic_h.empty else float("nan")
    ic_decay = pd.DataFrame(decay_rows).T
    ic_decay.index.name = "horizon"

    qret = quantile_returns(scores, fwd[1], n_quantiles)
    qsum = summarize_quantiles(qret, ppy=ppy)
    mono = monotonicity_test(qret, n_boot=n_boot, seed=seed)

    if isinstance(controls, str):
        if controls != "default":
            raise ValueError("controls must be 'default', None or a mapping")
        codes = sorted(scores.columns.astype(str))
        controls_map = load_controls(
            codes, dates, exclude_fields=tuple(getattr(result, "components", ()) or ())
        )
    else:
        controls_map = dict(controls or {})

    fm_uni, slopes_uni = fama_macbeth(scores, fwd[1])
    frames = {"univariate": fm_uni}
    slopes = {"univariate": slopes_uni}
    if controls_map:
        fm_ctl, slopes_ctl = fama_macbeth(scores, fwd[1], controls_map)
        frames["controls"] = fm_ctl
        slopes["controls"] = slopes_ctl
    frames = {k: v for k, v in frames.items() if not v.empty}
    fm = pd.concat(frames, names=["spec", "variable"]) if frames else pd.DataFrame()

    comp_scores = getattr(result, "component_scores", None)
    comp_ic = component_ic(comp_scores, fwd[1], ppy=ppy) if comp_scores is not None else pd.DataFrame()
    comp_corr = component_correlation(comp_scores) if comp_scores is not None else pd.DataFrame()
    autocorr = score_autocorrelation(scores)

    ic1 = decay_rows.get(1, {}) if 1 in decay_rows else summarize_ic(ic["rank_ic"], ppy=ppy)

    def _fm(spec: str, col: str) -> float:
        try:
            return float(fm.loc[(spec, "signal"), col])
        except (KeyError, TypeError):
            return float("nan")

    spread = qsum.loc["spread"] if "spread" in qsum.index else pd.Series(dtype=float)
    summary = {
        "n_periods": int(ic["rank_ic"].notna().sum()) if not ic.empty else 0,
        "periods_per_year": ppy,
        "avg_cross_section": float(ic["n"].mean()) if not ic.empty else float("nan"),
        "ic_mean": float(ic["ic"].mean()) if not ic.empty else float("nan"),
        "rank_ic_mean": ic1.get("mean", float("nan")),
        "rank_ic_ir": ic1.get("icir", float("nan")),
        "rank_ic_ir_annualized": ic1.get("icir_annualized", float("nan")),
        "rank_ic_tstat": ic1.get("tstat", float("nan")),
        "rank_ic_hit_rate": ic1.get("hit_rate", float("nan")),
        "spread_mean": float(spread.get("mean", float("nan"))),
        "spread_annualized": float(spread.get("annualized", float("nan"))),
        "spread_tstat": float(spread.get("tstat", float("nan"))),
        "monotonic_pvalue": mono["pvalue"],
        "monotonic_steps": f"{mono['n_increasing_steps']}/{mono['n_steps']}",
        "fm_coef": _fm("univariate", "coef"),
        "fm_tstat": _fm("univariate", "tstat"),
        "fm_coef_controls": _fm("controls", "coef"),
        "fm_tstat_controls": _fm("controls", "tstat"),
        "fm_controls": list(controls_map.keys()),
        "score_autocorrelation": float(autocorr.mean()) if len(autocorr) else float("nan"),
    }

    return SignalReport(
        summary=summary,
        ic=ic,
        ic_decay=ic_decay,
        quantile_returns=qret,
        quantile_summary=qsum,
        monotonicity=mono,
        fama_macbeth=fm,
        fama_macbeth_slopes=slopes,
        component_ic=comp_ic,
        component_correlation=comp_corr,
        score_autocorrelation=autocorr,
    )
