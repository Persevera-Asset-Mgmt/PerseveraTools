from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
import pandas as pd
import numpy as np
import logging
from datetime import datetime

class DataProviderError(Exception):
    """Base exception for data provider errors."""
    pass

class ValidationError(DataProviderError):
    """Raised when data validation fails."""
    pass

class DataRetrievalError(DataProviderError):
    """Raised when data retrieval fails."""
    pass

class DataProvider(ABC):
    """Base class for all data providers."""
    
    def __init__(self, start_date: str = '1980-01-01'):
        """
        Initialize the data provider.
        
        Args:
            start_date: The start date for data retrieval in 'YYYY-MM-DD' format
            
        Raises:
            ValueError: If start_date is not in the correct format
        """
        try:
            self.start_date = pd.to_datetime(start_date)
        except ValueError as e:
            raise ValueError(f"Invalid start_date format. Expected 'YYYY-MM-DD', got {start_date}") from e
            
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def get_data(self, category: str, **kwargs) -> pd.DataFrame:
        """
        Retrieve data for the specified category.
        
        Args:
            category: The category of data to retrieve
            **kwargs: Additional arguments specific to the provider
            
        Returns:
            DataFrame with columns: ['date', 'code', 'field', 'value']
            
        Raises:
            DataRetrievalError: If data retrieval fails
            ValidationError: If retrieved data fails validation
        """
        pass

    def _validate_output(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Validate and clean the output DataFrame.
        
        Args:
            df: DataFrame to validate
            
        Returns:
            Cleaned and validated DataFrame
            
        Raises:
            ValidationError: If validation fails
        """
        required_cols = ['date', 'code', 'field', 'value']
        
        # Check required columns
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            raise ValidationError(f"Missing required columns: {missing_cols}")
        
        try:
            df = df.copy()

            if df.empty:
                self.logger.warning("No data points to validate")
                return df

            # Validate and convert date column. YYYYMMDD integers must not go
            # through bare pd.to_datetime (they become 1970-01-01 ns timestamps).
            df['date'] = self._coerce_dates(df['date'])
            if df['date'].isna().all():
                raise ValidationError("Could not parse any values in 'date' column")

            # Validate code column
            if df['code'].isna().any():
                raise ValidationError("Found null values in 'code' column")

            # Clean value/field columns (null field violates indicadores NOT NULL)
            df = df.dropna(subset=['value', 'field'])
            if df.empty:
                self.logger.warning("No data points left after dropping null value/field")
                return df
            df['value'] = df['value'].astype(float)

            # Check for infinite values
            if not np.isfinite(df['value']).all():
                raise ValidationError("Found infinite values in 'value' column")

            # Filter by start date and sort
            n_before = len(df)
            date_min, date_max = df['date'].min(), df['date'].max()
            df = df[df['date'] >= self.start_date]
            if df.empty:
                self.logger.warning(
                    "No data points found after start_date filter "
                    f"(rows={n_before}, date_range={date_min}..{date_max}, "
                    f"start_date={self.start_date})"
                )
                return df

            df = df.sort_values(['date', 'code', 'field'], ascending=[False, True, True])

            return df[required_cols].reset_index(drop=True)

        except Exception as e:
            if not isinstance(e, ValidationError):
                raise ValidationError(f"Data validation failed: {str(e)}") from e
            raise

    @staticmethod
    def _coerce_dates(dates: pd.Series) -> pd.Series:
        """Parse dates, including Bloomberg-style YYYYMMDD integers."""
        if pd.api.types.is_datetime64_any_dtype(dates):
            return pd.to_datetime(dates)

        numeric = pd.to_numeric(dates, errors='coerce')
        if numeric.notna().any():
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

    @staticmethod
    def _require_codes_map(codes_map: Optional[Dict[str, str]], category: str) -> Dict[str, str]:
        """
        Valida o mapeamento código externo → código interno passado pelo chamador.

        Os providers sgs, fred, sidra e anbima (índices) liam esse mapeamento da
        tabela `indicadores_definicoes`, que não existe mais. Agora ele precisa
        vir em `codes_map`; sem ele, o provider não sabe o que buscar.
        """
        if not codes_map:
            raise ValueError(
                f"'{category}' exige `codes_map` (dict código externo → código interno), "
                "ex.: get_data(source='sgs', codes_map={'432': 'brl_selic_target'})."
            )
        return dict(codes_map)

    def _log_processing(self, category: str) -> None:
        """
        Log that we're processing a category.
        
        Args:
            category: The category being processed
        """
        self.logger.info(f"Processing '{category}'...") 