"""
Títulos públicos (TPF) em ``renda_fixa_historico``.

``renda_fixa_historico`` tem chave ``(code, date, field, source)`` e não tem
coluna de vencimento. Para guardar TPF nela, o vencimento entra no ``code``:
``<tipo_titulo>_<YYYY-MM-DD>``, por exemplo ``NTN-B_2035-05-15``.

Durante a transição (desde 2026-11-11) as cargas de TPF gravam nas duas
tabelas: ``anbima_titulos_publicos_historico`` (code = tipo, coluna
``maturity``) e ``renda_fixa_historico`` (este formato). O workflow do n8n
``wJU1cF110bRfljGZ`` faz o mesmo.
"""

import re
from typing import Tuple

import pandas as pd

TPF_CODE_SEPARATOR = "_"

# Mesmo predicado do índice parcial renda_fixa_historico_tpf_field_date_idx.
TPF_CODE_PATTERN = r"^(LTN|LFT|NTN-[A-Z])_\d{4}-\d{2}-\d{2}$"
_TPF_CODE_RE = re.compile(TPF_CODE_PATTERN)

RENDA_FIXA_COLUMNS = ["date", "code", "field", "value", "source"]


def tpf_code(tipo: str, maturity) -> str:
    """``tpf_code('NTN-B', '2035-05-15')`` → ``'NTN-B_2035-05-15'``."""
    return f"{str(tipo).strip()}{TPF_CODE_SEPARATOR}{pd.Timestamp(maturity):%Y-%m-%d}"


def is_tpf_code(code: str) -> bool:
    return bool(_TPF_CODE_RE.match(str(code)))


def split_tpf_code(code: str) -> Tuple[str, pd.Timestamp]:
    """``'NTN-B_2035-05-15'`` → ``('NTN-B', Timestamp('2035-05-15'))``."""
    if not is_tpf_code(code):
        raise ValueError(f"code de TPF fora do formato <tipo>_<YYYY-MM-DD>: {code!r}")
    tipo, maturity = code.split(TPF_CODE_SEPARATOR, 1)
    return tipo, pd.Timestamp(maturity)


def tpf_to_renda_fixa(df: pd.DataFrame, default_source: str = "anbima") -> pd.DataFrame:
    """
    Converte linhas no formato de ``anbima_titulos_publicos_historico``
    (``code, date, maturity, field, value[, source]``) para o formato de
    ``renda_fixa_historico`` (``date, code, field, value, source``), com o
    vencimento no code.

    Linhas sem vencimento ou sem valor são descartadas. ``source`` ausente ou
    nulo vira ``default_source`` (o provider ``anbima_titulos_publicos`` não
    preenche source, e a chave de ``renda_fixa_historico`` exige).
    """
    if df.empty:
        return pd.DataFrame(columns=RENDA_FIXA_COLUMNS)

    out = df.copy()
    out["maturity"] = pd.to_datetime(out["maturity"], errors="coerce")
    out = out.dropna(subset=["maturity", "value"])
    if out.empty:
        return pd.DataFrame(columns=RENDA_FIXA_COLUMNS)

    out["code"] = (
        out["code"].astype(str).str.strip()
        + TPF_CODE_SEPARATOR
        + out["maturity"].dt.strftime("%Y-%m-%d")
    )
    if "source" not in out.columns:
        out["source"] = default_source
    else:
        out["source"] = out["source"].where(out["source"].notna(), default_source)
    return out[RENDA_FIXA_COLUMNS].reset_index(drop=True)
