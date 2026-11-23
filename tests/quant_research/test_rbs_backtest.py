"""Backtest walk-forward do RBS. Offline, com preços sintéticos."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from persevera_tools.quant_research.risk_budgeting_spectrum import (
    AssetClass, BucketConfig, RCTarget, SpectrumConfig)
from persevera_tools.quant_research.risk_budgeting_spectrum import backtest as bt
from persevera_tools.quant_research.risk_budgeting_spectrum.estimation import ewma_vols

RF, ALT, RV = "Renda Fixa", "Alternativos", "Renda Variável"
PROXIES = {"RF Curta": "rf_c", "RF Longa": "rf_l", "Ouro": "ouro", "RV Brasil": "rv_b", "RV EUA": None}


def _config() -> SpectrumConfig:
    assets = [
        AssetClass("RF Curta", RF, "", 0.02, 1.0),
        AssetClass("RF Longa", RF, "", 0.06, 1.0),
        AssetClass("Ouro", ALT, "", 0.15, 0.30),
        AssetClass("RV Brasil", RV, "", 0.20, 0.60),
        AssetClass("RV EUA", RV, "", 0.16, 0.60),
    ]
    corr = np.full((5, 5), 0.2)
    np.fill_diagonal(corr, 1.0)
    return SpectrumConfig(
        assets=assets,
        buckets={b: BucketConfig(b, b) for b in (RF, ALT, RV)},
        rc_targets={RF: RCTarget(RF, 1.0, 0.2), ALT: RCTarget(ALT, 0.0, 0.2), RV: RCTarget(RV, 0.0, 0.6)},
        full_corr=corr, sigma_min_pct=2.5, sigma_max_pct=6.0, n_profiles=2, min_weight_threshold=0.0)


def _precos(meses=240, seed=0, vols=(0.02, 0.06, 0.15, 0.20)) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2005-01-31", periods=meses, freq="ME")
    r = rng.normal(0.004, 1, (meses, len(vols))) * np.array(vols) / np.sqrt(12)
    return pd.DataFrame(100 * np.cumprod(1 + r, axis=0), index=idx, columns=["rf_c", "rf_l", "ouro", "rv_b"])


def test_datas_de_rebalanceamento_sao_o_ultimo_ponto_de_cada_trimestre():
    idx = pd.date_range("2020-01-31", periods=12, freq="ME")
    datas = bt.datas_rebalanceamento(idx, "2020-02-01", meses=3)
    assert [d.strftime("%Y-%m") for d in datas] == ["2020-03", "2020-06", "2020-09", "2020-12"]


def test_ewma_mensal_bate_com_o_estimador_diario_quando_a_frequencia_e_diaria():
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2018-01-01", periods=1500)
    p = pd.DataFrame({"a": 100 * np.cumprod(1 + rng.normal(0, 0.01, 1500)),
                      "b": 100 * np.cumprod(1 + rng.normal(0, 0.02, 1500))}, index=idx)
    mine = bt.ewma_vol_periodica(p.pct_change(), 252)
    ref = ewma_vols(p)
    assert mine["a"] == pytest.approx(ref["a"], rel=0.05)
    assert mine["b"] == pytest.approx(ref["b"], rel=0.05)


def test_estimar_exclui_classe_com_pouco_historico_e_usa_so_ewma_quando_pedido():
    p = _precos()
    p.loc[p.index[:200], "ouro"] = np.nan          # só 40 meses de dado
    vol, corr = bt.estimar(p, 12, so_ewma=("rv_b",))
    assert "ouro" in vol.index
    p.loc[p.index[:230], "ouro"] = np.nan          # 10 meses: sai
    vol2, corr2 = bt.estimar(p, 12)
    assert "ouro" not in vol2.index and "ouro" not in corr2.index
    ew = bt.ewma_vol_periodica(p.pct_change().iloc[-180:], 12)
    assert vol["rv_b"] == pytest.approx(ew["rv_b"])


def test_config_na_data_troca_vol_e_correlacao_e_exclui_classe_sem_dado():
    cfg = _config()
    vol = pd.Series({"rf_c": 0.03, "rf_l": 0.07, "rv_b": 0.25})            # sem ouro
    corr = pd.DataFrame(np.eye(3), index=vol.index, columns=vol.index)
    corr.loc["rf_c", "rf_l"] = corr.loc["rf_l", "rf_c"] = 0.9
    novo, excl = bt.config_na_data(cfg, PROXIES, vol, corr)
    d = {a.name: a for a in novo.assets}
    assert excl == ["Ouro"] and d["Ouro"].max_weight == bt.PESO_MAX_EXCLUIDA
    assert d["RF Longa"].default_vol == 0.07
    assert d["RV EUA"].default_vol == 0.16                                   # sem proxy: manual
    assert novo.full_corr[0, 1] == pytest.approx(0.9, abs=1e-6)
    assert novo.full_corr[3, 4] == pytest.approx(0.2, abs=1e-6)              # par com lado manual
    assert cfg.assets[2].max_weight == 0.30                                  # original intacto


def test_realizar_aplica_pesos_com_deriva_e_mede_giro():
    idx = pd.date_range("2020-01-31", periods=6, freq="ME")
    ret = pd.DataFrame({"a": [0.0, 0.10, 0.0, 0.0, 0.0, 0.0], "b": 0.0}, index=idx)
    w = {1: np.array([0.5, 0.5])}
    pesos = {idx[0]: w, idx[2]: {1: np.array([0.5, 0.5])}}
    res = bt.realizar(pesos, {idx[0]: {1: 2.0}, idx[2]: {1: 2.0}}, ret)
    assert res.retornos.loc[idx[1], "P1"] == pytest.approx(0.05)
    # a carteira derivou para 0,5·1,1/1,05 em "a"; voltar a 50/50 move 2,38% do patrimônio
    assert res.giro.loc[idx[2], "P1"] == pytest.approx(0.5 * 1.1 / 1.05 - 0.5, rel=1e-6)


def test_rodar_nao_usa_dado_futuro():
    cfg, p = _config(), _precos()
    ret = p.pct_change().assign(**{"RV EUA": 0.0}).rename(
        columns={"rf_c": "RF Curta", "rf_l": "RF Longa", "ouro": "Ouro", "rv_b": "RV Brasil"}).fillna(0.0)
    ret = ret[[a.name for a in cfg.assets]]
    r1 = bt.rodar(cfg, PROXIES, p, ret, 12, "2022-01-31")
    t0 = r1.datas[0]
    p2 = p.copy()
    p2.loc[p2.index > t0] *= 1.5                                             # muda só o futuro
    r2 = bt.rodar(cfg, PROXIES, p2, ret, 12, "2022-01-31")
    for k in r1.pesos[t0]:
        assert r1.pesos[t0][k] == pytest.approx(r2.pesos[t0][k])
    assert list(r1.retornos.columns) == ["P1", "P2"]
    assert len(r1.datas) >= 12


def test_resumo_traz_vol_real_contra_alvo_e_giro():
    cfg, p = _config(), _precos()
    ret = p.pct_change().assign(**{"RV EUA": 0.0}).rename(
        columns={"rf_c": "RF Curta", "rf_l": "RF Longa", "ouro": "Ouro", "rv_b": "RV Brasil"}).fillna(0.0)
    ret = ret[[a.name for a in cfg.assets]]
    res = bt.rodar(cfg, PROXIES, p, ret, 12, "2022-01-31")
    out = bt.resumo(res, 12)
    assert {"retorno_aa_%", "vol_real_%", "alvo_%", "real/alvo", "max_dd_%", "giro_médio_%"} <= set(out.columns)
    assert (out["vol_real_%"] > 0).all() and out.loc["P2", "alvo_%"] > out.loc["P1", "alvo_%"]
