"""Checagens de dados do RBS. Offline, com séries sintéticas."""
import numpy as np
import pandas as pd
import pytest

from persevera_tools.quant_research.risk_budgeting_spectrum import data_checks as dc

HOJE = pd.Timestamp("2026-11-26")


def _serie(n=1500, vol=0.01, fim="2026-11-25", seed=0) -> pd.Series:
    idx = pd.bdate_range(end=fim, periods=n)
    r = np.random.default_rng(seed).normal(0.0003, vol, n)
    return pd.Series(100 * np.cumprod(1 + r), index=idx)


def test_serie_limpa_nao_tem_alerta():
    out = dc.checar_serie(_serie(), HOJE)
    assert not any([out["atrasada"], out["buracos"], out["muitos_zeros"], out["vol_sensivel"]])
    assert out["nao_positivos"] == 0 and out["dias_sem_dado"] == 1
    assert out["vol_%"] == pytest.approx(0.01 * np.sqrt(252) * 100, rel=0.1)


def test_serie_atrasada_e_marcada():
    out = dc.checar_serie(_serie(fim="2026-11-16"), HOJE)
    assert out["atrasada"] and out["dias_sem_dado"] == 10
    assert not dc.checar_serie(_serie(fim="2026-11-23"), HOJE)["atrasada"]    # fim de semana + 1


def test_buraco_acima_do_limite():
    s = _serie()
    s = s.drop(s.index[700:712])                                              # ~16 dias corridos sem dado
    out = dc.checar_serie(s, HOJE)
    assert out["buracos"] and out["gaps_acima"] == 1 and out["maior_gap"] > dc.MAX_GAP_DIAS


def test_valor_nao_positivo_conta():
    s = _serie()
    s.iloc[1000] = 0.0
    assert dc.checar_serie(s, HOJE)["nao_positivos"] == 1


def test_muitos_retornos_zero_sugerem_valor_repetido():
    s = _serie()
    s.iloc[800:900] = s.iloc[800]                                             # 100 dias repetidos
    out = dc.checar_serie(s, HOJE)
    assert out["retornos_zero"] >= 99 and out["muitos_zeros"]


def test_extremo_real_aparece_no_top3_e_muda_a_vol():
    s = _serie(vol=0.002)
    s.iloc[1200:] = s.iloc[1200:] * 0.8                                       # queda de 20% num dia
    out = dc.checar_serie(s, HOJE)
    assert out["extremos"] >= 1 and out["maior_queda_%"] == pytest.approx(-20, abs=1.5)
    assert out["vol_sem_extremos_%"] < out["vol_%"] and out["vol_sensivel"]
    assert s.index[1200].strftime("%Y-%m-%d") in out["top3"]


def test_janela_de_15_anos_ignora_o_dado_antigo():
    s = _serie(n=5000)                                                        # ~20 anos
    out = dc.checar_serie(s, HOJE)
    assert out["n"] < 4000 and out["inicio_serie"] == s.index[0].date()


def test_serie_vazia_ou_ausente_e_atrasada():
    df = dc.checar_series(pd.DataFrame({"a": _serie()}), HOJE, esperadas=["a", "b"])
    assert list(df.index) == ["a", "b"] and df.loc["b", "n"] == 0 and df.loc["b", "atrasada"]


def test_com_alerta_filtra_so_o_que_tem_alerta():
    precos = pd.DataFrame({"ok": _serie(), "velha": _serie(fim="2026-11-10", seed=1)})
    ruim = dc.com_alerta(dc.checar_series(precos, HOJE))
    assert list(ruim.index) == ["velha"]
