"""Estresse e drawdown do RBS. Offline, com preços sintéticos."""
import numpy as np
import pandas as pd
import pytest

from persevera_tools.quant_research.risk_budgeting_spectrum import stress


def _diario() -> pd.DataFrame:
    idx = pd.bdate_range("2020-02-10", "2020-03-31")
    a = pd.Series(100.0, index=idx)
    a.loc["2020-02-19":] = 100.0
    a.loc["2020-03-23":] = 66.0                       # −34% do dia 19/02 ao dia 23/03
    b = pd.Series(np.linspace(100, 110, len(idx)), index=idx)
    return pd.DataFrame({"a": a, "b": b})


def test_retorno_janela_usa_o_fechamento_das_duas_datas():
    px = _diario()
    r = stress.retorno_janela(px, "2020-02-19", "2020-03-23")
    assert r["a"] == pytest.approx(-0.34)
    assert r["b"] == pytest.approx(px.loc["2020-03-23", "b"] / px.loc["2020-02-19", "b"] - 1)


def test_retorno_janela_usa_o_ultimo_fechamento_em_data_sem_pregao():
    px = _diario()
    # 21/03/2020 é sábado: vale o fechamento de sexta, 20/03
    assert stress.retorno_janela(px, "2020-02-19", "2020-03-21")["b"] == pytest.approx(
        px.loc["2020-03-20", "b"] / px.loc["2020-02-19", "b"] - 1)


def test_retorno_janela_sem_dado_na_data_fica_nan():
    px = _diario()
    assert np.isnan(stress.retorno_janela(px, "2019-01-02", "2020-03-23")["a"])    # antes do início da série
    px.loc["2020-03-10":, "b"] = np.nan                                              # série parou
    assert np.isnan(stress.retorno_janela(px, "2020-02-19", "2020-03-31")["b"])
    assert stress.retorno_janela(px, "2020-02-19", "2020-03-12")["b"] == pytest.approx(
        px.loc["2020-03-09", "b"] / px.loc["2020-02-19", "b"] - 1)                   # dentro da tolerância de 7 dias


def test_retornos_janelas_tem_uma_linha_por_janela():
    px = _diario()
    out = stress.retornos_janelas(px, {"covid": ("2020-02-19", "2020-03-23"), "fora": ("2018-01-02", "2018-02-01")})
    assert list(out.index) == ["covid", "fora"] and list(out.columns) == ["a", "b"]
    assert out.loc["covid", "a"] == pytest.approx(-0.34) and out.loc["fora"].isna().all()


def test_janelas_padrao_tem_as_cinco_janelas_com_base_antes_do_fim():
    assert len(stress.JANELAS_ESTRESSE) == 5
    for a, b in stress.JANELAS_ESTRESSE.values():
        assert pd.Timestamp(a) < pd.Timestamp(b)


def test_estresse_carteira_pondera_e_ignora_peso_zero_sem_dado():
    ret = pd.DataFrame({"x": [-0.10, 0.02], "y": [-0.30, 0.05], "z": [np.nan, np.nan]}, index=["j1", "j2"])
    pesos = pd.Series({"x": 0.6, "y": 0.4, "z": 0.0})
    out = stress.estresse_carteira(pesos, ret)
    assert out["j1"] == pytest.approx(0.6 * -0.10 + 0.4 * -0.30)
    assert out["j2"] == pytest.approx(0.6 * 0.02 + 0.4 * 0.05)


def test_estresse_carteira_nan_quando_classe_com_peso_nao_tem_retorno():
    ret = pd.DataFrame({"x": [-0.10, -0.05], "y": [np.nan, 0.01]}, index=["j1", "j2"])
    out = stress.estresse_carteira(pd.Series({"x": 0.5, "y": 0.5}), ret)
    assert np.isnan(out["j1"]) and out["j2"] == pytest.approx(0.5 * -0.05 + 0.5 * 0.01)


def test_drawdown_historico_pior_mes_e_maximo_drawdown():
    idx = pd.date_range("2020-01-31", periods=5, freq="ME")
    ret = pd.DataFrame({"x": [0.02, -0.10, -0.05, 0.03, 0.04], "y": 0.0}, index=idx)
    pesos = pd.Series({"x": 0.5, "y": 0.5})
    out = stress.drawdown_historico(pesos, ret)
    carteira = 0.5 * ret["x"]
    curva = (1 + carteira).cumprod()
    assert out["pior_mes"] == pytest.approx(-0.05)
    assert out["max_dd"] == pytest.approx(((curva / curva.cummax()) - 1).min())
    assert out["desde"] == idx[0] and out["meses"] == 5


def test_drawdown_historico_comeca_quando_todas_as_classes_com_peso_tem_dado():
    idx = pd.date_range("2020-01-31", periods=6, freq="ME")
    ret = pd.DataFrame({"x": 0.01, "y": [np.nan, np.nan, 0.02, 0.02, -0.04, 0.0], "z": np.nan}, index=idx)
    out = stress.drawdown_historico(pd.Series({"x": 0.5, "y": 0.5, "z": 0.0}), ret)
    assert out["desde"] == idx[2] and out["meses"] == 4        # classe z tem peso zero e não conta
    assert out["pior_mes"] == pytest.approx(0.5 * 0.01 + 0.5 * -0.04)


def test_drawdown_historico_sem_mes_em_comum_devolve_nan():
    idx = pd.date_range("2020-01-31", periods=3, freq="ME")
    ret = pd.DataFrame({"x": [0.01, np.nan, np.nan], "y": [np.nan, np.nan, 0.02]}, index=idx)
    out = stress.drawdown_historico(pd.Series({"x": 0.5, "y": 0.5}), ret)
    assert np.isnan(out["max_dd"]) and out["meses"] == 0


def test_choque_com_ativo_unico_so_escala_a_vol():
    base, choque = stress.choque_vol_correlacao(np.array([1.0]), np.array([[0.04]]))
    assert base == pytest.approx(0.2) and choque == pytest.approx(0.3)


def test_choque_aumenta_o_risco_de_carteira_diversificada():
    cov = np.array([[0.04, 0.0], [0.0, 0.04]])
    base, choque = stress.choque_vol_correlacao(np.array([0.5, 0.5]), cov)
    assert base == pytest.approx(np.sqrt(0.5 * 0.04))
    # ρ vai de 0 para 0,5 e as vols sobem 50%
    esperado = np.sqrt(2 * (0.5 ** 2) * (0.3 ** 2) * (1 + 0.5))
    assert choque == pytest.approx(esperado) and choque / base > 1.5
