from typing import Dict, Optional, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import requests

from .base import DataProvider, DataRetrievalError
# from persevera_tools.db.operations import read_sql
# from persevera_tools.db.fibery import read_fibery
from ...db.operations import read_sql
from ...db.fibery import read_fibery

class MaisRetornoProvider(DataProvider):
    """Provider for Mais Retorno data (e.g., debentures quotes and funds)."""

    BASE_URL = "https://api.maisretorno.com/v3/general/quotes"
    FUNDS_BASE_URL = "https://data.maisretorno.com/mr-data/v4/general/quotes"

    def __init__(self, start_date: str = '1980-01-01'):
        super().__init__(start_date)

    def get_data(self, category: str, **kwargs) -> pd.DataFrame:
        self._log_processing(category)
        if category == 'mais_retorno_debentures':
            df = self.get_debentures_data(category, **kwargs)
        elif category == 'mais_retorno_fundos':
            df = self.get_funds_data(category, **kwargs)
        else:
            raise ValueError(f"Invalid category: {category}")
        return df

    def _normalize_raw_code(self, category: str, code: str) -> str:
        """
        Normalize user-provided code into Mais Retorno slug (raw_code).
        For debentures, accepts 'CSNAA1' and converts to 'csnaa1:deb'.
        For funds, accepts CNPJ like '50.716.952/0001-84' or '50716952000184'
        and converts it to '50716952000184:fi'.
        """
        code_clean = (code or "").strip()
        if not code_clean:
            return code_clean
        category_lc = category.lower()
        if category_lc in {"debentures", "mais_retorno_debentures"}:
            lc = code_clean.lower()
            return lc if ":" in lc else f"{lc}:deb"
        if category_lc in {"funds", "fundos", "mais_retorno_fundos"}:
            # Accept formatted CNPJ like '50.716.952/0001-84' and normalize to digits
            if ":" in code_clean:
                return code_clean.lower()
            digits_only = "".join(ch for ch in code_clean if ch.isdigit())
            base = digits_only or code_clean.replace(".", "").replace("-", "").replace("/", "")
            return f"{base.lower()}:fi"
        return code_clean

    def _fetch_quotes_for_code(self, raw_code: str, adjusted: bool = True) -> pd.DataFrame:
        """
        Fetch time series quotes for a single Mais Retorno raw code (slug).

        Args:
            raw_code: Code slug used by Mais Retorno API (e.g., 'csnaa1:deb').
            adjusted: Whether to request adjusted time series.

        Returns:
            DataFrame with columns ['date', 'value'] for the given raw_code.
        """
        params = {"adjusted": "true" if adjusted else "false"}
        url = f"{self.BASE_URL}/{raw_code}"
        print(url)
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            payload = r.json() or {}
            quotes = payload.get("quotes") or []
            if not quotes:
                return pd.DataFrame()

            temp = pd.DataFrame(quotes)
            # Expecting 'c' = price/value, 'd' = epoch ms
            if not {"c", "d"}.issubset(temp.columns):
                return pd.DataFrame()

            temp = temp.rename(columns={"c": "value", "d": "date"})
            temp["date"] = pd.to_datetime(temp["date"], unit="ms", errors="coerce")
            temp = temp.dropna(subset=["date", "value"])
            return temp[["date", "value"]]
        except requests.HTTPError as e:
            self.logger.warning(f"HTTP error fetching Mais Retorno code '{raw_code}': {e}")
            return pd.DataFrame()
        except Exception as e:
            self.logger.warning(f"Failed to fetch Mais Retorno code '{raw_code}': {e}")
            return pd.DataFrame()

    def _fetch_fund_quotes_for_code(
        self,
        raw_code: str,
        adjusted: bool = True,
        link_old_historic: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch time series fund data for a single Mais Retorno fund raw code (slug).

        Args:
            raw_code: Code slug used by Mais Retorno API (e.g., '50716952000184:fi').
            adjusted: Whether to request adjusted time series.
            link_old_historic: Whether to include old historic series.

        Returns:
            DataFrame with columns ['date', 'fund_nav', 'fund_total_equity', 'fund_holders']
            for the given raw_code.
        """
        params = {
            "adjusted": "true" if adjusted else "false",
            "link_old_historic": "true" if link_old_historic else "false",
        }
        url = f"{self.FUNDS_BASE_URL}/{raw_code}"
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            payload = r.json() or {}
            quotes = payload.get("quotes") or []
            if not quotes:
                return pd.DataFrame()

            temp = pd.DataFrame(quotes)
            # Expecting 'd' = date (ISO string), 'c' = fund NAV, 'p' = total equity, 'q' = holders
            required_cols = {"d", "c", "p", "q"}
            if not required_cols.issubset(temp.columns):
                return pd.DataFrame()

            temp = temp.rename(
                columns={
                    "d": "date",
                    "c": "fund_nav",
                    "p": "fund_total_equity",
                    "q": "fund_holders",
                }
            )
            temp["date"] = pd.to_datetime(temp["date"], errors="coerce")
            for col in ["fund_nav", "fund_total_equity", "fund_holders"]:
                temp[col] = pd.to_numeric(temp[col], errors="coerce")
            temp = temp.dropna(subset=["date"])
            if temp.empty:
                return pd.DataFrame()
            return temp[["date", "fund_nav", "fund_total_equity", "fund_holders"]]
        except requests.HTTPError as e:
            self.logger.warning(f"HTTP error fetching Mais Retorno fund code '{raw_code}': {e}")
            return pd.DataFrame()
        except Exception as e:
            self.logger.warning(f"Failed to fetch Mais Retorno fund code '{raw_code}': {e}")
            return pd.DataFrame()

    def get_debentures_data(self, category: str, **kwargs) -> pd.DataFrame:
        """
        Retrieve data from Mais Retorno.

        Usage examples:
            - Debentures: category='debentures'
              Optionally pass 'codes_map' or 'codes' in kwargs.

        Kwargs:
            adjusted: bool (default True) - whether to use adjusted time series.
            codes_map: Dict[str, str] mapping input code or raw_code to internal code.
            codes: Iterable[str] of user-facing codes (e.g., ['CSNAA1']).
                   For debentures, these will be normalized to Mais Retorno slugs like 'csnaa1:deb'.

        Returns:
            DataFrame with columns: ['date', 'code', 'field', 'value']
        """
        adjusted = kwargs.get("adjusted", True)
        # Preferred: mapping from DB defining internal code
        codes_map: Optional[Dict[str, str]] = kwargs.get("codes_map")

        if codes_map is None:
            # Attempt to load from indicadores_definicoes where source='mais_retorno' and category matches
            try:
                # renda_fixa_historico também guarda TPF (code NTN-B_2035-05-15)
                # desde 2026-11-11; o filtro de prefixo os exclui de forma explícita.
                # Mesma query do workflow n8n KR9EDNi6nnWTtNfK.
                query = """SELECT DISTINCT code
                FROM renda_fixa_historico
                WHERE LENGTH(code) <= 6
                  AND code !~* '^(LTN|LFT|NTN|TESOURO)'
                ORDER BY code;"""
                codes = read_sql(query)
                # codes_map = get_emissions(selected_fields=['code'])
                codes_map = codes.assign(code_mais_retorno=lambda x: x['code']).set_index('code')['code_mais_retorno'].to_dict()
            except Exception:
                codes_map = {}

        # Also allow a direct list/iterable of user-facing codes to fetch
        codes_iterable: Optional[Iterable[str]] = kwargs.get("codes")
        if codes_iterable is None:
            # If no explicit codes provided, use keys from codes_map
            codes_iterable = list(codes_map.keys())

        if not codes_iterable:
            raise DataRetrievalError(f"No Mais Retorno codes available for category '{category}'")

        frames = []

        # Use ThreadPoolExecutor to speed up retrieval across many debentures
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_codes = {}
            for input_code in codes_iterable:
                raw_code = self._normalize_raw_code("debentures", input_code)
                future = executor.submit(self._fetch_quotes_for_code, raw_code, adjusted)
                future_to_codes[future] = (input_code, raw_code)

            for future in as_completed(future_to_codes):
                input_code, raw_code = future_to_codes[future]
                try:
                    temp = future.result()
                except Exception as e:
                    self.logger.warning(
                        f"Failed to fetch Mais Retorno code '{raw_code}' in thread: {e}"
                    )
                    continue

                if temp.empty:
                    continue

                # Resolve internal code preference: input_code -> raw_code -> fallback to input_code
                internal_code = (
                    (codes_map or {}).get(input_code)
                    or (codes_map or {}).get(raw_code)
                    or input_code
                )
                field_name = "price_close_adj" if adjusted else "price_close"
                temp = temp.assign(code=internal_code, field=field_name)
                frames.append(temp[["date", "code", "field", "value"]])

        if not frames:
            raise DataRetrievalError("No data retrieved from Mais Retorno")

        df = pd.concat(frames, ignore_index=True)
        df['source'] = 'mais_retorno'
        return df

    def get_funds_data(self, category: str, **kwargs) -> pd.DataFrame:
        """
        Retrieve fund data (NAV, total equity, holders) from Mais Retorno.

        Usage example:
            category='mais_retorno_fundos'
            Optionally pass 'codes_map' or 'codes' in kwargs.

        Kwargs:
            adjusted: bool (default True) - whether to use adjusted time series.
            link_old_historic: bool (default True) - whether to include old historic series.
            codes_map: Dict[str, str] mapping input code or raw_code to internal code.
            codes: Iterable[str] of user-facing codes (e.g., CNPJ strings like '50716952000184').
                   These will be normalized to Mais Retorno slugs like '50716952000184:fi'.

        Returns:
            DataFrame
        """
        adjusted = kwargs.get("adjusted", True)
        link_old_historic = kwargs.get("link_old_historic", True)
        codes_map: Optional[Dict[str, str]] = kwargs.get("codes_map")

        if codes_map is None:
            codes_map = {}

        # Also allow a direct list/iterable of user-facing codes to fetch
        codes_iterable: Optional[Iterable[str]] = kwargs.get("codes")
        if codes_iterable is None:
            # If no explicit codes provided, use keys from codes_map
            # codes_iterable = list(codes_map.keys())
            ativos = read_fibery(table_name='Inv-Taxonomia/Ativos', include_fibery_fields=False)
            ativos = ativos[ativos['Classificação Sub-Conjunto'] == 'Fundo de Crédito High Yield']
            codes_iterable = ativos['Name'].tolist()

        if not codes_iterable:
            raise DataRetrievalError(f"No Mais Retorno fund codes available for category '{category}'")

        frames = []
        for input_code in codes_iterable:
            raw_code = self._normalize_raw_code("funds", input_code)
            temp = self._fetch_fund_quotes_for_code(
                raw_code,
                adjusted=adjusted,
                link_old_historic=link_old_historic,
            )
            if temp.empty:
                continue

            # Resolve internal code preference: input_code -> raw_code -> fallback to input_code
            internal_code = (
                (codes_map or {}).get(input_code)
                or (codes_map or {}).get(raw_code)
                or input_code
            )

            temp = temp.assign(code=internal_code)
            frames.append(temp[["date", "code", "fund_nav", "fund_total_equity", "fund_holders"]])

        if not frames:
            raise DataRetrievalError("No fund data retrieved from Mais Retorno")

        df = pd.concat(frames, ignore_index=True)
        df = df.rename(columns={"code": "fund_cnpj"})
        return df
