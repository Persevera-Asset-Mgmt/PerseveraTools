from typing import Dict, Optional
import pandas as pd
import urllib.request
import warnings
from datetime import datetime, timedelta
import numpy as np
import io
from .base import DataProvider, DataRetrievalError
from ...utils.logging import get_logger

logger = get_logger(__name__)
warnings.filterwarnings("ignore", message="Workbook contains no default style", category=UserWarning)

class AnbimaProvider(DataProvider):
    """Provider for ANBIMA data."""
    
    def __init__(self, start_date: str = '1980-01-01'):
        super().__init__(start_date)
    
    def get_data(self, category: str, **kwargs) -> pd.DataFrame:
        """
        Retrieve data from ANBIMA.
        
        Args:
            **kwargs: Additional keyword arguments
            
        Returns:
            DataFrame with columns: ['date', 'code', 'field', 'value']
        """
        self._log_processing(category)
        
        if category == 'anbima_indices':
            return self._read_anbima_files(kwargs.get('codes_map'), category)
        elif category == 'anbima_debentures':
            return self.get_debentures_data(category, **kwargs)
        elif category == 'anbima_titulos_publicos':
            return self.get_titulos_publicos_data(category, **kwargs)
        elif category == 'anbima_cri_cra':
            return self.get_cri_cra_data(category, **kwargs)
        else:
            raise ValueError(f"Invalid category: {category}")

        # df = self._read_anbima_files()
        
        # if df.empty:
        #     raise DataRetrievalError("No data retrieved from ANBIMA")
            
        # return self._validate_output(df)

    def _read_anbima_files(self, codes_map: Optional[Dict[str, str]], category: str) -> pd.DataFrame:
        """Read ANBIMA files from URL. `codes_map`: URL do arquivo → código interno."""
        securities_list = self._require_codes_map(codes_map, category)
        data_frames = []

        for url, index_name in securities_list.items():
            try:
                logger.info(f"Reading {index_name} from {url}")
                try:
                    temp = pd.read_excel(url, usecols="B:C", engine="openpyxl")
                except:
                    temp = pd.read_excel(url, usecols="B:C", engine="xlrd")
                    
                temp.columns = ['date', 'value']
                temp['code'] = index_name
                data_frames.append(temp)
                
            except Exception as e:
                logger.warning(f"Failed to process {index_name}: {str(e)}")
        
        if not data_frames:
            return pd.DataFrame()
            
        df = pd.concat(data_frames, ignore_index=True)
        df = df.assign(field='close')
        return df

    def get_debentures_data(self, category: str, **kwargs) -> pd.DataFrame:
        """
        Retrieves and parses ANBIMA debentures data for a specific date.

        Args:
            date: The date for which to retrieve the data.

        Returns:
            A DataFrame with the parsed debentures data.
        """
        self._log_processing(category)
        
        data_frames = []
        for date in pd.bdate_range(start=self.start_date, end=datetime.today(), freq='B'):
            url = f"https://www.anbima.com.br/informacoes/merc-sec-debentures/arqs/db{date.strftime('%y%m%d')}.txt"
            
            content = None
            try:
                with urllib.request.urlopen(url) as response:
                    content = response.read().decode('latin-1')
            except Exception as e:
                logger.error(f"Failed to download debentures file from {url}: {e}")
                continue
                
            if content:
                try:
                    parsed_df = self._parse_debentures_file(content, date)
                    if not parsed_df.empty:
                        data_frames.append(parsed_df)
                except Exception as e:
                    logger.error(f"Failed to parse debentures file: {e}")

        if not data_frames:
            raise DataRetrievalError("No data retrieved from ANBIMA debentures")
        
        df = pd.concat(data_frames, ignore_index=True)
        
        df = df.melt(
            id_vars=['code', 'date'],
            var_name='field'
        )
        df = df.replace({np.nan: None})
        df['source'] = 'anbima'
        return df

    def _parse_debentures_file(self, content: str, date: datetime) -> pd.DataFrame:
        """
        Parses the content of an ANBIMA debentures TXT file.

        Args:
            content: The string content of the file.

        Returns:
            A DataFrame with the parsed data.
        """
        lines = content.splitlines()
        if len(lines) < 4:
            return pd.DataFrame()
            
        header = lines[2].split('@')
        data = [line.split('@') for line in lines[3:]]

        df = pd.DataFrame(data, columns=header)

        df = df[['Código', 'Taxa Indicativa', 'PU', 'Duration']]

        def clean_value(v):
            return v.strip().replace('.', '').replace(',', '.').replace('--', '0') if v.strip() else None

        df = df.map(clean_value)

        cols_num = ['Taxa Indicativa', 'PU', 'Duration']
        df[cols_num] = df[cols_num].apply(lambda x: pd.to_numeric(x, errors='coerce'))
        df.columns = ['code', 'yield_to_maturity', 'price_close', 'duration']
        df['date'] = date
        return df

    def get_titulos_publicos_data(self, category: str, **kwargs) -> pd.DataFrame:
        """
        Retrieves and parses ANBIMA titulos publicos data for a specific date.

        Args:
            date: The date for which to retrieve the data.

        Returns:
            A DataFrame with the parsed titulos publicos data.
        """
        self._log_processing(category)
        
        data_frames = []
        for date in pd.bdate_range(start=self.start_date, end=datetime.today(), freq='B'):
            url = f"https://www.anbima.com.br/informacoes/merc-sec/arqs/ms{date.strftime('%y%m%d')}.txt"
            content = None
            try:
                with urllib.request.urlopen(url) as response:
                    content = response.read().decode('latin-1')
            except Exception as e:
                logger.error(f"Failed to download titulos publicos file from {url}: {e}")
                continue
                
            if content:
                try:
                    parsed_df = self._parse_titulos_publicos_file(content)
                    if not parsed_df.empty:
                        data_frames.append(parsed_df)
                except Exception as e:
                    logger.error(f"Failed to parse titulos publicos file: {e}")

        if not data_frames:
            raise DataRetrievalError("No data retrieved from ANBIMA titulos publicos")
        
        df = pd.concat(data_frames, ignore_index=True)
        
        df = df.melt(
            id_vars=['code', 'date', 'maturity'], 
            var_name='field'
        )
        df = df.replace({np.nan: None})
        return df

    def _parse_titulos_publicos_file(self, content: str) -> pd.DataFrame:
        """
        Parses the content of an ANBIMA titulos publicos TXT file.

        Args:
            content: The string content of the file.

        Returns:
            A DataFrame with the parsed data.
        """
        lines = content.splitlines()
        if len(lines) < 4:
            return pd.DataFrame()
            
        header = lines[2].split('@')
        data = [line.split('@') for line in lines[3:]]

        df = pd.DataFrame(data, columns=header)

        df = df[['Titulo', 'Data Referencia', 'Data Vencimento', 'Tx. Indicativas', 'PU']]

        def clean_value(v):
            return v.strip().replace('.', '').replace(',', '.').replace('--', '0') if v.strip() else None

        df = df.map(clean_value)

        cols_num = ['Tx. Indicativas', 'PU']
        df[cols_num] = df[cols_num].apply(lambda x: pd.to_numeric(x, errors='coerce'))
        df['Data Referencia'] = pd.to_datetime(df['Data Referencia'], format='%Y%m%d', errors='coerce')
        df['Data Vencimento'] = pd.to_datetime(df['Data Vencimento'], format='%Y%m%d', errors='coerce')
        df.columns = ['code', 'date', 'maturity', 'yield_to_maturity', 'price_close']
        return df

    def get_cri_cra_data(self, category: str, **kwargs) -> pd.DataFrame:
        """
        Retrieves and parses ANBIMA CRI/CRA data for each business day using the CSV endpoint.

        Returns:
            A DataFrame with the parsed CRI/CRA data.
        """
        self._log_processing(category)

        data_frames = []
        for date in pd.bdate_range(start=self.start_date, end=datetime.today(), freq='B'):
            url = (
                "https://www.anbima.com.br/pt_br/anbima/TaxasCriCraExport/exportarCSV"
                f"?filtroTermo=&filtroData={date.strftime('%d/%m/%Y')}"
            )
            content = None
            try:
                with urllib.request.urlopen(url) as response:
                    content = response.read().decode('latin-1')
            except Exception as e:
                # logger.error(f"Failed to download CRI/CRA CSV from {url}: {e}")
                continue

            if content:
                try:
                    parsed_df = self._parse_cri_cra_csv(content)
                    if not parsed_df.empty:
                        data_frames.append(parsed_df)
                except Exception as e:
                    # logger.error(f"Failed to parse CRI/CRA CSV: {e}")
                    pass

        if not data_frames:
            raise DataRetrievalError("No data retrieved from ANBIMA CRI/CRA")

        df = pd.concat(data_frames, ignore_index=True)
        df = df.melt(
            id_vars=['code', 'date'],
            var_name='field'
        )
        df = df.dropna(subset=['value'])
        df = df.replace({np.nan: None})
        df['source'] = 'anbima'
        return df

    def _parse_cri_cra_csv(self, content: str) -> pd.DataFrame:
        """
        Parses the content of an ANBIMA CRI/CRA CSV export.

        Args:
            content: The string content of the CSV file.
            date: The date used in the request (fallback if needed).

        Returns:
            A DataFrame with the parsed data.
        """
        buffer = io.StringIO(content)
        try:
            raw = pd.read_csv(buffer, sep=';', engine='python')
        except Exception:
            return pd.DataFrame()

        if raw.empty:
            return pd.DataFrame()

        # Try accented column names first, then non-accented fallback
        try:
            df = raw.copy()
            df.columns = df.columns.str.strip()
            df = df[['Código', 'Data de Referência', 'Taxa Indicativa', 'PU', 'Duration']]
        except KeyError:
            return pd.DataFrame()

        def clean_value(v):
            return v.strip().replace('.', '').replace(',', '.').replace('--', '0') if isinstance(v, str) and v.strip() else v

        df = df.applymap(clean_value)

        df[['PU', 'Taxa Indicativa']] = df[['PU', 'Taxa Indicativa']].apply(lambda x: pd.to_numeric(x, errors='coerce'))
        df['Data de Referência'] = pd.to_datetime(df['Data de Referência'], format='%d/%m/%Y', errors='coerce')
        df.columns = ['code', 'date', 'yield_to_maturity', 'price_close', 'duration']
        return df
