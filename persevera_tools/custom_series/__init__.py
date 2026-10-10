"""
Séries customizadas construídas internamente (não vindas de providers).

Cada módulo implementa um pipeline que deriva um indicador e, opcionalmente,
faz upsert em ``indicadores`` com código ``persevera_*``.
"""

from .ihfa_subindices import (
    INDEX_CODE,
    INDEX_FIELD,
    SUBINDICES,
    build_index,
    classify_and_filter,
    fetch_composition,
    run_anbima_ihfa_ls_pipeline,
    run_ihfa_subindex,
)

__all__ = [
    "INDEX_CODE",
    "INDEX_FIELD",
    "SUBINDICES",
    "build_index",
    "classify_and_filter",
    "fetch_composition",
    "run_anbima_ihfa_ls_pipeline",
    "run_ihfa_subindex",
]
