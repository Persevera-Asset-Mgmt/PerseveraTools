"""Loader do RBS por livro (Onshore/Offshore) e estimação de vols/correlações. Offline."""
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from persevera_tools.quant_research.risk_budgeting_spectrum.estimation import (
    ClassSpec, correlation_pairs, estimate_class_stats)
from persevera_tools.quant_research.risk_budgeting_spectrum.loaders import fibery as ld

RF, ALT, RV = "Renda Fixa", "Alternativos", "Renda Variável"
ON, OFF = "Calibração On", "Calibração Off"

CLASSES = [  # nome, bucket, livro, vol
    ("RF Pré", RF, "Onshore", 0.03), ("Multimercado", ALT, "Onshore", 0.05), ("RV Brasil", RV, "Onshore", 0.18),
    ("Off Pós", RF, "Offshore", 0.01), ("Off Tsy", RF, "Offshore", 0.06), ("Off Ouro", ALT, "Offshore", 0.16),
    ("Off EUA", RV, "Offshore", 0.18), ("Off EM", RV, "Offshore", 0.23),
]


def _tables(livro_col=True, intra=None, calib_livros=("Onshore", "Offshore")):
    calib = pd.DataFrame({
        "fibery/id": ["c1", "c2"], "Name": [ON, OFF], "Data Calibração": ["2026-10-14", "2026-11-09"],
        "workflow/state": ["Aprovada", "Aprovada"], "Sigma Min": [0.03, 0.02], "Sigma Max": [0.10, 0.12],
        "N Profiles": [5, 5], "Min Weight Threshold": [0.0, 0.0], "Livro": list(calib_livros),
    })
    classes = pd.DataFrame({
        "fibery/id": [f"k{i}" for i in range(len(CLASSES))], "Name": [c[0] for c in CLASSES],
        "Classificação Layer 1": [c[1] for c in CLASSES], "Proxy": "", "Livro": [c[2] for c in CLASSES],
    })
    intra = intra or {}
    params = pd.DataFrame([
        {"Calibração": ON if lv == "Onshore" else OFF, "Classe": n, "Default Vol": v,
         "Max Weight": 1.0, "Max RC": None, "RC Intra-Bucket": intra.get(n)}
        for n, _, lv, v in CLASSES
    ])
    rc = pd.DataFrame([
        {"Calibração": cal, "Classificação Layer 1": bk, "RC P1": p1, "RC P10": p10, "Curvatura RC": None}
        for cal in (ON, OFF) for bk, p1, p10 in ((RF, 1.0, 0.1), (ALT, 0.0, 0.2), (RV, 0.0, 0.7))
    ])
    corr = []
    for cal, lv in ((ON, "Onshore"), (OFF, "Offshore")):
        names = [c[0] for c in CLASSES if c[2] == lv]
        corr += [{"Calibração": cal, "Classe A": a, "Classe B": b, "Rho": 0.2}
                 for i, a in enumerate(names) for b in names[i + 1:]]
    tables = {
        ld.DB_CALIBRATION: calib, ld.DB_ASSET_CLASS: classes, ld.DB_PARAMETERS: params,
        ld.DB_RC_TARGET: rc, ld.DB_CORR_CLASSES: pd.DataFrame(corr),
    }
    if not livro_col:
        tables[ld.DB_CALIBRATION] = calib.drop(columns="Livro")
        tables[ld.DB_ASSET_CLASS] = classes.drop(columns="Livro")
    return tables


def _load(tables, **kw):
    with patch.object(ld, "read_fibery", side_effect=lambda name: tables[name].copy()):
        return ld.load_from_fibery(**kw)


def test_onshore_e_o_default_e_ignora_classes_offshore():
    cfg = _load(_tables())
    assert cfg.calibration_name == ON and cfg.calibration_livro == "Onshore"
    assert cfg.class_names == ["RF Pré", "Multimercado", "RV Brasil"]
    assert cfg.intra_rc_weights is None


def test_offshore_carrega_universo_e_rc_intra():
    intra = {"Off EUA": 0.7, "Off EM": 0.3}
    cfg = _load(_tables(intra=intra), livro="offshore")
    assert cfg.calibration_name == OFF and cfg.calibration_livro == "Offshore"
    assert cfg.class_names == ["Off Pós", "Off Tsy", "Off Ouro", "Off EUA", "Off EM"]
    assert set(cfg.intra_rc_weights) == {RV}
    np.testing.assert_allclose(cfg.intra_rc_weights[RV], [0.7, 0.3])


def test_sem_campo_livro_tudo_e_onshore():
    t = _tables(livro_col=False)
    onshore = [c for c in CLASSES if c[2] == "Onshore"]
    t[ld.DB_ASSET_CLASS] = t[ld.DB_ASSET_CLASS].iloc[: len(onshore)]
    t[ld.DB_CALIBRATION] = t[ld.DB_CALIBRATION].iloc[:1]
    cfg = _load(t)
    assert cfg.calibration_livro == "Onshore" and len(cfg.assets) == 3


def test_livro_vazio_conta_como_onshore():
    t = _tables(calib_livros=(None, "Offshore"))
    t[ld.DB_ASSET_CLASS].loc[t[ld.DB_ASSET_CLASS]["Livro"] == "Onshore", "Livro"] = ""
    assert _load(t).calibration_name == ON


@pytest.mark.parametrize("vazio", [pd.NA, np.nan, None, ""])
def test_livro_vazio_em_coluna_mista_conta_como_onshore(vazio):
    # read_fibery devolve pd.NA nos vazios quando a coluna tem valores preenchidos.
    t = _tables(calib_livros=(vazio, "Offshore"))
    classes = t[ld.DB_ASSET_CLASS].astype({"Livro": object})
    classes.loc[classes["Livro"] == "Onshore", "Livro"] = vazio
    t[ld.DB_ASSET_CLASS] = classes
    assert _load(t).calibration_name == ON
    assert _load(t, livro="Offshore").calibration_name == OFF


def test_sem_calibracao_do_livro_falha():
    t = _tables(calib_livros=("Onshore", "Onshore"))
    with pytest.raises(ld.FiberyLoaderError, match="Offshore"):
        _load(t, livro="Offshore")


def test_livro_invalido_falha():
    with pytest.raises(ld.FiberyLoaderError, match="inválido"):
        _load(_tables(), livro="Global")


def test_rc_intra_parcial_falha():
    with pytest.raises(ld.FiberyLoaderError, match="só em parte"):
        _load(_tables(intra={"Off EUA": 0.7}), livro="Offshore")


# ── estimação ─────────────────────────────────────────────────────────────────

SPECS = [ClassSpec("Off EUA", RV, "SPY"), ClassSpec("Off Tsy", RF, "IEF"), ClassSpec("Off Ouro", ALT, "GLD")]


def test_correlation_pairs_ordena_por_bucket():
    corr = pd.DataFrame(np.eye(3), index=[s.name for s in SPECS], columns=[s.name for s in SPECS])
    pairs = correlation_pairs(SPECS, corr)
    assert list(map(tuple, pairs[["Classe A", "Classe B"]].values)) == [
        ("Off Tsy", "Off Ouro"), ("Off Tsy", "Off EUA"), ("Off Ouro", "Off EUA")]


def test_estimate_class_stats_anualiza_vol_semanal():
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2020-01-03", periods=5 * 260)
    weekly = pd.date_range(idx[0], idx[-1], freq="W-FRI")
    steps = pd.DataFrame(rng.normal(0, 0.02, (len(weekly), 3)), index=weekly, columns=["SPY", "IEF", "GLD"])
    prices = np.exp(steps.cumsum()).reindex(idx).ffill().bfill()
    stats = estimate_class_stats(SPECS, prices)
    assert stats.n_weeks >= 250
    np.testing.assert_allclose(stats.vols.values, 0.02 * np.sqrt(52), rtol=0.15)
    assert list(stats.corr.index) == [s.name for s in SPECS]


def test_estimate_class_stats_proxy_ausente():
    with pytest.raises(ValueError, match="GLD"):
        estimate_class_stats(SPECS, pd.DataFrame(columns=["SPY", "IEF"]))
