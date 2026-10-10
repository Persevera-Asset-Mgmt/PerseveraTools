import pandas as pd
import logging
from typing import List, Optional, Union, Dict
from datetime import datetime, date

from ..db.operations import read_sql
from ..utils.logging import get_logger, timed

logger = get_logger(__name__)

@timed
def get_funds_data(
    cnpjs: Optional[Union[str, List[str]]] = None,
    start_date: Optional[Union[str, date, datetime]] = None,
    end_date: Optional[Union[str, date, datetime]] = None,
    fields: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Retrieve fund data from the fundos_cvm database.
    
    Args:
        cnpjs: Optional fund CNPJ(s) to filter by. Can be a single CNPJ or a list.
        start_date: Optional start date for filtering data.
        end_date: Optional end date for filtering data.
        fields: Optional list of specific fields to retrieve.
                Available fields are: fund_nav, fund_total_equity, fund_total_value,
                fund_inflows, fund_outflows, fund_holders
    
    Returns:
        DataFrame with fund data
    """
    # Convert dates to strings if they're date objects
    if isinstance(start_date, (date, datetime)):
        start_date = start_date.strftime('%Y-%m-%d')
    if isinstance(end_date, (date, datetime)):
        end_date = end_date.strftime('%Y-%m-%d')
    
    # Define available columns and their SQL names
    all_columns = {
        'fund_nav': 'fund_nav',
        'fund_total_equity': 'fund_total_equity',
        'fund_total_value': 'fund_total_value',
        'fund_inflows': 'fund_inflows',
        'fund_outflows': 'fund_outflows',
        'fund_holders': 'fund_holders'
    }
    
    # Determine which columns to retrieve
    if fields:
        # Validate fields
        invalid_fields = set(fields) - set(all_columns.keys())
        if invalid_fields:
            raise ValueError(f"Invalid fields: {invalid_fields}. Valid fields are: {list(all_columns.keys())}")
        columns_to_select = [all_columns[field] for field in fields]
    else:
        columns_to_select = list(all_columns.values())
    
    # Build SQL query
    query = f"""
    SELECT 
        fund_cnpj,
        date,
        {', '.join(columns_to_select)}
    FROM fundos_cvm
    WHERE 1=1
    """
    
    # Add filters using string formatting like in other modules
    if cnpjs:
        if isinstance(cnpjs, str):
            cnpjs = [cnpjs]
        
        cnpjs_str = "','".join(cnpjs)
        query += f" AND fund_cnpj IN ('{cnpjs_str}')"
    
    if start_date:
        query += f" AND date >= '{start_date}'"
    
    if end_date:
        query += f" AND date <= '{end_date}'"
    
    # Order the results
    query += " ORDER BY date, fund_cnpj"
    
    # Execute query
    df = read_sql(query, date_columns=['date'])
    
    if df.empty:
        logger.warning("No fund data found with the specified filters")
        return pd.DataFrame()
    
    # Process the result into a multi-index DataFrame if multiple fields were requested
    if (fields is None) or (len(fields) > 1):
        # Pivot for each field and then combine
        result_dfs = {}
        
        for field in (fields or all_columns.keys()):
            field_name = all_columns[field]
            if field_name in df.columns:
                pivot = df.pivot(index='date', columns='fund_cnpj', values=field_name)
                result_dfs[field] = pivot
        
        # Create a multi-index DataFrame
        if result_dfs:
            return pd.concat(result_dfs, axis=1)
        return pd.DataFrame()
    
    # If only one field was requested, return a simple pivoted DataFrame
    else:
        field_name = all_columns[fields[0]]
        return df.pivot(index='date', columns='fund_cnpj', values=field_name)


# Dias úteis em que a última cota válida é repetida (retorno 0) antes de o
# fundo ser tratado como sem dados.
NAV_FFILL_LIMIT = 5
# Retornos diários acima deste valor absoluto são sempre tratados como erro de cota.
MAX_ABS_DAILY_RETURN = 0.50
# Acima deste valor, o retorno só é descartado se o dia seguinte o desfizer
# (pico e volta = cota errada num dia). Há fundos genuinamente voláteis
# (ex.: Versa Long Biased) com movimentos reais de 20–30% em um dia.
SPIKE_DAILY_RETURN = 0.20


def clean_fund_returns(
    nav: pd.DataFrame,
    ffill_limit: int = NAV_FFILL_LIMIT,
    max_abs_return: float = MAX_ABS_DAILY_RETURN,
    spike_return: float = SPIKE_DAILY_RETURN,
) -> pd.DataFrame:
    """
    Retornos diários de cotas (``date x fundo``) medidos contra a última cota válida.

    Cotas faltantes repetem a anterior por até ``ffill_limit`` linhas (retorno 0
    no buraco, movimento capturado quando a cota volta). São descartados (NaN)
    como erro de cota os retornos com ``|r| > max_abs_return`` e os picos
    ``|r| > spike_return`` desfeitos no dia seguinte (os dois lados do pico).
    ``fundos_cvm`` já teve cotas de subclasses diferentes alternando no mesmo
    CNPJ, o que gera exatamente esse padrão.
    """
    nav = nav.sort_index().where(nav > 0)
    ret = nav.ffill(limit=ffill_limit).pct_change(fill_method=None)
    combined = ((1 + ret) * (1 + ret.shift(-1)) - 1).abs()
    spike = (ret.abs() > spike_return) & (combined < ret.abs() / 2)
    bad = (ret.abs() > max_abs_return) | spike | spike.shift(1, fill_value=False)
    if bad.any().any():
        for dt, cnpj in bad.stack().loc[lambda s: s].index:
            logger.warning(
                "Retorno descartado: %s em %s = %.1f%%",
                cnpj, dt.date(), ret.at[dt, cnpj] * 100,
            )
    return ret.mask(bad)


def get_funds_returns(
    cnpjs: Optional[Union[str, List[str]]] = None,
    start_date: Optional[Union[str, date, datetime]] = None,
    end_date: Optional[Union[str, date, datetime]] = None,
    clean: bool = True,
) -> pd.DataFrame:
    """
    Retornos diários das cotas de ``fundos_cvm`` (``date x fund_cnpj``).

    Args:
        cnpjs: CNPJ(s) no formato de ``fundos_cvm`` (XX.XXX.XXX/XXXX-XX).
        start_date: Data inicial das cotas (o primeiro retorno é do dia seguinte).
        end_date: Data final.
        clean: Se ``True``, aplica :func:`clean_fund_returns`; senão, ``pct_change``
            simples entre cotas consecutivas.
    """
    nav = get_funds_data(cnpjs=cnpjs, start_date=start_date, end_date=end_date, fields=["fund_nav"])
    if nav.empty:
        return nav
    if clean:
        return clean_fund_returns(nav)
    return nav.sort_index().pct_change(fill_method=None)
