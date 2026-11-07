from typing import Dict, Optional
import pandas as pd
import numpy as np
import sidrapy

from .base import DataProvider, DataRetrievalError

class SidraProvider(DataProvider):
    """Provider for IBGE's SIDRA data."""
    
    # Default tables to fetch
    DEFAULT_TABLES = {
        '1737': 'IPCA - Série histórica com número-índice',
        '118': 'IPCA dessazonalizado',
        '6381': 'Taxa de desocupação',
        '3065': 'IPCA15'
    }
    
    def __init__(self, start_date: str = '1980-01-01'):
        super().__init__(start_date)
    
    def get_data(self, category: str, tables: Optional[Dict[str, str]] = None,
                 codes_map: Optional[Dict[str, str]] = None, **kwargs) -> pd.DataFrame:
        """
        Retrieve data from SIDRA.

        Args:
            tables: Optional dictionary mapping table codes to descriptions.
                   If not provided, uses DEFAULT_TABLES
            codes_map: Obrigatório. Código da série SIDRA (coluna D3N) →
                código interno. Séries fora do mapa são descartadas.

        Returns:
            DataFrame with columns: ['date', 'code', 'field', 'value']
        """
        self._log_processing(category)

        tables = tables or self.DEFAULT_TABLES
        securities_list = self._require_codes_map(codes_map, category)
        df = pd.DataFrame()
        
        for code in tables.keys():
            try:
                self.logger.info(f"Retrieving table {code}: {tables[code]}")
                temp = sidrapy.get_table(
                    table_code=code,
                    territorial_level='1',
                    ibge_territorial_code='all',
                    period='all',
                    header='n'
                )
                
                # Process the data
                temp = temp[['V', 'D2C', 'D3N']]
                temp = temp.replace('...', np.nan)
                temp = temp.dropna()
                temp['D2C'] = pd.to_datetime(temp['D2C'], format='%Y%m')
                temp.columns = ['value', 'date', 'sidra_code']
                
                df = pd.concat([df, temp], ignore_index=True)
                
            except Exception as e:
                self.logger.warning(f"Failed to retrieve table {code}: {str(e)}")
                continue
                
        if df.empty:
            raise DataRetrievalError("No data retrieved from SIDRA")
            
        df['code'] = df['sidra_code'].map(securities_list)
        df = df.assign(field='close')
        df = df.dropna(subset=['code'])
        
        return self._validate_output(df) 