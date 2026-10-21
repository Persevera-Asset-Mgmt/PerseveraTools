import warnings
from datetime import datetime
from typing import Optional, Union, List
import pandas as pd
import numpy as np

from persevera_tools.fixed_income.data import get_emissions, get_series, get_references
from persevera_tools.utils.dates import get_holidays
from persevera_tools.data.indicators import get_series as get_indicator_series
from persevera_tools.utils.logging import get_logger

logger = get_logger(__name__)

# Datas consecutivas em que a referência NTN-B de uma debênture pode ser
# repetida quando a ANBIMA não a publica (ex.: 2021-07-09 veio sem o campo).
REFERENCE_FFILL_LIMIT = 5


def _macaulay_duration(
    ytm: float,
    coupon_rate: float,
    years_to_maturity: float,
    coupon_frequency: int = 1,
) -> float:
    """Calculate Macaulay Duration via discounted cash flow.

    For zero-coupon bonds (coupon_rate=0), returns years_to_maturity directly,
    since the single cash flow at maturity makes D_mac = T by definition.

    Cash-flow times are measured backwards from maturity, so the final payment
    lands exactly on the (possibly fractional) maturity date and the remaining
    coupons are spaced 1/coupon_frequency apart. This preserves the fractional
    time to maturity instead of rounding it to whole coupon periods.

    Rates follow the Brazilian convention: both ``ytm`` and ``coupon_rate`` are
    effective annual rates, so the per-period coupon is
    ``(1 + coupon_rate) ** (1 / f) - 1`` (6% a.a. semiannual = 2.956% per
    semester, as in the NTN-B) and cash flows are discounted by
    ``(1 + ytm) ** t``.

    Args:
        ytm: Effective annual yield to maturity as a decimal (e.g., 0.12 for 12%).
        coupon_rate: Effective annual coupon rate as a decimal (0.0 for zero-coupon bonds).
        years_to_maturity: Remaining time to maturity in years.
        coupon_frequency: Number of coupon payments per year (default: 1).
    Returns:
        Macaulay Duration in years.
    """
    if coupon_rate == 0.0:
        return years_to_maturity
    if years_to_maturity <= 0:
        return 0.0

    f = coupon_frequency
    c = (1.0 + coupon_rate) ** (1.0 / f) - 1.0

    # Number of cash flows: one per coupon date in (0, T], measured back from T.
    n = max(1, int(np.ceil(years_to_maturity * f - 1e-9)))
    times = years_to_maturity - np.arange(n) / f  # descending; all in (0, T]
    cash_flows = np.full(n, c)
    cash_flows[0] += 1.0  # face value paid at maturity (times[0] == T)

    pv_flows = cash_flows / (1.0 + ytm) ** times
    total_pv = pv_flows.sum()
    return float((times * pv_flows).sum() / total_pv) if total_pv > 0 else 0.0


def _business_days_252(
    settlement: pd.Timestamp,
    maturity: pd.Timestamp,
    holidays: np.ndarray,
) -> int:
    """Count business days in (settlement, maturity] excluding ANBIMA holidays.

    Vectorized replacement for a day-by-day loop, matching the original
    convention (settlement excluded, maturity included).

    Args:
        settlement: Settlement/calculation date.
        maturity: Bond maturity date.
        holidays: Array of holidays as numpy datetime64[D].
    Returns:
        Number of business days between settlement (exclusive) and maturity (inclusive).
    """
    begin = np.datetime64((pd.Timestamp(settlement) + pd.Timedelta(days=1)).date(), 'D')
    end = np.datetime64((pd.Timestamp(maturity) + pd.Timedelta(days=1)).date(), 'D')
    if end <= begin:
        return 0
    return int(np.busday_count(begin, end, holidays=holidays))


# Fração descartada em cada ponta antes de calcular a média e a média ponderada
# dos spreads. A base tem emissores em estresse (spreads de centenas a 10^59 p.p.)
# e debêntures em "% do DI" cadastradas como "DI + spread" (~108). Com 5%, a
# série IPCA incentivada de 2017 (74 debêntures, 5 valores absurdos) continuava
# contaminada; 10% resolve sem mudar a série DI de forma relevante.
DEFAULT_SPREAD_TRIM = 0.10


def _trim_mask(values: pd.DataFrame, trim: float) -> pd.DataFrame:
    """True for the values kept after dropping ``floor(n * trim)`` at each tail of each row."""
    if trim <= 0:
        return values.notna()
    ranks = values.rank(axis=1, method='first')
    n = values.count(axis=1)
    k = np.floor(n * trim)
    return ranks.gt(k, axis=0) & ranks.le(n - k, axis=0)


# --------------------------------------------------------------------------- #
# Pure building blocks (no database access)
#
# The functions below only transform DataFrames, so callers that read the data
# on their own (e.g. a web backend with its own read-only connection) get the
# exact same numbers as the pipeline. ``calculate_spread`` /
# ``calculate_duration_stats`` are the database-backed wrappers around them.
# --------------------------------------------------------------------------- #

def outstanding_volume(
    par: pd.DataFrame,
    emissions: pd.DataFrame,
    like: pd.DataFrame,
) -> pd.DataFrame:
    """Outstanding volume proxy per (date, code): ANBIMA ``par_value`` x issued quantity.

    ``par_value`` (PU par) reflects amortizations; it only exists from 2021-04.
    Before that, or for codes without it, the nominal value at issuance is used.
    Codes absent from ``emissions`` (e.g. CRI/CRA) get NaN volume.

    Args:
        par: Wide (date × code) ``par_value`` panel; may be empty.
        emissions: Rows of ``credito_privado_emissoes`` (``code``,
            ``valor_nominal_na_emissao``, ``quantidade_emitida``).
        like: Panel whose index and columns the result follows.
    """
    par = par.reindex(index=like.index, columns=like.columns).ffill()
    emis = emissions.drop_duplicates('code').set_index('code')
    par = par.fillna(emis['valor_nominal_na_emissao'].reindex(like.columns))
    return par * emis['quantidade_emitida'].reindex(like.columns)


def spread_di_panel(ytm: pd.DataFrame) -> pd.DataFrame:
    """Spread per (date, code) of "DI + spread" assets: the ANBIMA indicative rate itself.

    Only gaps between two quotes are filled (up to 5 dates); no extrapolation
    after the last one.
    """
    return ytm.interpolate(limit=5, limit_area='inside')


def references_wide(references: pd.DataFrame, index: pd.Index) -> pd.DataFrame:
    """Benchmark NTN-B maturity per (date, code), aligned to ``index``.

    ``references`` is long (``date``, ``code``, ``reference``), as in
    ``credito_privado_referencia``. A missing day reuses the code's previous
    reference for up to ``REFERENCE_FFILL_LIMIT`` dates. No back-fill: it would
    use a benchmark chosen later.
    """
    ref = (
        references.dropna(subset=['reference'])
        .sort_values(['code', 'date'])
        .drop_duplicates(subset=['date', 'code'], keep='last')
        .pivot(index='date', columns='code', values='reference')
    )
    return (
        ref.reindex(index=ref.index.union(index))
        .ffill(limit=REFERENCE_FFILL_LIMIT)
        .reindex(index=index)
    )


def ntnb_curve_wide(titulos_publicos: pd.DataFrame) -> pd.DataFrame:
    """NTN-B yields as a (date × maturity) lookup table.

    ``titulos_publicos`` is what ``get_series(code='NTN-B', category='titulos_publicos')``
    returns (``date``, ``maturity``, ``value`` and optionally ``source``); ANBIMA is
    preferred when several sources exist.
    """
    tp = titulos_publicos
    if isinstance(tp.index, pd.MultiIndex):
        tp = tp.reset_index()
    elif getattr(tp.index, "name", None) in ['date', 'maturity']:
        tp = tp.reset_index()
    if 'source' in tp.columns:
        if 'anbima' in tp['source'].unique().tolist():
            tp = tp[tp['source'] == 'anbima']
        tp = tp.drop(columns=['source'])
    return (
        tp.drop_duplicates(subset=['date', 'maturity'])
        .pivot(index='date', columns='maturity', values='value')
    )


def spread_ipca_panel(
    ytm: pd.DataFrame,
    references: pd.DataFrame,
    ntnb_curve: pd.DataFrame,
) -> pd.DataFrame:
    """Spread per (date, code) of IPCA-linked assets: indicative rate minus the NTN-B yield
    at the asset's benchmark maturity on the same date.

    Args:
        ytm: Wide (date × code) indicative rates; zeros are treated as missing.
        references: Long benchmark references (``date``, ``code``, ``reference``).
            Assets without a reference are left out (logged).
        ntnb_curve: NTN-B yields as returned by ``ntnb_curve_wide``.
    """
    series_wide = ytm.mask(ytm == 0.).interpolate(limit=5, limit_area='inside')
    ref_wide = references_wide(references, series_wide.index)
    sem_ref = series_wide.notna() & ref_wide.reindex(columns=series_wide.columns).isna()
    if sem_ref.any().any():
        logger.warning(
            "Spread IPCA: %d pares (debenture, dia) com taxa e sem referencia NTN-B ficaram "
            "de fora (%.1f%% das observacoes, %d debentures)",
            int(sem_ref.sum().sum()),
            100 * sem_ref.sum().sum() / series_wide.notna().sum().sum(),
            int(sem_ref.any().sum()),
        )

    # Vectorised NTN-B lookup: for each (date, code) pair fetch the yield at
    # the code's benchmark maturity using a stacked-index reindex — no long-format
    # merge needed.
    # Levels by position (0 = date, -1 = code): the index may be unnamed when the
    # caller builds the panels itself.
    ntnb_stacked = ntnb_curve.stack()
    ref_long = ref_wide.stack().rename('reference')
    lookup_idx = pd.MultiIndex.from_arrays(
        [ref_long.index.get_level_values(0), ref_long.to_numpy()]
    )
    ytm_ntnb_wide = (
        pd.Series(ntnb_stacked.reindex(lookup_idx).to_numpy(), index=ref_long.index)
        .unstack(-1)
    )

    sem_ntnb = ytm_ntnb_wide.reindex_like(series_wide).isna() & ref_wide.reindex(columns=series_wide.columns).notna() & series_wide.notna()
    if sem_ntnb.any().any():
        logger.warning(
            "Spread IPCA: %d pares (debenture, dia) com referencia sem taxa da NTN-B correspondente ficaram de fora",
            int(sem_ntnb.sum().sum()),
        )

    # Spread: single wide-format subtraction, then interpolate gaps.
    return (series_wide - ytm_ntnb_wide).interpolate(limit=5, limit_area='inside')


# --------------------------------------------------------------------------- #
# Database-backed wrappers
# --------------------------------------------------------------------------- #

def _outstanding_volume(
    series: pd.DataFrame,
    emissions: pd.DataFrame,
    start_date,
    end_date,
) -> pd.DataFrame:
    """``outstanding_volume`` with ``par_value`` read from the database."""
    codes = list(series.columns)
    try:
        par = get_series(code=codes, source='anbima', category='credito_privado_di',
                         start_date=start_date, end_date=end_date, field='par_value')
    except ValueError:
        par = pd.DataFrame()
    return outstanding_volume(par, emissions, series)


def _restrict_codes(universe: List[str], codes: Optional[List[str]]) -> List[str]:
    """Universe intersected with ``codes`` (order of the universe); all of it when None."""
    if codes is None:
        return universe
    pedidos = {str(c).strip().upper() for c in codes}
    return [c for c in universe if str(c).strip().upper() in pedidos]


def _spread_panel(
    index_code: str,
    codes: List[str],
    start_date,
    end_date,
    field: Union[str, List[str]],
) -> pd.DataFrame:
    """Read the inputs for ``codes`` and build the per-asset spread panel."""
    if index_code == 'DI':
        return spread_di_panel(_get_wide_series(codes, index_code, field, start_date, end_date))
    if index_code == 'IPCA':
        ytm = _get_wide_series(codes, index_code, field, start_date, end_date)
        ref_start = (pd.Timestamp(start_date) - pd.Timedelta(days=15)) if start_date is not None else None
        references = get_references(code=codes, start_date=ref_start, end_date=end_date)
        titulos = get_series(code='NTN-B', category='titulos_publicos', start_date=start_date, end_date=end_date, field=field)
        return spread_ipca_panel(ytm, references, ntnb_curve_wide(titulos))
    raise ValueError("Invalid index code")


def calculate_spread_panel(
    index_code: str,
    start_date: Optional[Union[str, datetime, pd.Timestamp]] = None,
    end_date: Optional[Union[str, datetime, pd.Timestamp]] = None,
    field: Union[str, List[str]] = 'yield_to_maturity',
    deb_incent_lei_12431: Optional[bool] = None,
    codes: Optional[List[str]] = None) -> pd.DataFrame:
    """Per-asset spread panel (date × code) used by ``calculate_spread``.

    Same universe as ``calculate_spread`` (debentures in ``credito_privado_emissoes``
    for ``index_code``; DI only at 100% of CDI), optionally restricted to ``codes``.
    Zeros are not masked here; ``cross_section_stats`` does it.

    Args:
        index_code: 'DI' or 'IPCA'.
        start_date, end_date: Optional date filters.
        field: Field to read (default: 'yield_to_maturity').
        deb_incent_lei_12431: Whether to filter emissions under Lei 12431.
        codes: Optional subset of codes (case-insensitive); None keeps the whole universe.
    """
    emissions = get_emissions(index_code=index_code, deb_incent_lei_12431=deb_incent_lei_12431)
    universe = _restrict_codes(_select_codes(emissions, index_code), codes)
    return _spread_panel(index_code, universe, start_date, end_date, field)


def calculate_spread(
    index_code: str,
    start_date: Optional[Union[str, datetime, pd.Timestamp]] = None,
    end_date: Optional[Union[str, datetime, pd.Timestamp]] = None,
    field: Union[str, List[str]] = 'yield_to_maturity',
    calculate_distribution: bool = False,
    deb_incent_lei_12431: Optional[bool] = None,
    trim: float = DEFAULT_SPREAD_TRIM,
    codes: Optional[List[str]] = None) -> pd.DataFrame:
    """Calculate the spread for a given index code.

    - DI: ANBIMA indicative rate of "DI + spread" debentures (already a spread).
    - IPCA: debenture indicative rate minus the NTN-B yield at its reference maturity.

    ``median`` and the distribution counts use every debenture. ``mean`` and
    ``weighted_mean`` are trimmed: on each date the ``floor(n * trim)`` lowest
    and highest spreads are dropped first. ``weighted_mean`` and the volume
    columns weight by outstanding volume (ANBIMA ``par_value`` x issued
    quantity; nominal value at issuance before 2021-04).

    Args:
        index_code: Single index code. Supported values: 'DI', 'IPCA'.
        start_date: Optional start date filter as string 'YYYY-MM-DD', datetime, or pandas Timestamp.
        end_date: Optional end date filter as string 'YYYY-MM-DD', datetime, or pandas Timestamp.
        field: Field or list of fields to retrieve (default: 'yield_to_maturity').
        calculate_distribution: Whether to calculate the distribution of the spread.
        deb_incent_lei_12431: Whether to filter emissions under Lei 12431 (default: None).
        trim: Fraction dropped at each tail for ``mean`` / ``weighted_mean`` (default 10%).
        codes: Optional subset of the universe (e.g. one issuer's debentures);
            None keeps the whole universe.
    Returns:
        DataFrame with spread data, indexed by 'date'.
    Raises:
        ValueError: If index_code is not 'DI' or 'IPCA'.
    """
    emissions = get_emissions(index_code=index_code, deb_incent_lei_12431=deb_incent_lei_12431)
    universe = _restrict_codes(_select_codes(emissions, index_code), codes)
    series = _spread_panel(index_code, universe, start_date, end_date, field)
    return _cross_section_stats(series, emissions, start_date, end_date, trim, calculate_distribution, SPREAD_BUCKETS)


def calculate_duration_stats(
    index_code: str,
    start_date: Optional[Union[str, datetime, pd.Timestamp]] = None,
    end_date: Optional[Union[str, datetime, pd.Timestamp]] = None,
    calculate_distribution: bool = False,
    deb_incent_lei_12431: Optional[bool] = None,
    trim: float = DEFAULT_SPREAD_TRIM,
    codes: Optional[List[str]] = None) -> pd.DataFrame:
    """Calculate cross-sectional duration statistics for a given index code.

    Same universe and aggregation as ``calculate_spread`` (trimmed ``mean`` /
    ``weighted_mean``, outstanding-volume weights), applied to the
    ANBIMA-published duration, converted from business days to years (/252).

    Args:
        index_code: Single index code. Supported values: 'DI', 'IPCA'.
        start_date: Optional start date filter as string 'YYYY-MM-DD', datetime, or pandas Timestamp.
        end_date: Optional end date filter as string 'YYYY-MM-DD', datetime, or pandas Timestamp.
        calculate_distribution: Whether to calculate the distribution of the duration.
        deb_incent_lei_12431: Whether to filter emissions under Lei 12431 (default: None).
        trim: Fraction dropped at each tail for ``mean`` / ``weighted_mean`` (default 10%).
        codes: Optional subset of the universe; None keeps the whole universe.
    Returns:
        DataFrame with duration statistics in years, indexed by 'date'.
    Raises:
        ValueError: If index_code is not 'DI' or 'IPCA'.
    """
    emissions = get_emissions(index_code=index_code, deb_incent_lei_12431=deb_incent_lei_12431)
    universe = _restrict_codes(_select_codes(emissions, index_code), codes)

    series = _get_wide_series(universe, index_code, 'duration', start_date, end_date)
    series = series.mask(series == 0.).interpolate(limit=5, limit_area='inside') / 252

    return _cross_section_stats(series, emissions, start_date, end_date, trim, calculate_distribution, DURATION_BUCKETS)


# Distribution buckets as (column name, lower bound inclusive, upper bound exclusive).
# Spread values are in % p.a. (0.50 == 50bp); duration values are in years.
SPREAD_BUCKETS = (
    ('count_yield_under_neg50bp', -np.inf, -0.50),
    ('count_yield_neg50_0bp',     -0.50,    0.00),
    ('count_yield_0_50bp',         0.00,    0.50),
    ('count_yield_50_75bp',        0.50,    0.75),
    ('count_yield_75_100bp',       0.75,    1.00),
    ('count_yield_100_150bp',      1.00,    1.50),
    ('count_yield_150_250bp',      1.50,    2.50),
    ('count_yield_above_250bp',    2.50,  np.inf),
)

DURATION_BUCKETS = (
    ('count_duration_under_1y',  -np.inf, 1.0),
    ('count_duration_1_3y',       1.0,    3.0),
    ('count_duration_3_5y',       3.0,    5.0),
    ('count_duration_5_7y',       5.0,    7.0),
    ('count_duration_above_7y',   7.0,  np.inf),
)


def _select_codes(emissions: pd.DataFrame, index_code: str) -> List[str]:
    """Select the asset universe for an index code (DI: only 100% of CDI)."""
    if index_code == 'DI':
        return emissions[emissions['percentual_multiplicador_rentabilidade'] == 100]['code'].tolist()
    if index_code == 'IPCA':
        return emissions['code'].tolist()
    raise ValueError("Invalid index code")


def _get_wide_series(
    codes: List[str],
    index_code: str,
    field: Union[str, List[str]],
    start_date: Optional[Union[str, datetime, pd.Timestamp]],
    end_date: Optional[Union[str, datetime, pd.Timestamp]]) -> pd.DataFrame:
    """Fetch one ANBIMA field for the given codes as a wide (date × code) DataFrame.

    No interpolation or zero-masking is applied; callers handle both.
    """
    if index_code == 'DI':
        series = get_series(code=codes, source='anbima', category='credito_privado_di', start_date=start_date, end_date=end_date, field=field)
        if isinstance(series.columns, pd.MultiIndex):
            selected_field = field[0] if isinstance(field, list) and len(field) > 0 else (field if isinstance(field, str) else 'yield_to_maturity')
            if 'field' in series.columns.names:
                series = series.xs(selected_field, axis=1, level='field')
            if 'source' in series.columns.names:
                series = series.xs('anbima', axis=1, level='source')
        return series

    # IPCA category returns long format (date, code, value); pivot once to wide.
    series = get_series(code=codes, source='anbima', category='credito_privado_ipca', start_date=start_date, end_date=end_date, field=field)
    return (
        series.drop_duplicates(subset=['date', 'code'])
        .pivot(index='date', columns='code', values='value')
    )


def _cross_section_stats(
    series: pd.DataFrame,
    emissions: pd.DataFrame,
    start_date,
    end_date,
    trim: float,
    calculate_distribution: bool,
    buckets: tuple) -> pd.DataFrame:
    """``cross_section_stats`` over the debentures in ``emissions``, with the
    outstanding volume read from the database (``_outstanding_volume``)."""
    emissions = emissions[emissions['code'].isin(series.columns)]
    series = series[[c for c in series.columns if c in set(emissions['code'])]]
    series = series.mask(series == 0.)
    volume = _outstanding_volume(series, emissions, start_date, end_date)
    return cross_section_stats(series, volume, trim, calculate_distribution, buckets)


def cross_section_stats(
    series: pd.DataFrame,
    volume: Optional[pd.DataFrame] = None,
    trim: float = DEFAULT_SPREAD_TRIM,
    calculate_distribution: bool = False,
    buckets: tuple = SPREAD_BUCKETS) -> pd.DataFrame:
    """Daily cross-sectional statistics of a wide (date × code) panel.

    ``median`` and the distribution counts use every asset; ``mean`` and
    ``weighted_mean`` are trimmed (``_trim_mask``: with fewer than
    ``1 / trim`` assets nothing is dropped). Zeros are treated as missing.

    ``weighted_mean`` and the volume columns weight by ``volume`` (same shape as
    ``series``, e.g. ``outstanding_volume``). Assets without volume count in
    ``median``/``mean`` but not in the weighted figures; without any volume they
    are NaN (``weighted_mean``) or 0 (volume columns).

    Args:
        series: Wide (date × code) panel (spreads in % p.a. or durations in years).
        volume: Optional wide (date × code) weights; None means no weights.
        trim: Fraction dropped at each tail for ``mean`` / ``weighted_mean``.
        calculate_distribution: Whether to add the above/below-mean and bucket counts.
        buckets: (name, lower inclusive, upper exclusive) tuples, e.g.
            ``SPREAD_BUCKETS`` or ``DURATION_BUCKETS``.
    """
    series = series.mask(series == 0.)
    if volume is None:
        volume = pd.DataFrame(np.nan, index=series.index, columns=series.columns)
    volume_df = volume.reindex(index=series.index, columns=series.columns).where(series.notna())
    kept = _trim_mask(series, trim)
    series_kept = series.where(kept)
    volume_kept = volume_df.where(kept)

    stats = pd.DataFrame(index=series.index)
    stats['median'] = series.median(axis=1)
    stats['mean'] = series_kept.mean(axis=1)
    stats['weighted_mean'] = (
        (series_kept * volume_kept).sum(axis=1, min_count=1)
        / volume_kept.sum(axis=1, min_count=1)
    )

    if calculate_distribution:
        # Extract raw numpy arrays once and use column broadcasting to avoid
        # repeated .T round-trips and redundant boolean recomputations.
        vals = series.to_numpy()
        mean_col = stats['mean'].to_numpy()[:, np.newaxis]

        above_mean = pd.DataFrame(vals > mean_col, index=series.index, columns=series.columns)
        under_mean = pd.DataFrame(vals <= mean_col, index=series.index, columns=series.columns)

        stats['count_above_mean'] = above_mean.sum(axis=1)
        stats['count_under_mean'] = under_mean.sum(axis=1)
        stats['volume_above_mean'] = above_mean.mul(volume_df).sum(axis=1)
        stats['volume_under_mean'] = under_mean.mul(volume_df).sum(axis=1)

        # NaN compares False on both sides, so missing values fall in no bucket.
        for name, lower, upper in buckets:
            stats[name] = ((vals >= lower) & (vals < upper)).sum(axis=1)

    return stats


def _normalize_codes(code) -> "tuple[list, bool]":
    """Normalize the ``code`` argument into a de-duplicated list of strings.

    Returns the list of codes and a flag indicating whether the input was a
    single scalar string (so the public function can return a dict instead of
    a DataFrame).
    """
    if isinstance(code, str):
        codes, single = [code], True
    elif isinstance(code, pd.Series):
        codes, single = code.dropna().astype(str).tolist(), False
    elif isinstance(code, (pd.Index, np.ndarray)):
        codes, single = [str(c) for c in code.tolist()], False
    elif isinstance(code, (list, tuple, set)):
        codes, single = [str(c) for c in code], False
    else:
        raise ValueError("code must be a string, list, tuple, set, pandas Series, or Index")

    seen = set()
    codes = [c for c in codes if c and not (c in seen or seen.add(c))]
    if not codes:
        raise ValueError("No valid codes provided")
    return codes, single


def _fetch_anbima_field(codes: list, field: str, end_date_str: str) -> dict:
    """Fetch a single ANBIMA field for one or more codes, batching the query.

    Returns a dict mapping code -> non-empty pandas Series (NaNs dropped).
    Codes without data are simply absent from the result. Never raises.
    """
    out: dict = {}
    try:
        if len(codes) == 1:
            series = get_series(code=codes[0], source='anbima', field=field, end_date=end_date_str)
            if isinstance(series, pd.DataFrame):
                series = series.iloc[:, 0]
            series = series.dropna()
            if not series.empty:
                out[codes[0]] = series
            return out

        raw = get_series(code=codes, source='all', field=field, end_date=end_date_str)
        if isinstance(raw, pd.Series):
            raw = raw.dropna()
            if not raw.empty:
                out[codes[0]] = raw
            return out

        cols = raw.columns
        if isinstance(cols, pd.MultiIndex) and 'source' in cols.names:
            if 'anbima' in cols.get_level_values('source'):
                raw = raw.xs('anbima', axis=1, level='source')
            else:
                return out
        for c in raw.columns:
            series = raw[c].dropna()
            if not series.empty:
                out[str(c)] = series
    except (ValueError, IndexError, KeyError):
        pass
    return out


def _broadcast_param(value, codes: list, name: str) -> dict:
    """Broadcast a per-asset parameter into a {code: value} mapping.

    Accepted forms:
    - None -> every code maps to None.
    - dict / pandas Series indexed by code -> looked up per code (missing -> None).
    - list / tuple / ndarray -> aligned positionally to ``codes`` (length must match).
    - scalar (str, number, datetime, Timestamp, ...) -> applied to every code.
    """
    if value is None:
        return {c: None for c in codes}
    if isinstance(value, dict):
        return {c: value.get(c) for c in codes}
    if isinstance(value, pd.Series):
        lookup = value.to_dict()
        return {c: lookup.get(c) for c in codes}
    if isinstance(value, (list, tuple, np.ndarray)):
        seq = list(value)
        if len(seq) != len(codes):
            raise ValueError(
                f"Length of '{name}' ({len(seq)}) must match the number of codes ({len(codes)}). "
                "Use a dict or pandas Series keyed by code to avoid positional alignment."
            )
        return dict(zip(codes, seq))
    return {c: value for c in codes}


CDI_SERIES_CODE = 'br_cdi_yield'
# Dias úteis entre a data do dado e a data pedida acima dos quais o resultado
# é marcado como desatualizado (``stale``).
DEFAULT_MAX_STALE_DAYS = 5


def calculate_duration(
    code: Union[str, List[str], pd.Series, pd.Index],
    maturity_date: Optional[Union[str, datetime, pd.Timestamp, list, tuple, dict, pd.Series]] = None,
    settlement_date: Optional[Union[str, datetime, pd.Timestamp]] = None,
    ytm: Optional[Union[float, list, tuple, dict, pd.Series]] = None,
    coupon_rate: Optional[Union[float, list, tuple, dict, pd.Series]] = None,
    coupon_frequency: Union[int, list, tuple, dict, pd.Series] = 1,
    indice: Optional[Union[str, list, tuple, dict, pd.Series]] = None,
    use_anbima: bool = True,
    max_stale_days: int = DEFAULT_MAX_STALE_DAYS,
) -> Union[dict, pd.DataFrame]:
    """Calculate Macaulay Duration (in years) for one or more fixed income assets.

    Accepts a single code (str) or a collection of codes (list, tuple, set,
    pandas Series/Index, e.g. a DataFrame column). For a single code a dict is
    returned; for a collection a DataFrame indexed by code is returned.

    For each asset it attempts first to use the duration published by ANBIMA in
    the database (published in business days and converted here to years,
    DU/252). If unavailable (e.g. CRI, CRA, CDB), it falls back to an
    analytical calculation using the yield to maturity (from the database, or
    provided via ``ytm``) and the ``maturity_date`` supplied by the caller.
    The maturity is never looked up in the database.

    The analytical fallback assumes a bullet structure (no amortization) with
    periodic coupons. When coupon_rate is not provided, a par-bond approximation
    is used (coupon_rate = yield), which is accurate for bonds trading near par.
    For zero-coupon bonds, pass coupon_rate=0.0; duration equals years_to_maturity.

    Conventions (Brazilian market): time in business days / 252 with the
    ANBIMA holiday calendar, and effective annual rates (see
    :func:`_macaulay_duration`). For ``indice='DI'`` the yield — from the
    database or from ``ytm`` — is the spread over CDI ("DI + spread", as ANBIMA
    publishes it); cash flows are discounted at the full rate
    ``(1 + CDI) * (1 + spread) - 1``, with CDI from ``br_cdi_yield`` on the
    settlement date.

    Per-asset parameters: ``maturity_date``, ``indice``, ``ytm`` and
    ``coupon_rate`` accept either a single scalar (applied to every code), a
    list/tuple aligned positionally to the codes, or a dict / pandas Series
    keyed by code (recommended for collections).

    Performance: holidays are fetched once and reused across all codes, the
    yield/duration series are fetched in a single batched query, and business
    days use ``numpy.busday_count`` instead of a day-by-day loop. No emissions
    table lookup is performed.

    Args:
        code: Single bond code or a collection of codes. Supports str, list,
              tuple, set, pandas Series (e.g. a DataFrame column), or Index.
        maturity_date: Bond maturity date(s). Required whenever the analytical
                       fallback is used (i.e. when an ANBIMA duration is not
                       available or ``ytm`` is provided). Scalar, list/tuple
                       aligned to codes, or dict/Series keyed by code.
        settlement_date: Calculation date. Defaults to today; database values
                         are the latest on or before it.
        ytm: Annual yield to maturity as a decimal (e.g., 0.12 for 12%); for
             ``indice='DI'`` the spread over CDI (e.g., 0.015 for DI + 1.5%).
             When provided for a code, skips the database lookup for yield (and
             the ANBIMA duration lookup) for that code. Scalar, list/tuple, or
             dict/Series keyed by code. Required for assets without historical
             series or for prospective/future settlement dates, EXCEPT for
             zero-coupon/bullet bonds (coupon_rate=0.0), where the yield is not
             needed because duration equals the time to maturity.
        coupon_rate: Effective annual coupon rate as a decimal (e.g., 0.12 for 12%).
                     Use 0.0 for zero-coupon/bullet bonds such as a CDB that
                     pays principal and interest only at maturity; in that case
                     duration equals the time to maturity and no ytm is required.
                     Overrides the par-bond approximation when provided. Scalar
                     or per-code.
        coupon_frequency: Number of coupon payments per year (default: 1, annual).
                          Accepts a scalar (applied to all codes), or a list/tuple
                          aligned positionally, or a dict/Series keyed by code.
        indice: 'DI' means the yield is a spread over CDI; any other value
                (e.g. 'IPCA', 'PRE') means the yield is the full rate. Scalar
                or per-code. Defaults to 'DI' (with a warning) when omitted.
        use_anbima: If True, returns the ANBIMA-published duration when available.
        max_stale_days: Business days between the data date and
                        ``settlement_date`` above which ``stale`` is True.
    Returns:
        For a single code, a dict with keys:
            - 'macaulay_duration': Macaulay Duration in years.
            - 'years_to_maturity': Remaining time to maturity in years, DU/252
              (None if ANBIMA source).
            - 'ytm': Yield as published/provided (decimal; spread over CDI for DI).
            - 'cdi': CDI used to build the full DI rate (decimal; None otherwise).
            - 'coupon_rate': Coupon rate used (decimal, None if ANBIMA source).
            - 'par_bond_approximation': True if coupon_rate was not provided and ytm was
              used as a proxy (par-bond approximation); False otherwise; NaN for no_data rows.
            - 'source': 'anbima', 'calculated', or 'no_data'.
            - 'settlement_date': The date of the data used for the calculation.
            - 'stale': True if that date is more than ``max_stale_days`` business
              days before ``settlement_date``.
        For a collection of codes, a DataFrame with those keys as columns,
        indexed by code. Codes with missing data get NaN values and source
        'no_data' (instead of raising).
    Raises:
        ValueError: For a single code, if required data (yield, maturity or
                    CDI) is missing; or if a per-code list length does not match codes.
    """
    codes, single = _normalize_codes(code)

    if settlement_date is None:
        settlement_dt = pd.Timestamp.today().normalize()
    elif isinstance(settlement_date, (str, datetime, pd.Timestamp)):
        settlement_dt = pd.Timestamp(settlement_date)
    else:
        raise ValueError("settlement_date must be a string, datetime, or pd.Timestamp")

    end_date_str = settlement_dt.strftime('%Y-%m-%d')

    # Per-asset parameters (scalar / positional list / dict / Series keyed by code).
    maturity_map = _broadcast_param(maturity_date, codes, 'maturity_date')
    indice_map = _broadcast_param(indice, codes, 'indice')
    coupon_map = _broadcast_param(coupon_rate, codes, 'coupon_rate')
    coupon_freq_map = _broadcast_param(coupon_frequency, codes, 'coupon_frequency')
    ytm_user_map = _broadcast_param(ytm, codes, 'ytm')

    # --- Batched database reads (only for codes without a user-provided ytm) ---
    codes_db = [c for c in codes if ytm_user_map[c] is None]
    dur_map: dict = {}
    ytm_map: dict = {}
    if codes_db:
        ytm_map = _fetch_anbima_field(codes_db, 'yield_to_maturity', end_date_str)
        if use_anbima:
            dur_map = _fetch_anbima_field(codes_db, 'duration', end_date_str)

    lazy: dict = {}

    def _holidays_arr() -> np.ndarray:
        if 'holidays' not in lazy:
            lazy['holidays'] = np.array(
                [np.datetime64(pd.Timestamp(h).date(), 'D') for h in get_holidays()],
                dtype='datetime64[D]',
            )
        return lazy['holidays']

    def _cdi_on(date) -> Optional[float]:
        """CDI (decimal, annual) on the last date on or before ``date``."""
        if 'cdi' not in lazy:
            try:
                cdi = get_indicator_series(CDI_SERIES_CODE, end_date=end_date_str)
                lazy['cdi'] = (cdi.iloc[:, 0] if isinstance(cdi, pd.DataFrame) else cdi).dropna().sort_index()
            except ValueError:
                lazy['cdi'] = pd.Series(dtype=float)
        cdi = lazy['cdi'].loc[:pd.Timestamp(date)]
        return float(cdi.iloc[-1]) / 100.0 if not cdi.empty else None

    def _is_stale(data_date) -> bool:
        lag = _business_days_252(pd.Timestamp(data_date), settlement_dt, _holidays_arr())
        return lag > max_stale_days

    def _resolve_indice(bond_code: str) -> str:
        if indice_map[bond_code] is not None:
            return indice_map[bond_code]
        warnings.warn(
            f"indice not specified for '{bond_code}'; defaulting to 'DI' (yield read as "
            "spread over CDI). Pass indice='IPCA' (or another value) for bonds whose "
            "yield is the full rate.",
            UserWarning,
            stacklevel=4,
        )
        return 'DI'

    def _no_data_row(ytm_decimal=None, settlement=None) -> dict:
        return {
            'macaulay_duration': np.nan,
            'years_to_maturity': np.nan,
            'ytm': ytm_decimal,
            'cdi': None,
            'coupon_rate': np.nan,
            'par_bond_approximation': np.nan,
            'source': 'no_data',
            'settlement_date': settlement,
            'stale': np.nan,
        }

    def _compute(bond_code: str) -> dict:
        user_ytm = ytm_user_map[bond_code]
        coupon_value = coupon_map[bond_code]
        # A zero-coupon / bullet bond (e.g. a CDB paying everything at maturity)
        # has a single cash flow, so its Macaulay duration equals the time to
        # maturity and the yield is not needed.
        is_zero_coupon = coupon_value == 0.0

        # 1. ANBIMA-published duration (skipped when the caller supplies ytm).
        if use_anbima and user_ytm is None and bond_code in dur_map:
            dur_series = dur_map[bond_code]
            ytm_val = None
            if bond_code in ytm_map:
                ytm_val = float(ytm_map[bond_code].iloc[-1]) / 100.0
            data_date = dur_series.index[-1]
            return {
                # ANBIMA publishes duration in business days.
                'macaulay_duration': float(dur_series.iloc[-1]) / 252.0,
                'years_to_maturity': None,
                'ytm': ytm_val,
                'cdi': None,
                'coupon_rate': None,
                'par_bond_approximation': False,
                'source': 'anbima',
                'settlement_date': data_date,
                'stale': _is_stale(data_date),
            }

        # 2. Resolve yield to maturity and the effective settlement date.
        stale = False
        if user_ytm is not None:
            ytm_decimal = user_ytm
            actual_settlement = settlement_dt
        elif bond_code in ytm_map:
            ytm_series = ytm_map[bond_code]
            ytm_decimal = float(ytm_series.iloc[-1]) / 100.0
            actual_settlement = ytm_series.index[-1]
            stale = _is_stale(actual_settlement)
        elif is_zero_coupon:
            # No yield available, but none is needed for a single-cash-flow bond.
            ytm_decimal = None
            actual_settlement = settlement_dt
        elif single:
            raise ValueError(
                f"No yield_to_maturity data found for code '{bond_code}'. "
                "Provide ytm= explicitly (or coupon_rate=0.0 for a zero-coupon/bullet "
                "bond such as a CDB) for assets without historical series or future dates."
            )
        else:
            return _no_data_row()

        # 3. Resolve maturity date (caller-supplied only).
        bond_maturity = maturity_map[bond_code]
        if bond_maturity is None:
            if single:
                raise ValueError(
                    f"No maturity_date provided for code '{bond_code}'. "
                    "Pass maturity_date= (scalar, list, dict, or Series keyed by code)."
                )
            return _no_data_row(ytm_decimal, actual_settlement)
        bond_maturity = pd.Timestamp(bond_maturity)

        # 4. Full discount rate: DI yields are spreads over CDI.
        cdi_used = None
        rate = ytm_decimal
        if ytm_decimal is not None and _resolve_indice(bond_code) == 'DI':
            cdi_used = _cdi_on(actual_settlement)
            if cdi_used is None:
                if single:
                    raise ValueError(
                        f"No {CDI_SERIES_CODE} on or before {pd.Timestamp(actual_settlement).date()} "
                        f"to build the DI rate of '{bond_code}'."
                    )
                return _no_data_row(ytm_decimal, actual_settlement)
            rate = (1.0 + cdi_used) * (1.0 + ytm_decimal) - 1.0

        is_par_bond_approx = coupon_value is None
        effective_coupon = coupon_value if coupon_value is not None else rate

        base = {
            'ytm': ytm_decimal,
            'cdi': cdi_used,
            'coupon_rate': effective_coupon,
            'par_bond_approximation': is_par_bond_approx,
            'source': 'calculated',
            'settlement_date': actual_settlement,
            'stale': stale,
        }
        if bond_maturity <= pd.Timestamp(actual_settlement):
            return {'macaulay_duration': 0.0, 'years_to_maturity': 0.0, **base}

        bdays = _business_days_252(actual_settlement, bond_maturity, _holidays_arr())
        years_to_maturity = bdays / 252.0

        freq = coupon_freq_map[bond_code] if coupon_freq_map[bond_code] is not None else 1
        macaulay_dur = _macaulay_duration(rate, effective_coupon, years_to_maturity, freq)

        return {'macaulay_duration': macaulay_dur, 'years_to_maturity': years_to_maturity, **base}

    results = {bond_code: _compute(bond_code) for bond_code in codes}

    if single:
        return results[codes[0]]

    columns = ['macaulay_duration', 'years_to_maturity', 'ytm', 'cdi', 'coupon_rate',
               'par_bond_approximation', 'source', 'settlement_date', 'stale']
    df = pd.DataFrame.from_dict(results, orient='index')[columns]
    df.index.name = 'code'
    return df
