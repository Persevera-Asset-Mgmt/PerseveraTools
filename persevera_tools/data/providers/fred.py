from typing import Dict, Optional
import pandas as pd
import time
from fredapi import Fred

from .base import DataProvider, DataRetrievalError
from ...config import settings
from ...utils.logging import get_logger, timed

logger = get_logger(__name__)

FRED_API_KEY = settings.FRED_API_KEY

class FredProvider(DataProvider):
    """Provider for Federal Reserve Economic Data (FRED)."""
    
    def __init__(self, start_date: str = '1980-01-01', api_key: Optional[str] = None, 
                 request_delay: float = 0.2, max_retries: int = 3):
        """
        Initialize FRED provider.
        
        Args:
            start_date: The start date for data retrieval
            api_key: Optional FRED API key. If not provided, uses the one from config
            request_delay: Delay in seconds between requests to avoid rate limiting (default: 0.2)
            max_retries: Maximum number of retry attempts for rate-limited requests (default: 3)
        """
        super().__init__(start_date)
        self.fred = Fred(api_key=api_key or FRED_API_KEY)
        self.request_delay = request_delay
        self.max_retries = max_retries
        logger.info(f"FRED API key: {FRED_API_KEY}")
        logger.info(f"FRED rate limiting: {request_delay}s delay, {max_retries} max retries")
    
    def get_data(self, category: str, codes_map: Optional[Dict[str, str]] = None, **kwargs) -> pd.DataFrame:
        """
        Retrieve data from FRED.

        Args:
            codes_map: Obrigatório. Série FRED → código interno,
                ex.: {'UNRATE': 'us_unemployment_rate'}.

        Returns:
            DataFrame with columns: ['date', 'code', 'field', 'value']
        """
        self._log_processing(category)

        securities_list = self._require_codes_map(codes_map, category)
        
        # Retrieve data with rate limiting and retry logic
        data_dict = {}
        for series in securities_list.keys():
            series_data = self._get_series_with_retry(series)
            if series_data is not None:
                data_dict[series] = series_data
            # Add delay between requests to avoid rate limiting
            time.sleep(self.request_delay)
        
        if not data_dict:
            raise DataRetrievalError("No data retrieved from FRED")
            
        df = pd.DataFrame(data_dict)
        df = df.stack().reset_index()
        df.columns = ['date', 'fred_code', 'value']
        df['code'] = df['fred_code'].map(securities_list)
        df = df.assign(field='close')
        
        return self._validate_output(df)
    
    def _get_series_with_retry(self, series: str) -> Optional[pd.Series]:
        """
        Retrieve a single series from FRED with retry logic for rate limiting.
        
        Args:
            series: FRED series code
            
        Returns:
            Series data or None if all attempts fail
        """
        for attempt in range(self.max_retries):
            try:
                return self.fred.get_series(series, observation_start=self.start_date)
            except Exception as e:
                error_msg = str(e).lower()
                
                # Check if this is a rate limiting error
                if 'rate limit' in error_msg or 'too many requests' in error_msg:
                    if attempt < self.max_retries - 1:
                        # Exponential backoff: wait 2^attempt seconds
                        wait_time = 2 ** attempt
                        logger.warning(f"Rate limit hit for series {series}. Waiting {wait_time}s before retry {attempt + 2}/{self.max_retries}")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"Failed to retrieve series {series} after {self.max_retries} attempts due to rate limiting")
                        return None
                else:
                    # For non-rate-limiting errors, log and return None immediately
                    logger.warning(f"Failed to retrieve series {series}: {str(e)}")
                    return None
        
        return None 