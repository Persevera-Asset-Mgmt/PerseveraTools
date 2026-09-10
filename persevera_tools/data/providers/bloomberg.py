from typing import Dict, List, Optional, Union, Literal, Any
import pandas as pd
import numpy as np
from datetime import datetime
import time
import os

from .base import DataProvider, DataRetrievalError
from ..lookups import get_codes, get_securities_by_exchange
from ...config import settings
from ...db.fibery import read_fibery

DATA_PATH = settings.DATA_PATH

_blp_module = None
_blp_import_error: Optional[BaseException] = None


def _get_blp():
    """
    Lazily import ``xbbg.blp``.

    Importing ``xbbg.blp`` triggers a native extension load that requires the
    Bloomberg SDK (``blpapi``) or a running Bloomberg Terminal. Deferring the
    import to first use means that merely importing ``persevera_tools`` (or
    constructing a ``BloombergProvider``) does not fail on machines without
    Bloomberg access - only actually requesting Bloomberg data does.
    """
    global _blp_module, _blp_import_error
    if _blp_module is None:
        if _blp_import_error is not None:
            raise DataRetrievalError(
                "Bloomberg data requires the Bloomberg SDK (blpapi) or a running "
                f"Bloomberg Terminal, which is not available on this machine. "
                f"Original error: {_blp_import_error}"
            ) from _blp_import_error
        try:
            from xbbg import blp as _blp
        except ImportError as e:
            _blp_import_error = e
            raise DataRetrievalError(
                "Bloomberg data requires the Bloomberg SDK (blpapi) or a running "
                f"Bloomberg Terminal, which is not available on this machine. "
                f"Original error: {e}"
            ) from e
        _blp_module = _blp
    return _blp_module


def _bdh(**kwargs):
    """Call ``xbbg.blp.bdh`` with stable pandas / wide defaults.

    xbbg 0.12+ warns that defaults will flip to ``backend='narwhals'`` and
    ``format='long'``. Pinning avoids silent shape changes and the FutureWarning.
    """
    kwargs.setdefault('backend', 'pandas')
    kwargs.setdefault('format', 'wide')
    return _get_blp().bdh(**kwargs)


def _coerce_bdh_dates(dates: pd.Series) -> pd.Series:
    """Normalize bdh date values to ``datetime64``.

    Newer xbbg pipelines sometimes yield YYYYMMDD integers (e.g. ``20200101``).
    Bare ``pd.to_datetime`` treats those as nanoseconds since the epoch, producing
    1970-01-01 timestamps that then fail the provider ``start_date`` filter.
    """
    if pd.api.types.is_datetime64_any_dtype(dates):
        return pd.to_datetime(dates)

    numeric = pd.to_numeric(dates, errors='coerce')
    if numeric.notna().any():
        # YYYYMMDD integers live in a compact range; real epoch-ns values do not.
        yyyymmdd_share = numeric.dropna().between(1_000_0101, 9_999_1231).mean()
        if yyyymmdd_share > 0.9:
            return pd.to_datetime(
                numeric.round().astype('Int64').astype(str),
                format='%Y%m%d',
                errors='coerce',
            )

    parsed = pd.to_datetime(dates, errors='coerce')
    if parsed.isna().all() and len(dates):
        parsed = pd.to_datetime(dates.astype(str), format='%Y%m%d', errors='coerce')
    return parsed


def _map_bloomberg_fields(series: pd.Series, field_list: Dict[str, str]) -> pd.Series:
    """Map Bloomberg field codes to mnemonics, case-insensitively.

    xbbg >=0.12 lowercases historical field names (``PX_LAST`` → ``px_last``)
    for v0.10 compatibility. Fibery mappings stay uppercase, so a plain
    ``Series.map`` would produce null ``field`` values and fail the DB NOT NULL.
    """
    if not field_list:
        return series
    lookup = {str(k).upper(): v for k, v in field_list.items()}
    keys = series.astype('string').str.upper()
    mapped = keys.map(lookup)
    return mapped.where(mapped.notna(), series)

DataCategory = Literal[
    # Market data categories
    'Atividade Bancária', 'Ativos Offshore', 'CFTC', 'Commodity', 'Comérico', 'Crédito', 'Dívida', 'Equity', 'Futuros', 'Governo', 'Inflação', 'Macro', 'Moedas', 'Monetário', 'Setor Externo', 'Taxas', 'Trabalho', 'Varejo', 'Índices',
    # Company data categories
    'Valuation', 'Breadth', 'Opções', 'index_weight'
]

class BloombergProvider(DataProvider):
    """Provider for all Bloomberg data - both market and company data."""
    
    COUNTRY_CURRENCIES = {
        'BZ': 'BRL',
        'US': 'USD',
    }

    _FREQUENCY_MAP = {
        'Diário': 'daily',
        'Trimestral': 'quarterly',
        'Consenso': 'consensus',
    }

    _DEFAULT_MARKET_FIELDS = {'PX_LAST': 'close'}
    _CATEGORY_EXTRA_MARKET_FIELDS: Dict[str, Dict[str, str]] = {
        'Ativos Offshore': {'PX_DIRTY_MID': 'dirty_mid_price'},
    }
    
    def __init__(
        self, 
        start_date: str = '1980-01-01',
        tickers_mapping: Optional[Dict[str, Dict[str, str]]] = None,
        fields_mapping: Optional[Dict[str, Dict[str, str]]] = None,
    ):
        """
        Initialize the Bloomberg data provider.
        
        Args:
            start_date: The start date for data retrieval in 'YYYY-MM-DD' format
            tickers_mapping: Optional custom mapping of Bloomberg tickers to internal codes
                            Format: {'category': {'bloomberg_ticker': 'internal_code', ...}, ...}
            fields_mapping: Optional custom mapping of Bloomberg fields to internal fields
                           Format: {'category': {'bloomberg_field': 'internal_field', ...}, ...}
        """
        super().__init__(start_date)
        self.tickers_mapping = tickers_mapping or {}
        self.fields_mapping = fields_mapping or {}
        self._load_company_field_mappings()
        self._load_indicators_field_mappings()
        
    def _load_indicators_field_mappings(self) -> None:
        """Load all indicators additional fields mappings from Fibery, grouped by category."""
        try:
            df_additional_fields = read_fibery(table_name='Inv-Rsrch-Quant/Campos Adicionais de Indicadores')
            self.indicators_field_mappings = (
                df_additional_fields[['Categoria', 'Name', 'Código']]
                .groupby('Categoria')
                .apply(lambda x: x.set_index('Código')['Name'].to_dict())
                .to_dict()
            )
        except Exception as e:
            raise DataRetrievalError(f"Failed to load indicators additional fields mappings: {str(e)}")

    def _load_company_field_mappings(self) -> None:
        """Load company field mappings from Fibery, grouped by category."""
        try:
            df_factors = read_fibery(table_name='Inv-Rsrch-Quant/Definições dos Fatores')
            base = df_factors[
                (df_factors['state'] == 'Ativo') &
                df_factors['Código Bloomberg'].notna()
            ][['Categoria Independente', 'Código Bloomberg', 'Name', 'Frequência']].rename(
                columns={
                    'Categoria Independente': 'category',
                    'Código Bloomberg': 'bloomberg_code',
                    'Name': 'mnemonic',
                    'Frequência': 'frequency',
                }
            )
            base['frequency'] = base['frequency'].map(self._FREQUENCY_MAP)

            self.field_mappings = base.groupby('category').apply(
                lambda x: x.set_index('bloomberg_code')['mnemonic'].to_dict()
            ).to_dict()
            self.frequencies = base.groupby('category')['frequency'].first().to_dict()

            # ANNOUNCEMENT_DT has no Bloomberg code in Fibery but is required
            # for quarterly date adjustment in _adjust_quarterly_dates.
            for category, frequency in self.frequencies.items():
                if frequency == 'quarterly':
                    self.field_mappings[category]['ANNOUNCEMENT_DT'] = 'ANNOUNCEMENT_DT'
        except Exception as e:
            raise DataRetrievalError(f"Failed to load field mappings: {str(e)}")

    def get_data(
        self,
        category: str,
        data_type: Literal['market', 'company'] = 'market',
        additional_fields: Optional[str] = None,
        exchanges: Optional[List[str]] = None,
        best_fperiod_override: Optional[str] = None,
        use_fund_currency: bool = False,
        index_list: Optional[List[str]] = None,
        custom_tickers: Optional[Dict[str, str]] = None,
        custom_fields: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        Retrieve data from Bloomberg.
        
        Args:
            category: The category of data to retrieve
            data_type: Whether to retrieve market or company data
            additional_fields: Optional name of additional fields to retrieve
            exchanges: List of exchanges for company data
            best_fperiod_override: Optional override for BEST_FPERIOD parameter
            use_fund_currency: Whether to use local currency for each exchange
            index_list: List of indices for index weight calculations
            custom_tickers: Optional mapping of Bloomberg tickers to internal codes for this call
            custom_fields: Optional mapping of Bloomberg fields to internal fields for this call
            **kwargs: Additional arguments passed to Bloomberg API
            
        Returns:
            DataFrame with columns: ['date', 'code', 'field', 'value']
        """
        self._log_processing(f"{data_type} data - {category}")
        
        if data_type == 'market':
            return self._get_market_data(
                category=category,
                additional_fields=additional_fields,
                best_fperiod_override=best_fperiod_override,
                custom_tickers=custom_tickers,
                custom_fields=custom_fields,
                **kwargs
            )
        else:
            return self._get_company_data(
                category=category,
                exchanges=exchanges,
                best_fperiod_override=best_fperiod_override,
                use_fund_currency=use_fund_currency,
                index_list=index_list,
                custom_tickers=custom_tickers,
                custom_fields=custom_fields,
                **kwargs
            )
    
    @staticmethod
    def _bdh_to_long(
        df: Union[pd.DataFrame, pd.Series],
        tickers: List[str],
        fields: List[str],
    ) -> pd.DataFrame:
        """
        Normalize xbbg ``bdh`` output to ``[date, field, code_bloomberg, value]``.

        Column layout from Bloomberg/xbbg varies with the number of tickers and
        fields: MultiIndex ``(ticker, field)``, ticker-only columns (single field),
        field-only columns (single ticker), xbbg ``format='long'`` /
        ``semi_long``, or a plain Series. Blind ``stack().stack()`` raises
        ``AttributeError: 'Series' object has no attribute 'stack'`` whenever
        the first stack already yields a Series.
        """
        empty = pd.DataFrame(columns=['date', 'field', 'code_bloomberg', 'value'])
        if df is None or (hasattr(df, 'empty') and df.empty):
            return empty

        # Narwhals / non-pandas frames from newer xbbg backends.
        if not isinstance(df, (pd.DataFrame, pd.Series)):
            to_pandas = getattr(df, 'to_pandas', None)
            if callable(to_pandas):
                df = to_pandas()
            else:
                df = pd.DataFrame(df)

        if isinstance(df, pd.Series):
            out = df.rename('value').reset_index()
            out.columns = ['date', 'value']
            out['code_bloomberg'] = tickers[0] if tickers else df.name
            out['field'] = fields[0] if fields else 'PX_LAST'
            out['date'] = _coerce_bdh_dates(out['date'])
            return out[['date', 'field', 'code_bloomberg', 'value']]

        cols_lower = {str(c).lower(): c for c in df.columns}
        ticker_key = next(
            (k for k in ('ticker', 'code_bloomberg', 'bloomberg_code') if k in cols_lower),
            None,
        )
        if ticker_key and 'field' in cols_lower and 'value' in cols_lower:
            # xbbg format='long' (and similar tidy layouts)
            date_col = cols_lower.get('date')
            if date_col is None and not isinstance(df.index, pd.DatetimeIndex):
                raise DataRetrievalError(
                    "Bloomberg long-format bdh has no date column or DatetimeIndex"
                )
            out = pd.DataFrame({
                'date': df[date_col] if date_col is not None else df.index,
                'field': df[cols_lower['field']],
                'code_bloomberg': df[cols_lower[ticker_key]],
                'value': df[cols_lower['value']],
            })
            out['date'] = _coerce_bdh_dates(out['date'])
            return out[['date', 'field', 'code_bloomberg', 'value']]

        if ticker_key and 'date' in cols_lower:
            # xbbg format='semi_long': ticker + date + one column per field
            id_cols = [cols_lower[ticker_key], cols_lower['date']]
            value_vars = [c for c in df.columns if c not in id_cols]
            melted = pd.melt(
                df,
                id_vars=id_cols,
                value_vars=value_vars,
                var_name='field',
                value_name='value',
            )
            melted = melted.rename(
                columns={cols_lower[ticker_key]: 'code_bloomberg', cols_lower['date']: 'date'}
            )
            melted['date'] = _coerce_bdh_dates(melted['date'])
            return melted[['date', 'field', 'code_bloomberg', 'value']]

        if isinstance(df.columns, pd.MultiIndex):
            stacked: Union[pd.DataFrame, pd.Series] = df
            for _ in range(df.columns.nlevels):
                try:
                    stacked = stacked.stack(future_stack=True)
                except TypeError:
                    stacked = stacked.stack()
            out = stacked.rename('value').reset_index()
            # After stacking both MultiIndex levels (ticker, field) last-first,
            # typical order is date / field / ticker.
            if out.shape[1] == 4:
                out.columns = ['date', 'field', 'code_bloomberg', 'value']
                # Swap if the second level looks like tickers rather than fields.
                sample = {str(v).upper() for v in out['field'].dropna().unique()[:50]}
                field_set = {str(f).upper() for f in fields}
                ticker_set = {str(t).upper() for t in tickers}
                if sample and sample <= ticker_set and not sample <= field_set:
                    out = out[['date', 'code_bloomberg', 'field', 'value']]
                    out.columns = ['date', 'field', 'code_bloomberg', 'value']
                out['date'] = _coerce_bdh_dates(out['date'])
                return out[['date', 'field', 'code_bloomberg', 'value']]
            raise DataRetrievalError(
                f"Unexpected MultiIndex bdh shape after stack: {list(out.columns)}"
            )

        try:
            stacked = df.stack(future_stack=True).rename('value').reset_index()
        except TypeError:
            stacked = df.stack().rename('value').reset_index()
        stacked.columns = ['date', '_level', 'value']

        col_set = {str(c).upper() for c in df.columns}
        field_set = {str(f).upper() for f in fields}
        ticker_set = {str(t).upper() for t in tickers}

        if col_set <= field_set:
            stacked = stacked.rename(columns={'_level': 'field'})
            ticker = df.columns.name or (tickers[0] if len(tickers) == 1 else None)
            if ticker is None:
                raise DataRetrievalError(
                    "Bloomberg returned field columns without a ticker identifier"
                )
            stacked['code_bloomberg'] = ticker
        elif col_set <= ticker_set or len(fields) == 1:
            stacked = stacked.rename(columns={'_level': 'code_bloomberg'})
            stacked['field'] = fields[0] if fields else 'PX_LAST'
        elif len(tickers) == 1:
            stacked = stacked.rename(columns={'_level': 'field'})
            stacked['code_bloomberg'] = tickers[0]
        else:
            raise DataRetrievalError(
                f"Cannot interpret Bloomberg bdh columns: {list(df.columns)[:10]}"
            )

        stacked['date'] = _coerce_bdh_dates(stacked['date'])
        return stacked[['date', 'field', 'code_bloomberg', 'value']]

    def _get_market_data(
        self,
        category: str,
        additional_fields: Optional[str] = None,
        best_fperiod_override: Optional[str] = None,
        custom_tickers: Optional[Dict[str, str]] = None,
        custom_fields: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """Get market data from Bloomberg."""
        # Use custom tickers if provided, otherwise use the mapping from the category
        # or fall back to the lookup function
        if custom_tickers:
            securities_list = custom_tickers
        elif category in self.tickers_mapping:
            securities_list = self.tickers_mapping[category]
        else:
            df_securities = read_fibery(table_name='Inv-Rsrch-Quant/Indicadores')
            df_securities = df_securities[
                (df_securities['Fonte'] == 'Bloomberg') &
                (df_securities['Categoria'] == category)
            ][['Name', 'Código']]
            securities_list = df_securities.set_index('Código')['Name'].to_dict()

        if not securities_list:
            self.logger.warning(
                f"No Bloomberg securities found for category '{category}'. "
                "Check that the category name matches the 'Categoria' field in Fibery "
                "(Inv-Rsrch-Quant/Indicadores) or supply custom_tickers."
            )
            return pd.DataFrame(columns=['date', 'code', 'field', 'value'])

        if additional_fields:
            if custom_fields:
                field_list = custom_fields
            elif additional_fields in self.fields_mapping:
                field_list = self.fields_mapping[additional_fields]
            elif additional_fields in self.indicators_field_mappings:
                field_list = self.indicators_field_mappings[additional_fields]
            else:
                raise ValueError(f"Unknown additional fields: '{additional_fields}'. "
                                 f"Available: {list(self.indicators_field_mappings.keys())}")

            api_kwargs = {
                'tickers': list(securities_list.keys()),
                'flds': list(field_list.keys()),
                'start_date': self.start_date,
                **kwargs
            }
            if best_fperiod_override:
                api_kwargs['BEST_FPERIOD_OVERRIDE'] = best_fperiod_override

            raw = _bdh(**api_kwargs)
            df = self._bdh_to_long(
                raw, tickers=list(securities_list.keys()), fields=list(field_list.keys())
            )
            df['code'] = df['code_bloomberg'].map(securities_list)
            df['field'] = _map_bloomberg_fields(df['field'], field_list)
            df = df.drop(columns='code_bloomberg')
        else:
            field_mapping = {
                **self._DEFAULT_MARKET_FIELDS,
                **self._CATEGORY_EXTRA_MARKET_FIELDS.get(category, {}),
            }

            raw = _bdh(
                tickers=list(securities_list.keys()),
                flds=list(field_mapping.keys()),
                start_date=self.start_date,
                **kwargs
            )
            df = self._bdh_to_long(
                raw,
                tickers=list(securities_list.keys()),
                fields=list(field_mapping.keys()),
            )
            df['code'] = df['code_bloomberg'].map(securities_list)
            df['field'] = _map_bloomberg_fields(df['field'], field_mapping)
            df = df.drop(columns='code_bloomberg')
            
        if category == "macro":
            df = self._process_breakeven_rates(df)
            
        return self._validate_output(df)
    
    def _get_company_data(
        self,
        category: str,
        exchanges: Optional[List[str]] = None,
        best_fperiod_override: Optional[str] = None,
        use_fund_currency: Optional[str] = None,
        index_list: Optional[List[str]] = None,
        custom_tickers: Optional[Dict[str, str]] = None,
        custom_fields: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """Get company-specific data from Bloomberg."""
        if exchanges is None:
            exchanges = ['BZ', 'US']
            
        # Use custom fields if provided, otherwise use the mapping from the category
        if custom_fields:
            field_list = custom_fields
        else:
            field_list = self.field_mappings.get(category)
            if not field_list:
                raise ValueError(f"Unknown category: {category}")
            
        frequency = self.frequencies.get(category)
        
        all_data = []
        for exchange in exchanges:
            self.logger.info(f"Processing exchange: {exchange}")
            
            # Use custom tickers if provided, otherwise get from exchange
            if custom_tickers:
                securities_list = custom_tickers
            else:
                securities_list = get_securities_by_exchange(exchange=exchange)
                
            self.logger.info(f"{category.upper()}: {len(securities_list)} securities found")
            
            if not securities_list:
                self.logger.warning(f"No securities found for exchange {exchange}")
                continue
                
            try:
                if category == 'index_weight' and index_list:
                    df = self._get_index_weight_data(
                        securities_list=securities_list,
                        field_list=field_list,
                        index_list=index_list
                    )
                else:
                    df = self._get_regular_company_data(
                        securities_list=securities_list,
                        field_list=field_list,
                        frequency=frequency,
                        exchange=exchange if use_fund_currency else None,
                        best_fperiod_override=best_fperiod_override,
                        **kwargs
                    )
                    
                all_data.append(df)
                
            except Exception as e:
                self.logger.error(f"Error processing {exchange}: {str(e)}")
                continue
                
        if not all_data:
            raise DataRetrievalError("No data retrieved from any exchange")
            
        final_df = pd.concat(all_data, ignore_index=True)
        return self._validate_output(final_df)
    
    def _get_index_weight_data(
        self,
        securities_list: Dict[str, str],
        field_list: Dict[str, str],
        index_list: List[str]
    ) -> pd.DataFrame:
        """Get index weight data for multiple indices."""
        all_data = []
        
        for index_rel in index_list:
            self.logger.info(f"Downloading members of {index_rel}...")
            try:
                raw = _bdh(
                    tickers=list(securities_list.keys()),
                    flds=list(field_list.keys()),
                    start_date=self.start_date,
                    REL_INDEX=index_rel,
                )
                
                df = self._bdh_to_long(
                    raw,
                    tickers=list(securities_list.keys()),
                    fields=list(field_list.keys()),
                )
                df['code'] = df['code_bloomberg'].map(securities_list)
                df['field'] = _map_bloomberg_fields(df['field'], field_list) + '_' + index_rel.lower()
                df = df.drop(columns='code_bloomberg')
                
                all_data.append(df)
                time.sleep(5)  # Rate limiting
                
            except Exception as e:
                self.logger.error(f"Error processing index {index_rel}: {str(e)}")
                continue
                
        return pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()
    
    def _get_regular_company_data(
        self,
        securities_list: Dict[str, str],
        field_list: Dict[str, str],
        frequency: str,
        exchange: Optional[str] = None,
        best_fperiod_override: Optional[str] = None,
        **kwargs
    ) -> pd.DataFrame:
        """Get regular company data."""
        tickers = list(securities_list.keys())
        fields = list(field_list.keys())
        api_kwargs = {
            'tickers': tickers,
            'flds': fields,
            'start_date': self.start_date,
            **kwargs
        }
        
        if exchange:
            api_kwargs['EQY_FUND_CRNCY'] = self.COUNTRY_CURRENCIES[exchange]
        if best_fperiod_override:
            api_kwargs['BEST_FPERIOD_OVERRIDE'] = best_fperiod_override
        if frequency == 'quarterly':
            api_kwargs['FILING_STATUS'] = 'OR'
            
        raw = _bdh(**api_kwargs)
        df = self._bdh_to_long(raw, tickers=tickers, fields=fields)

        if df.empty:
            self.logger.warning(
                f"Bloomberg bdh returned no rows "
                f"(tickers={len(tickers)}, fields={len(fields)}, exchange={exchange})"
            )
            return pd.DataFrame(columns=['code', 'date', 'field', 'value'])

        if frequency == 'quarterly':
            df = self._adjust_quarterly_dates(df)

        n_raw = len(df)
        sample_tickers = df['code_bloomberg'].dropna().astype(str).unique()[:3].tolist()
        sample_fields = df['field'].dropna().astype(str).unique()[:5].tolist()
        null_codes = df['code_bloomberg'].map(securities_list).isna().sum()

        df['code'] = df['code_bloomberg'].map(securities_list)
        df['field'] = _map_bloomberg_fields(df['field'], field_list)
        df = df.drop(columns='code_bloomberg')

        out = df[['code', 'date', 'field', 'value']].dropna()
        if out.empty and n_raw > 0:
            self.logger.warning(
                f"All {n_raw} Bloomberg rows dropped after mapping "
                f"(unmapped codes={null_codes}, null values={df['value'].isna().sum()}, "
                f"exchange={exchange}, sample_tickers={sample_tickers}, "
                f"sample_fields={sample_fields})"
            )
        return out
    
    def _process_breakeven_rates(self, df: pd.DataFrame) -> pd.DataFrame:
        """Process macro data to calculate breakeven rates."""
        for vertice in ['_1y', '_2y', '_3y', '_5y', '_10y']:
            temp = (
                df
                .pivot_table(index='date', columns='code', values='value')
                .eval(f"((1 + br_pre{vertice}/100) / (1 + br_ipca{vertice}/100) - 1) * 100")
                .dropna()
            )
            temp = temp.reset_index()
            temp['code'] = f'br_breakeven{vertice}'
            temp.columns = ['date', 'value', 'code']
            temp = temp.assign(field='close')
            df = pd.concat([df, temp], ignore_index=True)
        
        return df
    
    def _adjust_quarterly_dates(self, df: pd.DataFrame) -> pd.DataFrame:
        """Adjust observation dates using ``ANNOUNCEMENT_DT`` (long-format bdh).

        Expects columns ``date``, ``field``, ``code_bloomberg``, ``value``.
        Announcement rows are consumed for the calendar and dropped from output,
        matching the previous wide-format behavior.
        """
        keys = ['date', 'code_bloomberg']
        # xbbg lowercases field names (announcement_dt); compare case-insensitively.
        is_announcement = df['field'].astype('string').str.upper() == 'ANNOUNCEMENT_DT'
        ann = (
            df.loc[is_announcement, keys + ['value']]
            .assign(
                ANNOUNCEMENT_DT=lambda x: pd.to_datetime(
                    x['value'], format='%Y%m%d', errors='coerce'
                )
            )[keys + ['ANNOUNCEMENT_DT']]
            .drop_duplicates(keys)
        )
        calendar = (
            df[keys]
            .drop_duplicates()
            .merge(ann, on=keys, how='left')
            .sort_values(['code_bloomberg', 'date'])
        )
        calendar['date_adj'] = calendar['ANNOUNCEMENT_DT'].fillna(calendar['date'])
        calendar['date_dif'] = calendar.groupby('code_bloomberg')['date_adj'].diff(1)
        calendar['date_adj'] = np.where(
            calendar['date_dif'].dt.days < 0, calendar['date'], calendar['date_adj']
        )

        out = (
            df.loc[~is_announcement]
            .merge(calendar[keys + ['date_adj']], on=keys, how='left')
        )
        out['date'] = out['date_adj'].fillna(out['date'])
        return out.drop(columns=['date_adj']) 