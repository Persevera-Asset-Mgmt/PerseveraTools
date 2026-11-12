"""TPF em renda_fixa_historico: code <tipo>_<YYYY-MM-DD> e gravação dupla."""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from persevera_tools.data.financial_data_service import FinancialDataService
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
