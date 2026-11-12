"""TPF em renda_fixa_historico: code <tipo>_<YYYY-MM-DD> e gravação dupla."""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from persevera_tools.data.financial_data_service import FinancialDataService
from persevera_tools.fixed_income import data as fi_data
from persevera_tools.data.tpf_codes import (
    is_tpf_code,
    split_tpf_code,
    tpf_code,
    tpf_to_renda_fixa,
)


def test_tpf_code_e_split():
    assert tpf_code("NTN-B", "2035-05-15") == "NTN-B_2035-05-15"
    assert tpf_code(" LTN ", pd.Timestamp("2027-04-01 00:00")) == "LTN_2027-04-01"
    assert split_tpf_code("NTN-F_2031-01-01") == ("NTN-F", pd.Timestamp("2031-01-01"))


@pytest.mark.parametrize(
    "code, esperado",
    [
        ("NTN-B_2035-05-15", True),
        ("LFT_2032-12-01", True),
        ("NTN-C_2031-01-01", True),
        ("NTN-B", False),
        ("NTN-B 2035-05-15", False),
        ("ACRC21", False),
        ("CRA020001US", False),
    ],
)
def test_is_tpf_code(code, esperado):
    assert is_tpf_code(code) is esperado


def test_split_tpf_code_rejeita_fora_do_formato():
    with pytest.raises(ValueError):
        split_tpf_code("ACRC21")


def test_tpf_to_renda_fixa():
    df = pd.DataFrame({
        "code": ["NTN-B", "LTN", "LFT", "NTN-F"],
        "date": pd.to_datetime(["2026-11-10"] * 4),
        "maturity": [pd.Timestamp("2035-05-15"), "2027-04-01", None, pd.Timestamp("2031-01-01")],
        "field": ["yield_to_maturity"] * 4,
        "value": [7.7221, 13.7, 0.1, None],
        "source": ["anbima", None, "anbima", "anbima"],
    })
    out = tpf_to_renda_fixa(df)
    assert list(out.columns) == ["date", "code", "field", "value", "source"]
    # sem vencimento (LFT) e sem valor (NTN-F) ficam de fora
    assert out["code"].tolist() == ["NTN-B_2035-05-15", "LTN_2027-04-01"]
    assert out["source"].tolist() == ["anbima", "anbima"]


def test_tpf_to_renda_fixa_sem_coluna_source():
    # formato do provider legado anbima_titulos_publicos
    df = pd.DataFrame({
        "code": ["NTN-B"], "date": [pd.Timestamp("2026-11-10")],
        "maturity": [pd.Timestamp("2035-05-15")], "field": ["price_close"], "value": [4500.0],
    })
    assert tpf_to_renda_fixa(df)["source"].tolist() == ["anbima"]


def test_tpf_to_renda_fixa_vazio():
    assert tpf_to_renda_fixa(pd.DataFrame()).empty


def _service_with_feed(df):
    svc = FinancialDataService.__new__(FinancialDataService)
    svc.logger = MagicMock()
    for name in (
        "bloomberg", "sgs", "fred", "sidra", "anbima", "anbima_feed", "anbima_fundos",
        "cvm", "bcb_focus", "simplify", "invesco", "kraneshares", "investing_com",
        "debentures_com", "mdic", "b3", "mais_retorno", "investfy",
    ):
        setattr(svc, name, MagicMock())
    svc.anbima_feed.get_data.return_value = df
    return svc


TPF_ROW = pd.DataFrame({
    "code": ["NTN-B"], "date": [pd.Timestamp("2026-11-10")],
    "maturity": [pd.Timestamp("2035-05-15")], "field": ["yield_to_maturity"],
    "value": [7.7221], "source": ["anbima"],
})


def test_get_data_tpf_grava_nas_duas_tabelas():
    svc = _service_with_feed(TPF_ROW)
    with patch("persevera_tools.data.financial_data_service.to_sql") as to_sql:
        svc.get_data("anbima_feed_titulos_publicos_mercado_secundario")
    calls = {c.kwargs["table_name"]: c.kwargs for c in to_sql.call_args_list}
    assert set(calls) == {"anbima_titulos_publicos_historico", "renda_fixa_historico"}
    assert calls["anbima_titulos_publicos_historico"]["primary_keys"] == ["code", "date", "maturity", "field"]
    rf = calls["renda_fixa_historico"]
    assert rf["primary_keys"] == ["code", "date", "field", "source"]
    assert rf["data"]["code"].tolist() == ["NTN-B_2035-05-15"]
    assert "maturity" not in rf["data"].columns


def test_get_data_tpf_com_tabela_customizada_nao_duplica():
    svc = _service_with_feed(TPF_ROW)
    with patch("persevera_tools.data.financial_data_service.to_sql") as to_sql:
        svc.get_data(
            "anbima_feed_titulos_publicos_mercado_secundario",
            table_name="tmp_tpf", primary_keys=["code", "date", "maturity", "field"],
        )
    assert [c.kwargs["table_name"] for c in to_sql.call_args_list] == ["tmp_tpf"]


# --- get_series(category='titulos_publicos') lê renda_fixa_historico ---

def _get_series_tpf(**kwargs):
    retorno = pd.DataFrame({
        "date": pd.to_datetime(["2026-11-11"]), "code": ["NTN-B"],
        "maturity": pd.to_datetime(["2035-05-15"]), "value": [7.6756],
    })
    with patch.object(fi_data, "read_sql", return_value=retorno) as read_sql:
        out = fi_data.get_series(category="titulos_publicos", **kwargs)
    query = " ".join(read_sql.call_args.args[0].split())
    return out, query, read_sql.call_args.kwargs


def test_get_series_tpf_le_renda_fixa_com_predicado_do_indice():
    out, query, kwargs = _get_series_tpf(code="NTN-B", start_date="2026-11-01", end_date="2026-11-11")
    assert "FROM renda_fixa_historico" in query
    assert "anbima_titulos_publicos_historico" not in query
    # mesmo predicado do índice parcial renda_fixa_historico_tpf_field_date_idx
    assert r"code ~ '^(LTN|LFT|NTN-[A-Z])_\d{4}-\d{2}-\d{2}$'" in query
    assert "split_part(code, '_', 1) AS code" in query
    assert "split_part(code, '_', 2)::date AS maturity" in query
    assert "field IN ('yield_to_maturity')" in query
    assert "split_part(code, '_', 1) IN ('NTN-B')" in query
    assert "date >= '2026-11-01'" in query and "date <= '2026-11-11'" in query
    assert query.endswith("ORDER BY date, code, maturity")
    assert kwargs["date_columns"] == ["date", "maturity"]
    # mesmo formato de antes: o DataFrame cru, sem pivot
    assert list(out.columns) == ["date", "code", "maturity", "value"]


def test_get_series_tpf_source():
    _, query_all, _ = _get_series_tpf(code="NTN-B")
    assert "source" not in query_all
    _, query_anbima, _ = _get_series_tpf(code="NTN-B", source="anbima")
    assert "AND source = 'anbima'" in query_anbima


def test_get_series_tpf_sem_code_e_varios_fields():
    _, query, _ = _get_series_tpf(field=["yield_to_maturity", "price_close"])
    assert "field IN ('yield_to_maturity','price_close')" in query
    assert "IN ('NTN" not in query
