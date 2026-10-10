"""Caminho antigo do pipeline IHFA Long & Short; ver :mod:`.ihfa_subindices`."""

from .ihfa_subindices import INDEX_CODE, INDEX_FIELD, run_anbima_ihfa_ls_pipeline  # noqa: F401

if __name__ == "__main__":
    run_anbima_ihfa_ls_pipeline()
