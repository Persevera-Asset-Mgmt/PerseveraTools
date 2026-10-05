from typing import Optional, Dict, List, Union, Literal, Any
import pandas as pd
import logging

from .providers.bloomberg import BloombergProvider, DataCategory
from .providers.sgs import SGSProvider
from .providers.fred import FredProvider
from .providers.sidra import SidraProvider
from .providers.anbima import AnbimaProvider
from .providers.anbima_feed import AnbimaFeedProvider, AnbimaFundosProvider
from .providers.cvm import CVMProvider
from .providers.bcb_focus import BcbFocusProvider
from .providers.simplify import SimplifyProvider
from .providers.invesco import InvescoProvider
from .providers.kraneshares import KraneSharesProvider
from .providers.investing_com import InvestingComProvider
from .providers.debentures_com import DebenturesComProvider
from .providers.mdic import MDICProvider
from .providers.b3 import B3Provider
from .providers.mais_retorno import MaisRetornoProvider
from .providers.investfy import InvestfyProvider
from ..db.operations import to_sql

logger = logging.getLogger(__name__)


class FinancialDataService:
    """High-level interface for financial data retrieval and storage from multiple sources."""
    
    def __init__(
        self,
        start_date: str = '1980-01-01',
        fred_api_key: Optional[str] = None,
        bloomberg_tickers_mapping: Optional[Dict[str, Dict[str, str]]] = None,
        bloomberg_fields_mapping: Optional[Dict[str, Dict[str, str]]] = None,
    ):
        """
        Initialize the financial data service.
        
        Args:
            start_date: The start date for data retrieval
            fred_api_key: Optional API key for FRED
            bloomberg_tickers_mapping: Optional custom mapping of Bloomberg tickers to internal codes
            bloomberg_fields_mapping: Optional custom mapping of Bloomberg fields to internal fields
        """
        self.start_date = start_date
        self.bloomberg = BloombergProvider(
            start_date=start_date,
            tickers_mapping=bloomberg_tickers_mapping,
            fields_mapping=bloomberg_fields_mapping,
        )
        self.sgs = SGSProvider(start_date=start_date)
        self.fred = FredProvider(start_date=start_date)
        self.sidra = SidraProvider(start_date=start_date)
        self.anbima = AnbimaProvider(start_date=start_date)
        self.anbima_feed = AnbimaFeedProvider(start_date=start_date)
        self.anbima_fundos = AnbimaFundosProvider(start_date=start_date)
        self.cvm = CVMProvider(start_date=start_date)
        self.bcb_focus = BcbFocusProvider(start_date=start_date)
        self.simplify = SimplifyProvider(start_date=start_date)
        self.invesco = InvescoProvider(start_date=start_date)
        self.kraneshares = KraneSharesProvider(start_date=start_date)
        self.investing_com = InvestingComProvider()
        self.debentures_com = DebenturesComProvider()
        self.mdic = MDICProvider()
        self.b3 = B3Provider()
        self.mais_retorno = MaisRetornoProvider()
        self.investfy = InvestfyProvider(start_date=start_date)
        self.logger = logging.getLogger(self.__class__.__name__)
        
    def get_bloomberg_data(
        self,
        category: DataCategory,
        data_type: Literal['market', 'company'] = 'market',
        additional_fields: Optional[str] = None,
        exchanges: Optional[List[str]] = None,
        best_fperiod_override: Optional[str] = None,
        use_fund_currency: Optional[str] = None,
        index_list: Optional[List[str]] = None,
        custom_tickers: Optional[Dict[str, str]] = None,
        custom_fields: Optional[Dict[str, str]] = None,
        save_to_db: bool = True,
        retry_attempts: int = 3,
        table_name: Optional[str] = None
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
            save_to_db: Whether to save the data to the database
            retry_attempts: Number of retry attempts for Bloomberg API calls
            table_name: Optional custom table name for database storage
            
        Returns:
            DataFrame with columns: ['date', 'code', 'field', 'value']
            
        Raises:
            ValueError: If category or additional_fields are invalid
            RuntimeError: If data retrieval fails after all retry attempts
        """
        self.logger.info(f"Retrieving {category} {data_type} data from Bloomberg" + 
                        (f" with {additional_fields}" if additional_fields else ""))
        
        # Retry only the Bloomberg download; a database failure is raised as-is
        # instead of triggering a full re-download.
        attempt = 0
        last_error = None
        df = None

        while attempt < retry_attempts:
            try:
                df = self.bloomberg.get_data(
                    category=category,
                    data_type=data_type,
                    additional_fields=additional_fields,
                    exchanges=exchanges,
                    best_fperiod_override=best_fperiod_override,
                    use_fund_currency=use_fund_currency,
                    index_list=index_list,
                    custom_tickers=custom_tickers,
                    custom_fields=custom_fields
                )
                break
            except Exception as e:
                attempt += 1
                last_error = e
                self.logger.warning(f"Attempt {attempt} failed: {str(e)}")
                if attempt < retry_attempts:
                    self.logger.info(f"Retrying... ({attempt}/{retry_attempts})")

        if df is None:
            error_msg = f"Failed to retrieve data after {retry_attempts} attempts. Last error: {str(last_error)}"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg) from last_error

        if df.empty:
            self.logger.warning(f"No data retrieved for {category}")
            return df

        if save_to_db:
            db_table = table_name or ('indicadores' if data_type == 'market' else 'factor_zoo')
            self.logger.info(f"Saving {len(df)} rows to '{db_table}'")
            try:
                df = self._save_to_db(df, db_table, ['code', 'date', 'field'])
            except Exception as e:
                self.logger.error(f"Failed to save data to database: {str(e)}")
                raise

        return df
    
    def get_cvm_data(
        self,
        source: str = 'cvm',
        cnpjs: Optional[List[str]] = None,
        save_to_db: bool = True,
        retry_attempts: int = 3,
        table_name: str = 'fundos_cvm'
    ) -> pd.DataFrame:
        """
        Retrieve daily fund data from CVM.
        
        Args:
            cnpjs: List of fund CNPJs to retrieve.
            save_to_db: Whether to save the data to the database.
            retry_attempts: Number of retry attempts.
            table_name: The database table name to store CVM data.
            
        Returns:
            DataFrame with CVM data.
        """
        self.logger.info(f"Retrieving data from CVM" + (f" for {len(cnpjs)} CNPJs" if cnpjs else ""))
        
        attempt = 0
        last_error = None
        
        while attempt < retry_attempts:
            try:
                df = self.cvm.get_data(category=source, cnpjs=cnpjs)
                
                if df.empty:
                    self.logger.warning(f"No data retrieved from CVM")
                    return df
                
                if save_to_db:
                    self.logger.info(f"Saving {len(df)} rows to '{table_name}'")
                    try:
                        df = self._save_to_db(df, table_name, ['fund_cnpj', 'date'])
                    except Exception as e:
                        self.logger.error(f"Failed to save data to database: {str(e)}")
                        raise
                
                return df
                
            except Exception as e:
                attempt += 1
                last_error = e
                self.logger.warning(f"Attempt {attempt} failed: {str(e)}")
                if attempt < retry_attempts:
                    self.logger.info(f"Retrying... ({attempt}/{retry_attempts})")
        
        error_msg = f"Failed to retrieve data from CVM after {retry_attempts} attempts. Last error: {str(last_error)}"
        self.logger.error(error_msg)
        raise RuntimeError(error_msg)

    def get_anbima_fundos_serie_historica(
        self,
        cnpjs: List[str],
        tipo_fundo: Optional[str] = None,
        save_to_db: bool = True,
        retry_attempts: int = 3,
        table_name: str = 'fundos_cvm',
    ) -> pd.DataFrame:
        """
        Retrieve historical NAV and AUM time series from ANBIMA Fundos v2 API
        for one or more funds identified by CNPJ.

        The date range starts at ``start_date`` (set at service initialisation)
        and extends to the most recent available data. Automatic date-range
        pagination is handled internally — there is no practical limit on the
        number of records returned.

        Args:
            cnpjs: List of fund CNPJs (formatted as ``"XX.XXX.XXX/XXXX-XX"``
                or 14 raw digits). ANBIMA fund/class/subclass codes (F…/C…/S…)
                are also accepted.
            tipo_fundo: Fund type filter (``"FIF"``, ``"FII"``, ``"FIP"``,
                ``"FIDC"``, ``"FIAGRO"``, ``"ETF"``, ``"OFFSHORE"``).
                When supplied, significantly reduces the number of pages
                scanned to resolve each CNPJ.
            save_to_db: Whether to upsert the result into the database.
            retry_attempts: Number of retry attempts on failure.
            table_name: Target database table name.

        Returns:
            DataFrame with columns including ``cnpj_fundo``,
            ``codigo_fundo``, ``codigo_classe``, ``data_competencia``,
            ``valor_cota``, ``patrimonio_liquido``, and others returned
            by the ANBIMA API.
        """
        self.logger.info(
            "Retrieving ANBIMA Fundos series for %d CNPJ(s)%s",
            len(cnpjs),
            f" from {self.start_date}" if self.start_date else "",
        )

        if not cnpjs:
            self.logger.warning("Empty CNPJ list — nothing to retrieve from ANBIMA Fundos")
            return pd.DataFrame(columns=[
                "fund_cnpj", "date", "fund_nav", "fund_total_equity",
                "fund_inflows", "fund_outflows", "fund_holders", "fund_total_value",
            ])

        kwargs: Dict[str, Any] = {"data_inicio": self.start_date}
        if tipo_fundo:
            kwargs["tipo_fundo"] = tipo_fundo

        attempt = 0
        last_error = None
        cols = {
            "cnpj_fundo": "fund_cnpj",
            "data_competencia": "date",
            "valor_cota": "fund_nav",
            "valor_patrimonio_liquido": "fund_total_equity",
            "valor_volume_total_aplicacoes": "fund_inflows",
            "valor_volume_total_resgates": "fund_outflows",
            "numero_cotistas": "fund_holders",
        }

        while attempt < retry_attempts:
            try:
                df = self.anbima_fundos.get_series_historicas(cnpjs, **kwargs)

                if df.empty:
                    self.logger.warning("No data retrieved from ANBIMA Fundos")
                    return pd.DataFrame(columns=list(cols.values()) + ["fund_total_value"])

                missing = [c for c in cols if c not in df.columns]
                if missing:
                    raise KeyError(
                        f"ANBIMA Fundos response missing expected columns: {missing}. "
                        f"Available: {df.columns.tolist()}"
                    )

                df = df[list(cols.keys())].rename(columns=cols)
                df["fund_total_value"] = df["fund_total_equity"]
                df["date"] = pd.to_datetime(df["date"], errors="coerce")

                if save_to_db:
                    self.logger.info("Saving %d rows to '%s'", len(df), table_name)
                    try:
                        df = self._save_to_db(df, table_name, ["fund_cnpj", "date"])
                    except Exception as e:
                        self.logger.error("Failed to save ANBIMA Fundos data: %s", e)
                        raise

                return df

            except Exception as e:
                attempt += 1
                last_error = e
                self.logger.warning("Attempt %d failed: %s", attempt, e)
                if attempt < retry_attempts:
                    self.logger.info("Retrying... (%d/%d)", attempt, retry_attempts)

        error_msg = (
            f"Failed to retrieve ANBIMA Fundos data after {retry_attempts} attempts. "
            f"Last error: {last_error}"
        )
        self.logger.error(error_msg)
        raise RuntimeError(error_msg)

    def get_investing_calendar_data(
        self,
        save_to_db: bool = False,
        retry_attempts: int = 3,
        table_name: str = 'economic_calendar'
    ) -> pd.DataFrame:
        """
        Retrieve economic calendar data from Investing.com.
        
        Args:
            save_to_db: Whether to save the data to the database.
            retry_attempts: Number of retry attempts.
            table_name: The database table name to store economic calendar data.
            
        Returns:
            DataFrame with economic calendar data.
        """
        self.logger.info(f"Retrieving economic calendar from Investing.com")
        
        attempt = 0
        last_error = None
        
        while attempt < retry_attempts:
            try:
                df = self.investing_com.get_data(category='economic_calendar')
                
                if df.empty:
                    self.logger.warning(f"No data retrieved from Investing.com")
                    return df
                
                if save_to_db:
                    self.logger.info(f"Saving {len(df)} rows to '{table_name}'")
                    try:
                        df = self._save_to_db(df, table_name, ['date', 'event_id'])
                    except Exception as e:
                        self.logger.error(f"Failed to save data to database: {str(e)}")
                        raise
                
                return df
                
            except Exception as e:
                attempt += 1
                last_error = e
                self.logger.warning(f"Attempt {attempt} failed: {str(e)}")
                if attempt < retry_attempts:
                    self.logger.info(f"Retrying... ({attempt}/{retry_attempts})")
        
        error_msg = f"Failed to retrieve data from Investing.com after {retry_attempts} attempts. Last error: {str(last_error)}"
        self.logger.error(error_msg)
        raise RuntimeError(error_msg)

    def get_data(
        self,
        source: Literal[
            'sgs', 'fred', 'sidra', 'debentures_com',
            'anbima_indices', 'anbima_debentures', 'anbima_titulos_publicos', 'anbima_cri_cra',
            'anbima_feed_titulos_publicos_mercado_secundario', 'anbima_feed_titulos_publicos_vna',
            'anbima_feed_titulos_publicos_curvas_juros', 'anbima_feed_debentures_mercado_secundario',
            'anbima_feed_debentures_curvas_credito', 'anbima_feed_debentures_mais_mercado_secundario',
            'anbima_feed_cri_cra_mercado_secundario',
            'anbima_feed_fidc_mercado_secundario', 'anbima_feed_indices_resultados_ihfa_fechado',
            'anbima_feed_indices_resultados_ima', 'anbima_feed_indices_resultados_idka',
            'anbima_fundos_lista', 'anbima_fundos_instituicoes',
            'anbima_fundos_lote_dados_cadastrais', 'anbima_fundos_lote_serie_historica',
            'simplify', 'invesco', 'bcb_focus', 'kraneshares', 'mdic',
            'b3_investor_flow', 'b3_bdi',
            'mais_retorno_debentures', 'mais_retorno_fundos',
            'investfy_investor_flow',
        ],
        save_to_db: bool = True,
        retry_attempts: int = 3,
        table_name: Optional[str] = None,
        primary_keys: Optional[List[str]] = None,
        **kwargs
    ) -> pd.DataFrame:
        """
        Retrieve data from various sources.

        For ANBIMA Fundos time series by CNPJ, prefer
        :meth:`get_anbima_fundos_serie_historica` which handles automatic
        pagination and CNPJ resolution.

        Args:
            source: The data source to use.
            save_to_db: Whether to save the data to the database.
            retry_attempts: Number of retry attempts.
            table_name: Optional custom table name for database storage.
            primary_keys: Optional list of primary keys for the database table.
            **kwargs: Additional arguments passed to the specific provider.

        Returns:
            DataFrame with the data returned by the provider.
        """
        self.logger.info(f"Retrieving data from {source}")

        # Map of sources to providers and default table names
        providers = {
            'sgs': (self.sgs, 'indicadores'),
            'fred': (self.fred, 'indicadores'),
            'sidra': (self.sidra, 'indicadores'),
            'debentures_com': (self.debentures_com, 'credito_privado_emissoes'),
            'anbima_indices': (self.anbima, 'indicadores'),
            'anbima_debentures': (self.anbima, 'credito_privado_historico'),
            'anbima_titulos_publicos': (self.anbima, 'anbima_titulos_publicos_historico'),
            'anbima_cri_cra': (self.anbima, 'credito_privado_historico'),
            # ANBIMA Feed (OAuth2) – preços e índices
            'anbima_feed_titulos_publicos_mercado_secundario': (self.anbima_feed, 'anbima_titulos_publicos_historico'),
            'anbima_feed_titulos_publicos_vna': (self.anbima_feed, 'indicadores'),
            'anbima_feed_titulos_publicos_curvas_juros': (self.anbima_feed, 'indicadores'),
            'anbima_feed_debentures_mercado_secundario': (self.anbima_feed, 'credito_privado_historico'),
            'anbima_feed_debentures_curvas_credito': (self.anbima_feed, 'credito_privado_historico'),
            'anbima_feed_debentures_mais_mercado_secundario': (self.anbima_feed, 'credito_privado_historico'),
            'anbima_feed_cri_cra_mercado_secundario': (self.anbima_feed, 'credito_privado_historico'),
            'anbima_feed_fidc_mercado_secundario': (self.anbima_feed, 'credito_privado_historico'),
            'anbima_feed_indices_resultados_ihfa_fechado': (self.anbima_feed, 'indicadores'),
            'anbima_feed_indices_resultados_ima': (self.anbima_feed, 'indicadores'),
            'anbima_feed_indices_resultados_idka': (self.anbima_feed, 'indicadores'),
            # ANBIMA Fundos v2 – endpoints de lista/lote (sem CNPJ por rota)
            'anbima_fundos_lista': (self.anbima_fundos, 'fundos_anbima_cadastro'),
            'anbima_fundos_instituicoes': (self.anbima_fundos, 'fundos_anbima_cadastro'),
            'anbima_fundos_lote_dados_cadastrais': (self.anbima_fundos, 'fundos_anbima_cadastro'),
            'anbima_fundos_lote_serie_historica': (self.anbima_fundos, 'fundos_anbima'),
            'bcb_focus': (self.bcb_focus, 'indicadores'),
            'simplify': (self.simplify, 'indicadores'),
            'invesco': (self.invesco, 'indicadores'),
            'kraneshares': (self.kraneshares, 'indicadores'),
            'mdic': (self.mdic, 'indicadores'),
            'b3_investor_flow': (self.b3, 'indicadores'),
            'b3_bdi': (self.b3, 'credito_privado_historico'),
            'mais_retorno_debentures': (self.mais_retorno, 'credito_privado_historico'),
            'mais_retorno_fundos': (self.mais_retorno, 'fundos_cvm'),
            'investfy_investor_flow': (self.investfy, 'indicadores'),
        }
        
        if source not in providers:
            raise ValueError(f"Unknown source: {source}")

        # Per-table default primary keys.
        # credito_privado_historico includes 'source' because multiple providers share
        # the table and may produce identical (code, date, field) for different sources.
        # anbima_titulos_publicos_historico includes 'maturity' because the same bond
        # (code) can have different prices on the same date depending on its maturity.
        table_primary_keys = {
            'credito_privado_historico': ['code', 'date', 'field', 'source'],
            'anbima_titulos_publicos_historico': ['code', 'date', 'maturity', 'field'],
        }

        provider, default_table = providers[source]
        default_primary_keys = table_primary_keys.get(
            table_name or default_table, ['code', 'date', 'field']
        )
        
        attempt = 0
        last_error = None
        
        while attempt < retry_attempts:
            try:
                df = provider.get_data(category=source, **kwargs)
                if 'value' in df.columns: df = df.dropna(subset=['value'])
                
                if df.empty:
                    self.logger.warning(f"No data retrieved from {source}")
                    return df
                
                if save_to_db:
                    db_table = table_name or default_table
                    effective_keys = primary_keys or default_primary_keys
                    self.logger.info(f"Saving {len(df)} rows to '{db_table}'")
                    try:
                        df = self._save_to_db(df, db_table, effective_keys)
                    except Exception as e:
                        self.logger.error(f"Failed to save data to database: {str(e)}")
                        raise
                
                return df
                
            except Exception as e:
                attempt += 1
                last_error = e
                self.logger.warning(f"Attempt {attempt} failed: {str(e)}")
                if attempt < retry_attempts:
                    self.logger.info(f"Retrying... ({attempt}/{retry_attempts})")
        
        error_msg = f"Failed to retrieve data from {source} after {retry_attempts} attempts. Last error: {str(last_error)}"
        self.logger.error(error_msg)
        raise RuntimeError(error_msg)
        
    def _save_to_db(
        self,
        df: pd.DataFrame,
        table_name: str,
        primary_keys: List[str],
    ) -> pd.DataFrame:
        """Deduplicate df on primary_keys and upsert into the database table."""
        n_before = len(df)
        df = df.drop_duplicates(subset=primary_keys, keep='last')
        n_dropped = n_before - len(df)
        if n_dropped:
            self.logger.warning(
                f"Dropped {n_dropped} duplicate rows on {primary_keys} before upsert"
            )
        to_sql(
            data=df,
            table_name=table_name,
            primary_keys=primary_keys,
            update=True,
            batch_size=5000,
        )
        return df

    @staticmethod
    def create_tickers_mapping(tickers_dict: Dict[str, str], category: str) -> Dict[str, Dict[str, str]]:
        """
        Helper method to create a properly formatted tickers mapping.
        
        Args:
            tickers_dict: Dictionary mapping Bloomberg tickers to internal codes
            category: The category to associate with these tickers
            
        Returns:
            Properly formatted tickers mapping for use with BloombergProvider
        """
        return {category: tickers_dict}

    