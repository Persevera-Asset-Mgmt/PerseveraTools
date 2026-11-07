from typing import Dict, Optional
import pandas as pd
import requests
from datetime import datetime, timedelta

from .base import DataProvider, DataRetrievalError

class SGSProvider(DataProvider):
    """Provider for Brazilian Central Bank (SGS) data."""
    
    def __init__(self, start_date: str = '1980-01-01'):
        super().__init__(start_date)
    
    def get_data(self, category: str, codes_map: Optional[Dict[str, str]] = None, **kwargs) -> pd.DataFrame:
        """
        Retrieve data from SGS.

        Args:
            codes_map: Obrigatório. Código SGS → código interno,
                ex.: {'432': 'brl_selic_target'}.

        Returns:
            DataFrame with columns: ['date', 'code', 'field', 'value']
        """
        self._log_processing(category)

        securities_list = self._require_codes_map(codes_map, category)
        df = pd.DataFrame()
        
        for code in securities_list.keys():
            try:
                # First try without date parameters
                url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados?formato=json"
                r = requests.get(url)
                
                # Check if we need date parameters (daily series constraint)
                if r.status_code != 200:
                    error_data = r.json() if r.text else {}
                    error_msg = error_data.get('error', '')
                    
                    # If it's a daily series with date constraint, retry with date parameters
                    if 'periodicidade diária' in error_msg:
                        self.logger.warning(f"Failed to retrieve monthly data for code {code}: {error_msg}. Trying with daily frequency.")
                        end_date = datetime.now().strftime('%d/%m/%Y')
                        start_date = (datetime.now() - timedelta(days=365*7)).strftime('%d/%m/%Y')
                        url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados?formato=json&dataInicial={start_date}&dataFinal={end_date}"
                        r = requests.get(url)
                    else:
                        self.logger.warning(f"Failed to retrieve data for code {code}: {error_msg}")
                        continue
                
                if r.status_code != 200:
                    self.logger.warning(f"Failed to retrieve data for code {code}: HTTP {r.status_code}")
                    continue
                
                data = r.json()
                if not data:
                    self.logger.warning(f"No data returned for code {code}")
                    continue
                
                temp = pd.DataFrame(data)
                temp.columns = ['date', 'value']
                temp['sgs_code'] = code
                df = pd.concat([df, temp], ignore_index=True)
            except Exception as e:
                self.logger.warning(f"Failed to retrieve data for code {code}: {str(e)}")
                continue
                
        if df.empty:
            raise DataRetrievalError("No data retrieved from SGS")
            
        df['code'] = df['sgs_code'].map(securities_list)
        df['date'] = pd.to_datetime(df['date'], format='%d/%m/%Y')
        df = df.assign(field='close')
        df = df.drop(columns=['sgs_code'])

        return self._validate_output(df) 