"""Estimação par a par de vols/correlações do RBS e correção PSD. Offline."""
import numpy as np
import pandas as pd
import pytest

from persevera_tools.quant_research.risk_budgeting_spectrum.estimation import (
    ClassSpec, estimate_class_stats, nearest_correlation)

RF, ALT, RV = "Renda Fixa", "Alternativos", "Renda Variável"
SPECS = [ClassSpec("Tsy", RF, "IEF"), ClassSpec("Trend", ALT, "DBMF"), ClassSpec("EUA", RV, "SPX")]


def _prices(n_weeks=600, short_from=400, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2005-01-07", periods=n_weeks, freq="W-FRI")
    z = rng.normal(size=(n_weeks, 3))
    steps = pd.DataFrame({
        "IEF": 0.01 * z[:, 0],
        "SPX": 0.025 * (0.5 * z[:, 0] + np.sqrt(0.75) * z[:, 1]),
        "DBMF": 0.015 * z[:, 2],
    }, index=idx)
    px = np.exp(steps.cumsum())
    px.iloc[:short_from, px.columns.get_loc("DBMF")] = np.nan   # DBMF começa depois
    return px


def test_nearest_correlation_corrige_matriz_nao_psd():
    bad = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])
    assert np.linalg.eigvalsh(bad).min() < 0
    fixed = nearest_correlation(bad)
    assert np.linalg.eigvalsh(fixed).min() > -1e-8
    np.testing.assert_allclose(np.diag(fixed), 1.0)
    np.testing.assert_allclose(fixed, fixed.T)


def test_nearest_correlation_mantem_matriz_psd():
    ok = np.array([[1.0, 0.3], [0.3, 1.0]])
    np.testing.assert_allclose(nearest_correlation(ok), ok)


def test_pairwise_usa_historico_proprio_de_cada_classe():
    px = _prices()
    s = estimate_class_stats(SPECS, px, method="pairwise")
    assert s.coverage.loc["Tsy", "semanas"] == 599
    assert s.coverage.loc["Trend", "semanas"] == 199
    np.testing.assert_allclose(s.vols["EUA"], 0.025 * np.sqrt(52), rtol=0.12)
    assert abs(s.corr.loc["Tsy", "EUA"] - 0.5) < 0.1
    assert s.n_weeks == 199


def test_common_encurta_janela_para_classe_mais_curta():
    s = estimate_class_stats(SPECS, _prices(), method="common")
    assert set(s.coverage["semanas"]) == {199}


def test_class_start_corta_inicio_de_uma_classe():
    s = estimate_class_stats(SPECS, _prices(), method="pairwise", class_start={"EUA": "2010-01-01"})
    assert s.coverage.loc["EUA", "inicio"] >= pd.Timestamp("2010-01-01")
    assert s.coverage.loc["Tsy", "inicio"] < pd.Timestamp("2006-01-01")


def test_pairwise_falha_com_historico_curto():
    with pytest.raises(ValueError, match="menos de"):
        estimate_class_stats(SPECS, _prices(short_from=580), method="pairwise")


def test_metodo_invalido():
    with pytest.raises(ValueError, match="method"):
        estimate_class_stats(SPECS, _prices(), method="ewma")
