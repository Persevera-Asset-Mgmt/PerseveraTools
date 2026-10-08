import logging
import pandas as pd
import numpy as np
import time
import psycopg2
import psycopg2.extras
import sqlalchemy
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Optional, Dict, Any
from psycopg2.errors import UniqueViolation
from psycopg2 import sql

from ..config import settings
from .connection import get_db_engine
from ..utils.logging import get_logger, timed

# Get a logger for this module
logger = get_logger(__name__)


def _sanitize_for_psycopg2(data: pd.DataFrame) -> pd.DataFrame:
    """
    Convert pandas dtypes to values psycopg2 can adapt.

    Handles three cases:
    - Nullable extension dtypes (``Int64``, ``Float64``, etc.): ``pd.NA`` → ``None``
    - datetime64 columns: ``NaT`` → ``None``  (psycopg2 serializes pd.NaT as "NaT")
    - object columns: any remaining pd.NA / np.nan / pd.NaT → ``None``
    """
    out = data.copy()
    for col in out.columns:
        ser = out[col]
        if isinstance(ser.dtype, pd.api.extensions.ExtensionDtype):
            if pd.api.types.is_bool_dtype(ser):
                out[col] = ser.astype(object).where(ser.notna(), None)
            elif pd.api.types.is_numeric_dtype(ser):
                numeric = pd.to_numeric(ser, errors='coerce')
                out[col] = numeric.mask(
                    numeric.isin([float('inf'), float('-inf')])
                ).astype('float64')
            else:
                out[col] = ser.astype(object).where(ser.notna(), None)
        elif pd.api.types.is_datetime64_any_dtype(ser):
            # Iterating a datetime64[ns] Series yields pd.Timestamp or pd.NaT.
            # pd.isna(pd.NaT) is True, so this is the only reliable way to replace
            # NaT with Python None (Series.where/astype tricks may coerce None→NaN).
            out[col] = [None if pd.isna(v) else v for v in ser]
        elif ser.dtype == object:
            # Handles object columns that may contain np.nan, pd.NA, or pd.NaT
            out[col] = [None if pd.isna(v) else v for v in ser]
    return out


def _pandas_dtype_to_postgres(dtype) -> str:
    """Map a pandas dtype to a PostgreSQL column type for CREATE TABLE."""
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "TIMESTAMP"
    if pd.api.types.is_bool_dtype(dtype):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(dtype):
        return "BIGINT"
    if pd.api.types.is_float_dtype(dtype):
        return "DOUBLE PRECISION"
    return "TEXT"


def _ensure_table_exists(cursor, conn, table_name: str, data: pd.DataFrame) -> None:
    """
    Create ``table_name`` from ``data``'s schema if it does not already exist.

    Uses the live psycopg2 cursor instead of ``DataFrame.to_sql(engine, ...)``.
    Some pandas/SQLAlchemy combinations fail to treat ``Engine`` as a Connectable
    and then call ``engine.cursor()``, raising ``AttributeError``.
    """
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = %s
        )
        """,
        (table_name,),
    )
    if cursor.fetchone()[0]:
        return

    col_defs = [
        sql.SQL("{} {}").format(
            sql.Identifier(str(col)),
            sql.SQL(_pandas_dtype_to_postgres(data[col].dtype)),
        )
        for col in data.columns
    ]
    create_stmt = sql.SQL("CREATE TABLE {} ({})").format(
        sql.Identifier(table_name),
        sql.SQL(", ").join(col_defs),
    )
    logger.info(f"Creating table '{table_name}'")
    cursor.execute(create_stmt)
    conn.commit()


@timed
def to_sql(data: pd.DataFrame,
               table_name: str,
               primary_keys: list,
               update: bool,
               batch_size: int = 5000,
               only_changed: bool = False):
    """Upload data to SQL table with batch processing and conflict handling.

    ``only_changed`` (with ``update``) skips rewriting rows whose non-key
    columns are unchanged — re-uploading an overlapping window then writes
    only new or revised rows instead of churning the table.
    """
    logger.info(f"Uploading {len(data)} rows to table '{table_name}'")
    
    if len(data) == 0:
        logger.warning("No data to upload")
        return
    
    # Get database connection and engine
    engine = get_db_engine()
    conn = engine.raw_connection()
    cursor = conn.cursor()
    
    try:
        # Create table if it doesn't exist
        logger.debug(f"Ensuring table '{table_name}' exists")
        _ensure_table_exists(cursor, conn, table_name, data)
        
        # Prepare data for insertion
        data = _sanitize_for_psycopg2(data)
        # Final guard: pd.isna covers NaN, NaT, pd.NA and None uniformly.
        # Necessary because to_numpy() may surface NaT objects from datetime64 columns
        # that psycopg2 would serialize as the literal string "NaT" instead of NULL.
        data_tuples = [
            tuple(None if pd.isna(v) else v for v in row)
            for row in data.to_numpy()
        ]
        cols = ','.join(list(data.columns))
        
        # Create SQL query
        query = f"INSERT INTO {table_name} AS t ({cols}) VALUES %s"
        
        if update:
            # Add ON CONFLICT clause for upsert
            update_cols = [col for col in data.columns if col not in primary_keys]
            if not update_cols:
                logger.warning("No columns to update (all columns are primary keys)")
                return
                
            update_stmt = ', '.join([f"{col} = EXCLUDED.{col}" for col in update_cols])
            query += f" ON CONFLICT ({', '.join(primary_keys)}) DO UPDATE SET {update_stmt}"
            if only_changed:
                changed = ' OR '.join(f"t.{col} IS DISTINCT FROM EXCLUDED.{col}" for col in update_cols)
                query += f" WHERE {changed}"
        else:
            # Add ON CONFLICT DO NOTHING clause
            query += f" ON CONFLICT ({', '.join(primary_keys)}) DO NOTHING"
        
        # Process in batches
        total_batches = (len(data_tuples) + batch_size - 1) // batch_size
        logger.info(f"Processing {total_batches} batches of size {batch_size}")
        
        start_time = time.time()
        for i in range(0, len(data_tuples), batch_size):
            batch = data_tuples[i:i+batch_size]
            batch_num = i // batch_size + 1
            
            batch_start = time.time()
            psycopg2.extras.execute_values(cursor, query, batch)
            conn.commit()
            batch_time = time.time() - batch_start
            
            logger.debug(f"Batch {batch_num}/{total_batches} completed in {batch_time:.2f}s")
            
            # Estimate time remaining
            elapsed = time.time() - start_time
            avg_time_per_batch = elapsed / batch_num
            remaining_batches = total_batches - batch_num
            estimated_time_left = avg_time_per_batch * remaining_batches
            
            if estimated_time_left > 60:
                estimated_time_left_minutes = estimated_time_left / 60
                logger.info(f"Estimated time remaining: {estimated_time_left_minutes:.2f} minutes")
            else:
                logger.info(f"Estimated time remaining: {estimated_time_left:.2f} seconds")

        total_duration = time.time() - start_time
        logger.info(f"All data uploaded successfully in {total_duration:.2f} seconds")
    except (psycopg2.Error, SQLAlchemyError) as e:
        logger.error(f"Database error: {e}", exc_info=True)
        raise
    except UniqueViolation as uv:
        logger.info("UniqueViolation")
    finally:
        cursor.close()
        conn.close()
        engine.dispose()

@timed
def read_sql(sql_query: str, params: Optional[Dict[str, Any]] = None, date_columns: Optional[List[str]] = None, raise_errors: bool = False) -> pd.DataFrame:
    """Read data from SQL table based on the provided query.

    Errors are logged and an empty DataFrame is returned, unless ``raise_errors``
    (for callers that combine several reads and must not mistake a failure for
    an empty result).
    """
    # Extract table name from query for logging
    table_name = "unknown"
    try:
        # Simple extraction of table name from query
        if "FROM" in sql_query.upper():
            parts = sql_query.upper().split("FROM")[1].strip().split()
            if parts:
                table_name = parts[0].strip().rstrip(';')
    except Exception:
        pass  # If we can't extract the table name, just use "unknown"
    
    logger.info(f"Reading from table '{table_name}'")
    
    engine = get_db_engine()
    try:
        with engine.connect() as connection:
            start_time = time.time()
            df = pd.read_sql_query(
                sqlalchemy.text(sql_query),
                con=connection,
                params=params,
                parse_dates=date_columns
            )
            duration = time.time() - start_time
            
            logger.info(f"Query returned {len(df)} rows in {duration:.2f} seconds")
            return df
    except Exception as e:
        logger.error(f"Error executing SQL query: {e}", exc_info=True)
        if raise_errors:
            raise
        return pd.DataFrame()
    finally:
        engine.dispose()
        