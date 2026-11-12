from datetime import datetime
from typing import Optional, Union, List, Literal
import pandas as pd

from persevera_tools.data.tpf_codes import TPF_CODE_PATTERN, TPF_CODE_SEPARATOR
from persevera_tools.db.operations import read_sql


def get_emissions(
    index_code: Optional[Union[str, List[str]]] = None,
    start_date: Optional[Union[str, datetime, pd.Timestamp]] = None,
    selected_fields: Optional[List[str]] = None,
    deb_incent_lei_12431: Optional[bool] = None) -> pd.DataFrame:
    """Get emissions from credito_privado_emissoes table.

    Args:
        index_code: Single index code, list of index codes, or None to retrieve all codes.
        start_date: Optional start date filter as string 'YYYY-MM-DD', datetime, or pandas Timestamp
        selected_fields: Optional list of fields to retrieve
        deb_incent_lei_12431: Whether to retrieve emissions under the Debêntures Incentivadas by Lei 12431 (default: None)
    Returns:
        DataFrame with emissions data, indexed by 'data_emissao'.
    """
    index_codes = []
    if index_code is not None:
        if isinstance(index_code, str):
            index_codes = [index_code]
        elif isinstance(index_code, list):
            index_codes = index_code
        else:
            raise ValueError("index_code must be a string or list of strings")

        if not all(isinstance(idx, str) and idx for idx in index_codes):
            raise ValueError("All index codes must be non-empty strings")

    if selected_fields is not None:
        if not all(isinstance(field, str) and field for field in selected_fields):
            raise ValueError("All selected fields must be non-empty strings")
    else:
        selected_fields = ['code', 'empresa', 'data_emissao', 'data_vencimento', 'valor_nominal_na_emissao', 'quantidade_emitida', 'indice', 'percentual_multiplicador_rentabilidade']

    start_date_str = None

    if start_date is not None:
        if isinstance(start_date, str):
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                start_date_str = start_date
            except ValueError:
                raise ValueError("start_date string must be in YYYY-MM-DD format")
        elif isinstance(start_date, (datetime, pd.Timestamp)):
            start_dt = start_date
            start_date_str = start_dt.strftime("%Y-%m-%d")
        else:
            raise ValueError("start_date must be a string, datetime, or pandas Timestamp")

    field_str = ", ".join(selected_fields)

    query = f"""
        SELECT {field_str}
        FROM credito_privado_emissoes
    """

    where_clauses = []
    if index_codes:
        index_str = "','".join(index_codes)
        where_clauses.append(f"indice IN ('{index_str}')")

    if start_date_str:
        where_clauses.append(f"data_emissao >= '{start_date_str}'")

    if deb_incent_lei_12431 is not None:
        if deb_incent_lei_12431:
            where_clauses.append("deb_incent_lei_12431 = TRUE")
        else:
            where_clauses.append("deb_incent_lei_12431 = FALSE")

    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    query += " ORDER BY data_emissao, code"

    date_fields = [field for field in selected_fields if 'data' in field]
    df = read_sql(query, date_columns=date_fields)

    if df.empty:
        raise ValueError("No data found")

    if 'quantidade_emitida' in df.columns and 'valor_nominal_na_emissao' in df.columns:
        df['volume_emissao'] = df['valor_nominal_na_emissao'] * df['quantidade_emitida']

    if 'data_emissao' in df.columns:
        df = df.set_index('data_emissao')

    return df


def get_references(
    code: Optional[Union[str, List[str]]] = None,
    start_date: Optional[Union[str, datetime, pd.Timestamp]] = None,
    end_date: Optional[Union[str, datetime, pd.Timestamp]] = None) -> pd.DataFrame:
    """Get reference maturities from the credito_privado_referencia table.

    The ``reference`` dates (e.g. the benchmark NTN-B maturity matched to each
    IPCA-linked private credit asset) used to live in ``renda_fixa_historico``
    and are now stored in the dedicated ``credito_privado_referencia`` table
    with columns ``code, date, reference``.

    Args:
        code: Single code, list of codes, or None to retrieve all codes.
        start_date: Optional start date filter as string 'YYYY-MM-DD', datetime, or pandas Timestamp.
        end_date: Optional end date filter as string 'YYYY-MM-DD', datetime, or pandas Timestamp.
    Returns:
        DataFrame with columns ['date', 'code', 'reference'].
    Raises:
        ValueError: If dates are in invalid format or if end_date is before start_date.
    """
    codes = []
    if code is not None:
        if isinstance(code, str):
            codes = [code]
        elif isinstance(code, list):
            codes = code
        else:
            raise ValueError("code must be a string or list of strings")

        if not all(isinstance(c, str) and c for c in codes):
            raise ValueError("All codes must be non-empty strings")

    start_date_str = None
    end_date_str = None

    if start_date is not None:
        if isinstance(start_date, str):
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                start_date_str = start_date
            except ValueError:
                raise ValueError("start_date string must be in YYYY-MM-DD format")
        elif isinstance(start_date, (datetime, pd.Timestamp)):
            start_dt = start_date
            start_date_str = start_dt.strftime("%Y-%m-%d")
        else:
            raise ValueError("start_date must be a string, datetime, or pandas Timestamp")

    if end_date is not None:
        if isinstance(end_date, str):
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                end_date_str = end_date
            except ValueError:
                raise ValueError("end_date string must be in YYYY-MM-DD format")
        elif isinstance(end_date, (datetime, pd.Timestamp)):
            end_dt = end_date
            end_date_str = end_dt.strftime("%Y-%m-%d")
        else:
            raise ValueError("end_date must be a string, datetime, or pandas Timestamp")

    if start_date is not None and end_date is not None and start_dt > end_dt:
        raise ValueError("end_date cannot be before start_date")

    query = """
        SELECT date, code, reference
        FROM credito_privado_referencia
        WHERE 1 = 1
    """

    if codes:
        codes_str = "','".join(codes)
        query += f" AND code IN ('{codes_str}')"
    if start_date_str:
        query += f" AND date >= '{start_date_str}'"
    if end_date_str:
        query += f" AND date <= '{end_date_str}'"

    query += " ORDER BY date, code"

    return read_sql(query, date_columns=['date', 'reference'])


def _get_titulos_publicos(
    codes: List[str],
    fields: List[str],
    source: str,
    start_date_str: Optional[str],
    end_date_str: Optional[str],
) -> pd.DataFrame:
    """Títulos públicos (``category='titulos_publicos'`` de ``get_series``).

    Lê ``renda_fixa_historico``, onde o code de TPF leva o vencimento
    (``NTN-B_2035-05-15``; ver ``persevera_tools/data/tpf_codes.py``), e devolve
    o mesmo formato da antiga ``anbima_titulos_publicos_historico``:
    ``date, code, maturity, value`` com ``code`` = tipo do título (``'NTN-B'``).
    O predicado do code é o mesmo do índice parcial
    ``renda_fixa_historico_tpf_field_date_idx``, que evita varrer a tabela.
    """
    sep = TPF_CODE_SEPARATOR
    fields_str = "','".join(fields)
    query = f"""
        SELECT date,
               split_part(code, '{sep}', 1) AS code,
               split_part(code, '{sep}', 2)::date AS maturity,
               value
        FROM renda_fixa_historico
        WHERE code ~ '{TPF_CODE_PATTERN}'
          AND field IN ('{fields_str}')
    """
    if source != 'all':
        query += f" AND source = '{source}'"
    if codes:
        codes_str = "','".join(codes)
        query += f" AND split_part(code, '{sep}', 1) IN ('{codes_str}')"
    if start_date_str:
        query += f" AND date >= '{start_date_str}'"
    if end_date_str:
        query += f" AND date <= '{end_date_str}'"
    query += " ORDER BY date, code, maturity"

    return read_sql(query, date_columns=['date', 'maturity'])


def get_series(
    code: Optional[Union[str, List[str]]] = None,
    start_date: Optional[Union[str, datetime, pd.Timestamp]] = None,
    end_date: Optional[Union[str, datetime, pd.Timestamp]] = None,
    field: Union[str, List[str]] = 'yield_to_maturity',
    source: Literal['anbima', 'b3', 'all'] = 'all',
    category: Optional[Literal['credito_privado_di', 'credito_privado_ipca', 'titulos_publicos']] = None) -> Union[pd.DataFrame, pd.Series]:
    """Get time series data for one or more fixed income indicators from the database.

    Args:
        code: Single indicator code, list of codes, or None to retrieve all codes.
        start_date: Optional start date filter as string 'YYYY-MM-DD', datetime, or pandas Timestamp.
        end_date: Optional end date filter as string 'YYYY-MM-DD', datetime, or pandas Timestamp.
        field: Field or list of fields to retrieve (default: 'yield_to_maturity').
        source: Source of the data (default: 'all'). Can be 'anbima', 'b3', or 'all'.
        category: Data category. One of 'credito_privado_di', 'credito_privado_ipca', 'titulos_publicos', or None.
    Returns:
        pd.Series or pd.DataFrame:
        - A Series if a single code and a single field are requested.
        - A DataFrame with fields as columns if a single code and multiple fields are requested.
        - A DataFrame with codes as columns if multiple codes and a single field are requested.
        - A DataFrame with a MultiIndex (code, field) for columns if multiple codes and fields are requested.
        The columns will be ordered according to the input lists of codes and fields if provided.

    Raises:
        ValueError: If dates are in invalid format or if end_date is before start_date.
    """
    codes = []
    if code is not None:
        if isinstance(code, str):
            codes = [code]
        elif isinstance(code, list):
            codes = code
        else:
            raise ValueError("code must be a string or list of strings")

        if not all(isinstance(c, str) and c for c in codes):
            raise ValueError("All codes must be non-empty strings")

    if isinstance(field, str):
        fields = [field]
    elif isinstance(field, list):
        fields = field
    else:
        raise ValueError("field must be a string or list of strings")

    if not all(isinstance(f, str) and f for f in fields):
        raise ValueError("All fields must be non-empty strings")

    start_date_str = None
    end_date_str = None

    if start_date is not None:
        if isinstance(start_date, str):
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                start_date_str = start_date
            except ValueError:
                raise ValueError("start_date string must be in YYYY-MM-DD format")
        elif isinstance(start_date, (datetime, pd.Timestamp)):
            start_dt = start_date
            start_date_str = start_dt.strftime("%Y-%m-%d")
        else:
            raise ValueError("start_date must be a string, datetime, or pandas Timestamp")

    if end_date is not None:
        if isinstance(end_date, str):
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                end_date_str = end_date
            except ValueError:
                raise ValueError("end_date string must be in YYYY-MM-DD format")
        elif isinstance(end_date, (datetime, pd.Timestamp)):
            end_dt = end_date
            end_date_str = end_dt.strftime("%Y-%m-%d")
        else:
            raise ValueError("end_date must be a string, datetime, or pandas Timestamp")

    if start_date is not None and end_date is not None and start_dt > end_dt:
        raise ValueError("end_date cannot be before start_date")

    if category == 'credito_privado_di':
        table_name = 'renda_fixa_historico'
        cols = ['date', 'code', 'field', 'value']
        date_cols = ['date']
    elif category == 'credito_privado_ipca':
        table_name = 'renda_fixa_historico'
        cols = ['date', 'code', 'value']
        date_cols = ['date']
    elif category == 'titulos_publicos':
        return _get_titulos_publicos(codes, fields, source, start_date_str, end_date_str)
    elif category is None:
        table_name = 'renda_fixa_historico'
        cols = ['date', 'code', 'field', 'value', 'source']
        date_cols = ['date']
    else:
        raise ValueError("Invalid category")

    table_has_source = table_name == 'renda_fixa_historico'
    if source == 'all' and table_has_source and 'source' not in cols:
        cols = cols + ['source']

    fields_str = "','".join(fields)
    cols_str = ",".join(cols)
    query = f"""
        SELECT {cols_str}
        FROM {table_name}
        WHERE field IN ('{fields_str}')
    """

    if source != 'all' and table_has_source:
        query += f" AND source = '{source}'"

    if codes:
        codes_str = "','".join(codes)
        query += f" AND code IN ('{codes_str}')"

    if start_date_str:
        query += f" AND date >= '{start_date_str}'"
    if end_date_str:
        query += f" AND date <= '{end_date_str}'"

    query += " ORDER BY date, code, field"

    df = read_sql(query, date_columns=date_cols)
    if category == 'credito_privado_ipca':
        return df

    if df.empty:
        if codes:
            raise ValueError(f"No data found for code(s) {codes} with field(s) {fields}")
        else:
            raise ValueError(f"No data found for any codes with field(s) {fields}")

    group_cols = ['code', 'field']
    if 'source' in df.columns:
        group_cols.append('source')
    df = df.pivot_table(
        index='date',
        columns=group_cols,
        values='value'
    )

    if source == 'all':
        if code is not None:
            if len(codes) == 1 and len(fields) == 1:
                df = df.droplevel(['code', 'field'], axis=1)
                return df
            elif len(codes) == 1:
                df = df.droplevel('code', axis=1)
                try:
                    sources = list(df.columns.get_level_values('source').unique())
                    new_columns = pd.MultiIndex.from_product([fields, sources], names=['field', 'source'])
                    df = df.reindex(columns=new_columns)
                except Exception:
                    pass
            elif len(fields) == 1:
                df = df.droplevel('field', axis=1)
                try:
                    sources = list(df.columns.get_level_values('source').unique())
                    new_columns = pd.MultiIndex.from_product([codes, sources], names=['code', 'source'])
                    df = df.reindex(columns=new_columns)
                except Exception:
                    pass
        else:
            if len(fields) == 1:
                df = df.droplevel('field', axis=1)
    else:
        from itertools import product
        if code is not None:
            if len(codes) == 1 and len(fields) == 1:
                series = df.iloc[:, 0]
                series.name = fields[0]
                return series
            elif len(codes) == 1:
                df = df.droplevel('code', axis=1)
                df = df.reindex(columns=fields)
            elif len(fields) == 1:
                df = df.droplevel('field', axis=1)
                df = df.reindex(columns=codes)
            else:
                new_columns = list(product(codes, fields))
                df = df.reindex(columns=new_columns)
        elif len(fields) == 1:
            df = df.droplevel('field', axis=1)

    df = df.dropna(how='all', axis=1)

    return df
