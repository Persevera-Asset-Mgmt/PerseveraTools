"""Pure transforms: factor_zoo long-format input -> derived long-format output."""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from .helpers import daily_ffilled_panel


def _stack_wide(panel: pd.DataFrame) -> pd.Series:
    # pandas>=2.1: future_stack without dropna; 1.x: dropna=False only
    try:
        return panel.stack(future_stack=True)
    except TypeError:
        return panel.stack(dropna=False)


def _panel_to_long(
    panel: pd.DataFrame,
    observed_dates: pd.DatetimeIndex,
    field_label: str,
) -> pd.DataFrame:
    r = panel.reindex(observed_dates)
    stacked = _stack_wide(r)
    stacked.name = "value"
    out = stacked.reset_index()
    out.columns = ["date", "code", "value"]
    out["field"] = field_label
    return out[["code", "date", "field", "value"]]


def compute_price_momentum(data: pd.DataFrame) -> pd.DataFrame:
    temp_pivot = data.pivot(index="date", columns="code", values="value")
    # Prices are carried for at most 21 days; pct_change must not fill again
    # (its default fill_method='pad' froze a delisted code's last price and
    # emitted momentum = 0 on every later date).
    temp, orig_idx = daily_ffilled_panel(temp_pivot, ffill_limit=21)
    chunks: List[pd.DataFrame] = []
    for horizon in [1, 3, 6, 9, 12]:
        shifted = temp.pct_change(periods=1, freq=f"{30 * horizon}D", fill_method=None).multiply(100)
        chunks.append(_panel_to_long(shifted, orig_idx, f"momentum_{horizon}m"))

    for horizon in [3, 6, 9, 12]:
        shifted = (
            temp.pct_change(periods=1, freq=f"{30 * (horizon - 1)}D", fill_method=None)
            .shift(30)
            .multiply(100)
        )
        chunks.append(_panel_to_long(shifted, orig_idx, f"momentum_{horizon}m1"))

    for days in [7, 14]:
        shifted = temp.pct_change(periods=1, freq=f"{days}D", fill_method=None).multiply(100)
        chunks.append(_panel_to_long(shifted, orig_idx, f"momentum_{days}d"))

    return pd.concat(chunks, ignore_index=True)


def compute_price_range(data: pd.DataFrame) -> pd.DataFrame:
    temp_pivot = data.pivot(index="date", columns="code", values="value")
    temp, orig_idx = daily_ffilled_panel(temp_pivot, ffill_limit=21)
    lo = temp.rolling(window=360).min()
    hi = temp.rolling(window=360).max()
    denom = hi - lo
    r_arr = np.where(denom.abs() > 1e-12, (temp - lo) / denom * 100.0, np.nan)
    r = pd.DataFrame(r_arr, index=temp.index, columns=temp.columns)
    return _panel_to_long(r, orig_idx, "price_range")


def compute_short_selling(data: pd.DataFrame) -> pd.DataFrame:
    temp = data.pivot(index=["code", "date"], columns="field", values="value")
    r = temp.eval(
        """
        short_interest_pct = short_interest / shares_outstanding / 1e4
        days_to_cover = short_interest / median_volume_traded_21d
        """
    )
    out = r[["short_interest_pct", "days_to_cover"]]
    stacked = _stack_wide(out)
    stacked.name = "value"
    long_df = stacked.reset_index()
    long_df.columns = ["code", "date", "field", "value"]
    return long_df


# ── Liquidity ─────────────────────────────────────────────────────────────────

_B3_CODE_RE = r"^[A-Z0-9]{4}\d{1,2}B?$"
_LIQUIDITY_WINDOWS = (7, 14, 21, 63, 252)
_LIQUIDITY_DELTA_WINDOWS = (7, 14, 21)


def _exchange_calendar(present: pd.DataFrame, *, min_share: float) -> pd.DatetimeIndex:
    """Sessions where at least ``min_share`` of listed codes have a print.

    ``present`` is a date × code boolean panel for one exchange. A code counts
    as listed between its first and last print, so holidays (no prints) drop
    out while thin early years (few listed codes) still qualify.
    """
    first = present.idxmax()
    last = present[::-1].idxmax()
    d = present.index.to_numpy()[:, None]
    listed = (d >= first.to_numpy()[None, :]) & (d <= last.to_numpy()[None, :])
    share = present.sum(axis=1) / np.maximum(listed.sum(axis=1), 1)
    return present.index[share.to_numpy() >= min_share]


def _listed_mask(
    present: pd.DataFrame, calendar: pd.DatetimeIndex, *, still_listed_days: int
) -> pd.DataFrame:
    """Calendar × code mask from first print to delisting.

    A code whose last print is within ``still_listed_days`` of the calendar
    end is treated as still listed, so recent no-trade sessions count.
    """
    pres = present.reindex(calendar, fill_value=False)
    has = pres.any()
    first = pres.idxmax().where(has)
    last = pres[::-1].idxmax().where(has)
    end = calendar.max()
    last = last.where(last < end - pd.Timedelta(days=still_listed_days), end)
    d = calendar.to_numpy()[:, None]
    mask = (d >= pd.to_datetime(first).to_numpy()[None, :]) & (
        d <= pd.to_datetime(last).to_numpy()[None, :]
    )
    return pd.DataFrame(mask, index=calendar, columns=present.columns)


def compute_liquidity(
    data: pd.DataFrame,
    *,
    min_session_share: float = 0.5,
    still_listed_days: int = 30,
    min_coverage: float = 0.8,
) -> pd.DataFrame:
    """Rolling median volumes on each exchange's trading calendar.

    - Windows count sessions of the code's own exchange (B3 tickers vs US),
      not rows of the mixed BZ+US date union.
    - A session without a print while the code is listed counts as zero
      volume, so illiquid names are not flattered.
    - A window needs ``min_coverage`` of its sessions inside the listing
      period (e.g. 202 of 252), instead of a single observation.
    """
    vals = data.dropna(subset=["value"])
    present_all = (
        vals.assign(_one=1)
        .pivot_table(index="date", columns="code", values="_one", aggfunc="max")
        .notna()
    )
    exchange = pd.Series(
        np.where(present_all.columns.str.match(_B3_CODE_RE), "BZ", "US"),
        index=present_all.columns,
    )
    field_names = sorted(vals["field"].unique().tolist())

    chunks: List[pd.DataFrame] = []
    for _, codes in exchange.groupby(exchange):
        present = present_all[codes.index]
        present = present[present.any(axis=1)]
        calendar = _exchange_calendar(present, min_share=min_session_share)
        listed = _listed_mask(present, calendar, still_listed_days=still_listed_days)

        medians: dict[tuple[str, int], pd.DataFrame] = {}
        for fld in field_names:
            panel = (
                vals[vals["field"] == fld]
                .pivot_table(index="date", columns="code", values="value", aggfunc="last")
                .reindex(index=calendar, columns=codes.index)
                .fillna(0.0)
                .where(listed)
            )
            prefix = (
                "median_volume_traded"
                if fld == "num_shares_traded"
                else "median_dollar_volume_traded"
            )
            for window in _LIQUIDITY_WINDOWS:
                rolled = panel.rolling(
                    window=window, min_periods=int(np.ceil(min_coverage * window))
                ).median().where(listed)
                medians[(prefix, window)] = rolled
                chunks.append(_panel_to_long(rolled, calendar, f"{prefix}_{window}d"))

        for window in _LIQUIDITY_DELTA_WINDOWS:
            for prefix, out_prefix in (
                ("median_dollar_volume_traded", "delta_dollar_volume"),
                ("median_volume_traded", "delta_volume"),
            ):
                if (prefix, window) not in medians:
                    continue
                ratio = medians[(prefix, window)] / medians[(prefix, 63)]
                chunks.append(
                    _panel_to_long(ratio, calendar, f"{out_prefix}_{window}d_63d")
                )

    if not chunks:
        return pd.DataFrame(columns=["code", "date", "field", "value"])
    out = pd.concat(chunks, ignore_index=True)
    return out[np.isfinite(pd.to_numeric(out["value"], errors="coerce"))]


# Level fields (currency amounts) grow in percent; ratio fields (margins,
# returns) change in percentage points.
_PCT_GROWTH_FIELDS = frozenset({"net_revenues_ltm"})


def compute_ratios_growth(data: pd.DataFrame) -> pd.DataFrame:
    temp_pivot = data.pivot(index=["field", "date"], columns="code", values="value")
    uniq_fields = sorted(data["field"].dropna().unique().tolist())

    chunks: List[pd.DataFrame] = []
    for fld in uniq_fields:
        temp_field = temp_pivot.loc[fld]
        densified, obs_idx = daily_ffilled_panel(temp_field, ffill_limit=360)
        if fld in _PCT_GROWTH_FIELDS:
            base = densified.shift(360)
            shifted = (
                (densified - base) / base.abs().replace(0, np.nan) * 100
            ).reindex(obs_idx)
        else:
            shifted = densified.diff(360).reindex(obs_idx)
        stacked = _stack_wide(shifted)
        stacked.name = "value"
        blk = stacked.reset_index()
        blk.columns = ["date", "code", "value"]
        blk["field"] = fld
        chunks.append(blk[["code", "date", "field", "value"]])

    growth_long = pd.concat(chunks, ignore_index=True)

    enriched = pd.merge(
        data, growth_long, on=["code", "date", "field"], how="left"
    ).dropna()
    enriched = enriched.drop(columns=["value_x"])
    enriched = enriched.rename(columns={"value_y": "value"})
    enriched["field"] = enriched["field"].apply(lambda x: f"{x}_growth_1y")
    return enriched[["code", "date", "field", "value"]]


def compute_value(data: pd.DataFrame) -> pd.DataFrame:
    temp = data.pivot(index=["code", "date"], columns="field", values="value")
    carry = ["ebitda_ltm", "ebit_ltm", "total_equity"]
    available = [c for c in carry if c in temp.columns]
    if available:
        temp[available] = temp.groupby(level=0)[available].transform(
            lambda df: df.ffill(limit=252)
        )
    # Per-share yields divide by the split-only adjusted price: Bloomberg
    # per-share fields are split-adjusted but not dividend-adjusted, while
    # price_close is total-return adjusted. Codes without the split-adjusted
    # price get NaN rather than a biased yield.
    if "price_close_split_adj" not in temp.columns:
        temp["price_close_split_adj"] = np.nan
    r = temp.eval(
        """
        earnings_yield_fwd = earnings_per_share_fwd / price_close_split_adj
        book_yield_fwd = book_value_per_share_fwd / price_close_split_adj
        book_yield_ltm = total_equity / market_cap
        fcf_yield_fwd = free_cash_flow_fwd / ev
        ebit_yield_fwd = ebit_fwd / ev
        ebitda_yield_fwd = ebitda_fwd / ev
        ebitda_yield_ltm = ebitda_ltm / ev
        ebit_yield_ltm = ebit_ltm / ev
        """
    ).multiply(100)
    keep = [
        "earnings_yield_fwd",
        "book_yield_fwd",
        "book_yield_ltm",
        "fcf_yield_fwd",
        "ebit_yield_fwd",
        "ebitda_yield_fwd",
        "ebitda_yield_ltm",
        "ebit_yield_ltm",
    ]
    out = r[keep]
    stacked = _stack_wide(out)
    stacked.name = "value"
    long_df = stacked.reset_index()
    long_df.columns = ["code", "date", "field", "value"]
    return long_df


# ── Quality variability helpers ───────────────────────────────────────────────

def _trend_se(arr: np.ndarray) -> float:
    """Standard error of residuals from OLS ŷ = α + β·t (ddof=2).

    Measures instability *after* removing a linear trend, so a company whose
    margin improves steadily is not penalised.  Equivalent to the MSCI Quality
    Index 'earnings variability' method when applied to quarterly snapshots.

    Returns NaN when fewer than 4 non-NaN observations are present.
    """
    mask = ~np.isnan(arr)
    n = int(mask.sum())
    if n < 4:
        return np.nan
    y = arr[mask]
    t = np.arange(n, dtype=np.float64)
    t_c = t - t.mean()
    ss_t = float(np.dot(t_c, t_c))
    if ss_t < 1e-12:
        return np.nan
    b = np.dot(t_c, y) / ss_t
    resid = y - (y.mean() + b * t_c)
    # SS_resid / (n - 2) = SE_regression²
    return float(np.sqrt(np.dot(resid, resid) / max(n - 2, 1)))


def _downside_std(arr: np.ndarray) -> float:
    """Square root of the mean squared negative deviation from the mean.

    Only penalises downside surprises.  Useful when upside variance is benign
    (e.g. margin beats) but downside instability is what matters for quality.

    Returns NaN when fewer than 4 non-NaN observations are present.
    """
    valid = arr[~np.isnan(arr)]
    if len(valid) < 4:
        return np.nan
    mu = valid.mean()
    neg = np.minimum(valid - mu, 0.0)
    return float(np.sqrt(np.mean(neg * neg)))


# ── Quality variability ───────────────────────────────────────────────────────

# Fields on which variability metrics are computed.  Include the growth field
# so that revenue_growth_variability is produced when this function is called
# AFTER compute_ratios_growth has added net_revenues_ltm_growth_1y to data.
_VARIABILITY_FIELDS = [
    "gross_margin",
    "ebitda_margin",
    "ebit_margin",
    "fcf_margin",
    "net_margin",
    "roe",
    "roa",
    "roic",
    "roce",
    "net_revenues_ltm_growth_1y",   # requires compute_ratios_growth upstream
]

def _quarterly_series(data: pd.DataFrame, field: str) -> pd.Series:
    """One field as a (code, date)-indexed series of its own release dates."""
    return (
        data[data["field"] == field]
        .dropna(subset=["value"])
        .drop_duplicates(["code", "date"], keep="last")
        .set_index(["code", "date"])["value"]
        .astype(float)
        .sort_index()
    )


def _per_code_rolling(s: pd.Series, *, window: int, min_periods: int, how) -> pd.Series:
    """Rolling statistic over each code's last ``window`` observations."""

    def roll(x: pd.Series) -> pd.Series:
        r = x.rolling(window=window, min_periods=min_periods)
        if how == "std":
            return r.std()
        if how == "median":
            return r.median()
        return r.apply(how, raw=True)

    return s.groupby(level="code", group_keys=False).transform(roll)


def _quarterly_to_long(s: pd.Series, field_label: str) -> pd.DataFrame:
    """Per-code quarterly series → long format on each code's own release dates.

    Values are not repeated on other codes' dates: consumers take the last
    value on or before each date (``scoring.snapshot_*`` forward-fill), so a
    dense copy only multiplied storage (~20x for quality variability).
    """
    if s.empty:
        return pd.DataFrame(columns=["code", "date", "field", "value"])
    out = s.dropna().rename("value").reset_index()
    out["field"] = field_label
    return out[["code", "date", "field", "value"]]


def compute_quality_variability(
    data: pd.DataFrame,
    *,
    rolling_window_years: int = 5,
    min_obs_quarters: int = 8,
) -> pd.DataFrame:
    """Rolling variability metrics for Quality factor (three variants per field).

    Computed on each company's quarterly observations (release dates), over
    the last ``rolling_window_years × 4`` quarters with at least
    ``min_obs_quarters`` of them, and stored on each company's release dates.

    Variants produced for each field in ``_VARIABILITY_FIELDS``:

    ``{field}_variability``
        Rolling standard deviation (σ).  Equivalent to the AQR *Quality Minus
        Junk* approach.

    ``{field}_trend_deviation``
        Standard error of OLS residuals from ŷ = α + β·t (t in quarters) on
        each rolling window.  Does not penalise steady secular improvement.
        Closest to the MSCI Quality Index methodology.

    ``{field}_downside_variability``
        Square root of mean squared *negative* deviations from the period
        mean.  Penalises downside surprises only.

    Pipeline note
    -------------
    ``net_revenues_ltm_growth_1y`` (percent, from ``compute_ratios_growth``)
    yields the ``revenue_growth_*`` outputs.  Fields absent from ``data`` are
    silently skipped.
    """
    window = rolling_window_years * 4

    avail = set(data["field"].unique())
    fields_to_run = [f for f in _VARIABILITY_FIELDS if f in avail]
    if not fields_to_run:
        return pd.DataFrame(columns=["code", "date", "field", "value"])

    chunks: List[pd.DataFrame] = []
    for fld in fields_to_run:
        s = _quarterly_series(data, fld)
        base = "revenue_growth" if fld == "net_revenues_ltm_growth_1y" else fld
        for suffix, how in (
            ("variability", "std"),
            ("trend_deviation", _trend_se),
            ("downside_variability", _downside_std),
        ):
            rolled = _per_code_rolling(
                s, window=window, min_periods=min_obs_quarters, how=how
            )
            chunks.append(_quarterly_to_long(rolled, f"{base}_{suffix}"))

    return pd.concat(chunks, ignore_index=True)


# ── Accruals ──────────────────────────────────────────────────────────────────

def compute_accruals(data: pd.DataFrame) -> pd.DataFrame:
    """Accruals ratio — earnings quality signal (Sloan 1996).

    Produces two variants when the required fields are present:

    ``accruals_ratio_bs``  *(balance-sheet method)*
        ``Δ(total_equity + net_debt) / avg(total_assets)``

        Change in Net Operating Assets (NOA) scaled by average total assets.
        NOA is approximated as ``total_equity + net_debt``, which equals
        total assets minus cash and financial liabilities — a standard proxy
        that requires only balance-sheet fields already in the database.

        Annual Δ is computed as a 360-day diff on the forward-filled daily
        panel, matching the convention used in ``compute_ratios_growth``.

    ``accruals_ratio_cf``  *(cash-flow method, full Sloan formula)*
        ``(net_income_q − CFO_q − CFI_q) / total_assets``

        Requires ``cash_flow_from_investing_activities_q``, which becomes
        available after downloading the new CF fields.  This is the
        *complete* version: subtracting CFI removes the capex component
        that the simplified ``(NI − CFO)`` approximation ignores.

        Expressed at the quarterly level (not annualised) to match the
        reporting frequency of the underlying fields.

    ``capex_to_sales``  *(proxy via CFI)*
        ``−cash_flow_from_investing_activities_q / net_revenues_q``

        Capex intensity proxy.  CFI is used as a proxy for capex; note that
        CFI also includes acquisitions and asset disposals, so this metric
        overstates true capex intensity for companies with active M&A.

    Both ratio variants are expressed in percent (×100) and follow the
    long-format ``(code, date, field, value)`` convention.

    A *higher* accruals ratio signals lower earnings quality (earnings are
    driven by accruals rather than cash flows).  Inversion to produce a
    "quality" signal (higher = better) should be applied at the scoring layer.
    """
    chunks: List[pd.DataFrame] = []

    # ── Pivot to (code, date) × field ────────────────────────────────────────
    wide = data.pivot(index=["code", "date"], columns="field", values="value")

    # Forward-fill balance-sheet items: quarterly reporting → daily panel
    bs_carry = ["total_equity", "net_debt", "total_assets"]
    available_carry = [c for c in bs_carry if c in wide.columns]
    if available_carry:
        wide[available_carry] = wide.groupby(level=0)[available_carry].transform(
            lambda df: df.ffill(limit=252)
        )

    # ── 1. Balance-sheet accruals ─────────────────────────────────────────────
    bs_needed = {"total_equity", "net_debt", "total_assets"}
    if bs_needed.issubset(wide.columns):
        # NOA = total_equity + net_debt  →  (code, date) Series
        noa_series = wide["total_equity"] + wide["net_debt"]

        # Convert to (date × code) panels for daily_ffilled_panel
        noa_panel = noa_series.unstack(level=0)          # date rows, code cols
        assets_panel = wide["total_assets"].unstack(level=0)

        noa_dense, obs_idx = daily_ffilled_panel(noa_panel, ffill_limit=90)
        assets_dense, _ = daily_ffilled_panel(assets_panel, ffill_limit=90)

        # Annual Δ NOA (360-day diff matching compute_ratios_growth convention)
        delta_noa = noa_dense.diff(360)

        # Average total assets over the same 360-day window
        avg_assets = (assets_dense + assets_dense.shift(360)) / 2

        accruals_bs = (delta_noa / avg_assets.replace(0, np.nan) * 100).reindex(obs_idx)
        chunks.append(_panel_to_long(accruals_bs, obs_idx, "accruals_ratio_bs"))

    # ── 2. Cash-flow accruals (full Sloan) ────────────────────────────────────
    cf_needed = {
        "net_income_q",
        "cash_flow_from_operations_q",
        "cash_flow_from_investing_activities_q",
        "total_assets",
    }
    if cf_needed.issubset(wide.columns):
        cf_result = (
            wide["net_income_q"]
            - wide["cash_flow_from_operations_q"]
            - wide["cash_flow_from_investing_activities_q"]
        ) / wide["total_assets"].replace(0, np.nan) * 100

        stacked = cf_result.rename("accruals_ratio_cf")
        cf_long = stacked.reset_index()
        cf_long.columns = ["code", "date", "value"]
        cf_long["field"] = "accruals_ratio_cf"
        chunks.append(cf_long[["code", "date", "field", "value"]])

    # ── 3. Capex-to-sales proxy ───────────────────────────────────────────────
    capex_needed = {"cash_flow_from_investing_activities_q", "net_revenues_q"}
    if capex_needed.issubset(wide.columns):
        capex_ratio = (
            -wide["cash_flow_from_investing_activities_q"]
            / wide["net_revenues_q"].replace(0, np.nan)
            * 100
        )
        stacked_c = capex_ratio.rename("capex_to_sales")
        cap_long = stacked_c.reset_index()
        cap_long.columns = ["code", "date", "value"]
        cap_long["field"] = "capex_to_sales"
        chunks.append(cap_long[["code", "date", "field", "value"]])

    return (
        pd.concat(chunks, ignore_index=True)
        if chunks
        else pd.DataFrame(columns=["code", "date", "field", "value"])
    )


# ── Operating leverage ────────────────────────────────────────────────────────

def compute_operating_leverage(
    data: pd.DataFrame,
    *,
    rolling_quarters: int = 8,
    min_obs_quarters: int = 4,
    clip_dol: float = 20.0,
    min_rev_change_pct: float = 1.0,
) -> pd.DataFrame:
    """Degree of Operating Leverage (DOL) as a Quality/Safety signal.

    ``operating_leverage`` = rolling median of ``%Δ EBIT_q / %Δ Revenue_q``

    A company with high DOL has margins that amplify the business cycle:
    revenue drops translate into outsized EBIT declines.  This is a
    *"false quality" filter*: high-margin businesses with high DOL look
    attractive on static profitability screens but are fragile in downturns.

    Implementation
    --------------
    1.  Works on each company's quarterly releases (``ebit_q`` and
        ``net_revenues_q`` reported together).
    2.  Changes are year-over-year (same quarter, 4 releases back), which
        removes seasonality; pairs whose release dates are not 300–430 days
        apart (missing quarters) are dropped.
    3.  Revenue changes below ``min_rev_change_pct``% in absolute value are
        masked to avoid spurious extremes when revenue is nearly flat.
    4.  Raw DOL observations are clipped to ``[−clip_dol, +clip_dol]``.
    5.  Rolling median over the last ``rolling_quarters`` releases (at least
        ``min_obs_quarters`` valid), stored on each company's release dates.
    """
    needed = {"ebit_q", "net_revenues_q"}
    if not needed.issubset(set(data["field"].unique())):
        return pd.DataFrame(columns=["code", "date", "field", "value"])

    wide = pd.concat(
        {f: _quarterly_series(data, f) for f in sorted(needed)}, axis=1
    ).dropna()
    if wide.empty:
        return pd.DataFrame(columns=["code", "date", "field", "value"])

    by_code = wide.groupby(level="code")
    rel_dates = pd.Series(wide.index.get_level_values("date"), index=wide.index)
    gap_days = (rel_dates - rel_dates.groupby(level="code").shift(4)).dt.days
    yoy = gap_days.between(300, 430)

    rev_prev = by_code["net_revenues_q"].shift(4)
    ebit_prev = by_code["ebit_q"].shift(4)
    rev_pct = (wide["net_revenues_q"] - rev_prev) / rev_prev.abs().replace(0, np.nan) * 100
    ebit_pct = (wide["ebit_q"] - ebit_prev) / ebit_prev.abs().replace(0, np.nan) * 100

    rev_safe = rev_pct.where(yoy & (rev_pct.abs() >= min_rev_change_pct))
    dol_raw = (ebit_pct / rev_safe).clip(-clip_dol, clip_dol)

    dol = _per_code_rolling(
        dol_raw, window=rolling_quarters, min_periods=min_obs_quarters, how="median"
    )
    return _quarterly_to_long(dol, "operating_leverage")


def compute_value_timeseries(
    data: pd.DataFrame,
    *,
    rolling_window_years: int = 10,
) -> pd.DataFrame:
    temp_pivot = data.pivot(index=["field", "date"], columns="code", values="value")
    uniq_fields = sorted(data["field"].dropna().unique().tolist())

    chunks: List[pd.DataFrame] = []
    horizon = rolling_window_years * 360
    for fld in uniq_fields:
        temp_field = temp_pivot.loc[fld]
        densified, obs_idx = daily_ffilled_panel(temp_field, ffill_limit=360)

        pct_rank = densified.apply(
            lambda series: pd.Series(series).rolling(
                window=horizon, min_periods=360
            ).rank(pct=True),
            axis=0,
            raw=False,
        )
        pct_rank = pct_rank.reindex(obs_idx).multiply(100)
        stacked = _stack_wide(pct_rank)
        stacked.name = "value"
        blk = stacked.reset_index()
        blk.columns = ["date", "code", "value"]
        base = fld[:-4] if len(fld) > 4 else fld
        blk["field"] = f"{base}_percentile_{rolling_window_years}y"
        chunks.append(blk[["code", "date", "field", "value"]])

    return pd.concat(chunks, ignore_index=True)