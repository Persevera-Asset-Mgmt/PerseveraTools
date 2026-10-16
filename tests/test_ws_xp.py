"""Offline tests for XPWSProvider.get_consolidated_positions_d0 (Make API mocked; no network)."""
import json
import logging

import pandas as pd
import pytest

from persevera_tools.data.providers.ws_xp import XPWSProvider

PROXY_ID = 4980782


def _item_class(nome, itens):
    return {"nome": nome, "itens": itens, "saldo": 0, "saldoLiquido": 0, "valorAplicado": 0}


def _position_payload(code):
    """Same shape as GET /v1/consolidated-positions/customer/{code} (fields trimmed)."""
    return {
        "codigoCliente": code,
        "dadoAtualizado": False,
        "atualizeEm": "2026-10-16T13:44:02.8441017-03:00",
        "posicaoDetalhada": {
            "financeiro": {"codigoCliente": code, "valorDisponivel": 1000.5, "valorTotal": 1000.5,
                           "dataPosicaoD0": "2026-10-16T00:00:00-03:00"},
            "dataAtualizacao": "2026-10-16T11:47:40.7272159-03:00",
            "patrimonioDeclarado": {"totalPatrimonioDeclarado": 0},
            "patrimonioTotal": 1000.5 + 2410.0 + 5100.0 + 10500.0 + 2000.0 + 121.21,
            "patrimonioTotalLiquido": 1000.5 + 2410.0 + 5050.0 + 10400.0 + 2000.0 + 100.0,
            "valorDisponivel": 1000.5,
            "acoes": _item_class("Ações", [{
                "codigoCliente": code, "dataPosicao": "2026-10-16T00:00:00-03:00", "codigoAtivo": "ABCD3",
                "nomeEmpresaEmitente": "EMPRESA ABCD", "nomeTipoAtivo": "Ação", "quantidadeAbertura": 200,
                "quantidadeTotalComGarantias": 241, "precoUnitarioAtual": 10.0, "valorAtual": 2410.0,
            }]),
            "opcoes": _item_class("Opções", []),
            "fundos": _item_class("Fundos de Investimentos", [{
                "codigoCliente": code, "dataPosicao": "2026-10-15T00:00:00-03:00", "nomeFundo": "FUNDO RF",
                "cnpj": "00000000000191", "subgrupo": "Fundos de Renda Fixa Pós-Fixado", "quantidadeCotas": 1000.0,
                "valorCota": 5.1, "valorAplicado": 5000.0, "valorBruto": 5100.0, "valorLiquido": 5050.0,
            }]),
            "rendaFixa": _item_class("Renda Fixa", [{
                "codigoCliente": code, "dataPosicao": "2026-10-16T00:00:00-03:00", "codigoAtivo": "CRA0000001",
                "nomeAtivo": "CRA EXEMPLO", "nickName": "CRA EXEMPLO - IPCA", "nomeEmissor": "EMISSOR EXEMPLO",
                "categoria": "CRA", "dataVencimento": "2031-03-15T00:00:00-03:00",
                "dataCarencia": "0001-01-01T00:00:00", "quantidadeTotal": 10, "precoUnitario": 1050.0,
                "valorAplicado": 10000.0, "valorFinanceiroBruto": 10500.0, "valorFinanceiroLiquido": 10400.0,
            }]),
            "previdencia": _item_class("Previdência Privada", [{
                "codigoCliente": code, "codigoProposta": 1234567, "tipoPlano": "PGBL", "nomeSeguradora": "XP SEGUROS",
                "nomePlanoResumido": "XP PREV", "cnpj": "11111111000111", "quantidadeCotas": 1000.0,
                "valorUltimaCotas": 2.0, "valorReservaAcumulada": 2000.0, "valorAplicado": 0,
                "dataSaldoAtual": "2026-10-14T00:00:00-03:00",
            }]),
            "proventos": _item_class("Proventos de Ação", [{
                "codigoCliente": code, "dataPosicao": "2026-10-16T00:00:00-03:00", "codigoAtivo": "WXYZ4",
                "tipoProvento": "JUROS SOBRE CAPITAL PROPRIO", "quantidadeProvisionada": 800, "precoAtual": 121.21,
                "porcentagemIR": 17.5, "valorLiquidoAtual": 100.0,
            }]),
        },
    }


class _Resp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeMake:
    """Stands in for requests.Session: answers the proxy scenario run by path, like the Make API does."""

    def __init__(self, bodies):
        self.bodies = bodies  # path -> Python object returned by the XP (None → empty body)
        self.calls = []

    def post(self, url, **kwargs):
        assert url.endswith(f"/scenarios/{PROXY_ID}/run")
        path = kwargs["json"]["data"]["path"]
        self.calls.append(path)
        body = self.bodies[path]
        # The proxy hands back the XP body JSON-encoded twice (a string holding a JSON string).
        encoded = "" if body is None else json.dumps(json.dumps(body))
        return _Resp({"executionId": "x", "outputs": {"status_code": 200 if body is not None else 204,
                                                     "body": encoded}})


def _provider(bodies):
    p = XPWSProvider(make_api_token="make-token", access_token="static-token")
    p._session = _FakeMake(bodies)
    return p


def _pos_path(code):
    return f"/v1/consolidated-positions/customer/{code}"


def test_explodes_one_row_per_position_with_cash():
    p = _provider({_pos_path(900001): _position_payload(900001)})
    df = p.get_consolidated_positions_d0([900001])

    assert p._session.calls == [_pos_path(900001)]
    assert sorted(df["classe"]) == ["acoes", "caixa", "fundos", "previdencia", "proventos", "rendaFixa"]
    assert list(df.columns[:16]) == [
        "codigoCliente", "dataReferencia", "classe", "classeNome", "tipo", "codigoAtivo", "nomeAtivo", "emissor",
        "cnpj", "dataVencimento", "quantidade", "precoUnitario", "valorAplicado", "valorBruto", "valorLiquido",
        "atualizadoEm",
    ]
    by = df.set_index("classe")

    acoes = by.loc["acoes"]
    assert (acoes.codigoAtivo, acoes.tipo, acoes.nomeAtivo) == ("ABCD3", "Ação", "EMPRESA ABCD")
    assert acoes.quantidade == 241 and acoes.precoUnitario == 10.0 and acoes.valorBruto == 2410.0

    fundos = by.loc["fundos"]
    assert fundos.codigoAtivo == fundos.cnpj == "00000000000191"
    assert fundos.tipo == "Fundos de Renda Fixa Pós-Fixado"
    assert (fundos.quantidade, fundos.precoUnitario, fundos.valorBruto, fundos.valorLiquido) == (1000.0, 5.1, 5100.0, 5050.0)
    assert fundos.dataReferencia == pd.Timestamp("2026-10-15")

    rf = by.loc["rendaFixa"]
    assert (rf.tipo, rf.emissor, rf.nomeAtivo) == ("CRA", "EMISSOR EXEMPLO", "CRA EXEMPLO")
    assert rf.dataVencimento == pd.Timestamp("2031-03-15")
    assert (rf.quantidade, rf.valorBruto, rf.valorLiquido) == (10, 10500.0, 10400.0)

    prev = by.loc["previdencia"]
    assert prev.codigoAtivo == "1234567"  # codigoProposta, as text
    assert prev.tipo == "PGBL" and prev.valorBruto == 2000.0
    assert prev.dataReferencia == pd.Timestamp("2026-10-14")

    prov = by.loc["proventos"]
    assert prov.valorBruto == 121.21 and prov.valorLiquido == 100.0 and pd.isna(prov.precoUnitario)

    caixa = by.loc["caixa"]
    assert caixa.valorBruto == caixa.valorLiquido == 1000.5
    assert caixa.dataReferencia == pd.Timestamp("2026-10-16")

    assert (df["codigoCliente"] == 900001).all()
    assert df["atualizadoEm"].iloc[0] == pd.Timestamp("2026-10-16T14:47:40.7272159Z")


def test_totals_reconcile_with_patrimonio_total():
    payload = _position_payload(1)
    df = _provider({_pos_path(1): payload}).get_consolidated_positions_d0(1)
    pos = payload["posicaoDetalhada"]
    assert df["valorBruto"].sum() == pytest.approx(pos["patrimonioTotal"])
    assert df["valorLiquido"].sum() == pytest.approx(pos["patrimonioTotalLiquido"])


def test_without_cash_and_with_raw_fields():
    df = _provider({_pos_path(1): _position_payload(1)}).get_consolidated_positions_d0(
        [1], include_cash=False, include_raw=True
    )
    assert "caixa" not in set(df["classe"])
    assert "nickName" in df.columns and "porcentagemIR" in df.columns
    assert df.loc[df.classe == "rendaFixa", "dataCarencia"].iloc[0] == "0001-01-01T00:00:00"


def test_iterates_several_customers_one_proxy_run_each():
    p = _provider({_pos_path(1): _position_payload(1), _pos_path(2): _position_payload(2)})
    df = p.get_consolidated_positions_d0([1, "2"])
    assert p._session.calls == [_pos_path(1), _pos_path(2)]
    assert df.groupby("codigoCliente").size().to_dict() == {1: 6, 2: 6}


def test_defaults_to_enabled_customers():
    customers = [
        {"customerCode": 1, "status": "HABILITADO"},
        {"customerCode": 2, "status": "CANCELADO"},
        {"customerCode": 3, "status": "HABILITADO"},
    ]
    p = _provider({
        "/v2/customers": customers,
        _pos_path(1): _position_payload(1),
        _pos_path(3): _position_payload(3),
    })
    df = p.get_consolidated_positions_d0()
    assert p._session.calls == ["/v2/customers", _pos_path(1), _pos_path(3)]
    assert set(df["codigoCliente"]) == {1, 3}


def test_get_data_category():
    p = _provider({_pos_path(1): _position_payload(1)})
    df = p.get_data("consolidated_positions_d0", customer_codes=[1], include_cash=False)
    assert len(df) == 5


def test_unmapped_class_uses_generic_fields(caplog):
    payload = _position_payload(1)
    payload["posicaoDetalhada"]["compromissada"] = _item_class("Compromissadas", [{
        "codigoCliente": 1, "dataPosicao": "2026-10-16T00:00:00-03:00", "codigoAtivo": "COMP1",
        "nomeAtivo": "COMPROMISSADA", "quantidadeTotal": 1, "precoUnitario": 99.0,
        "valorFinanceiroBruto": 99.0, "valorFinanceiroLiquido": 98.0,
    }])
    with caplog.at_level(logging.WARNING):
        df = _provider({_pos_path(1): payload}).get_consolidated_positions_d0([1])
    row = df.set_index("classe").loc["compromissada"]
    assert (row.codigoAtivo, row.quantidade, row.valorBruto, row.valorLiquido) == ("COMP1", 1, 99.0, 98.0)
    assert "compromissada" in caplog.text


def test_empty_body_gives_no_rows():
    df = _provider({_pos_path(1): None}).get_consolidated_positions_d0([1])
    assert df.empty
    assert "valorBruto" in df.columns
