"""Motor do RBS (core.py): interpolação de RC, viabilidade e convergência. Offline."""
import warnings

import numpy as np
import pytest

from persevera_tools.quant_research.risk_budgeting_spectrum import (
    AssetClass, BucketConfig, RCTarget, SpectrumConfig, build_spectrum, compute_feasible_vol_range)
from persevera_tools.quant_research.risk_budgeting_spectrum.core import (
    _interpolate_rc_targets, _is_converged, _vol_curve)

RF, ALT, RV = "Renda Fixa", "Alternativos", "Renda Variável"


def _config(**kw) -> SpectrumConfig:
    assets = [
        AssetClass("RF Curta", RF, "", 0.02, 1.0),
        AssetClass("RF Longa", RF, "", 0.06, 1.0),
        AssetClass("Ouro", ALT, "", 0.15, 0.30),
        AssetClass("RV Brasil", RV, "", 0.20, 0.60),
        AssetClass("RV EUA", RV, "", 0.16, 0.60),
    ]
    corr = np.full((5, 5), 0.2)
    np.fill_diagonal(corr, 1.0)
    base = dict(
        assets=assets,
        buckets={b: BucketConfig(b, b) for b in (RF, ALT, RV)},
        rc_targets={RF: RCTarget(RF, 1.0, 0.2), ALT: RCTarget(ALT, 0.0, 0.2), RV: RCTarget(RV, 0.0, 0.6)},
        full_corr=corr, sigma_min_pct=2.5, sigma_max_pct=6.0, n_profiles=3, min_weight_threshold=0.0,
    )
    base.update(kw)
    return SpectrumConfig(**base)


def test_curva_de_vol_e_exponencial_e_toca_os_extremos():
    v = _vol_curve(0.02, 0.12, 5)
    assert v[0] == pytest.approx(0.02) and v[-1] == pytest.approx(0.12)
    assert v[1] / v[0] == pytest.approx(v[2] / v[1])


@pytest.mark.parametrize("gamma", [None, [1.0, 1.0, 1.0], [0.5, 1.2, 1.2]])
def test_interpolacao_soma_um_e_reproduz_extremos(gamma):
    p1, p10 = np.array([1.0, 0.0, 0.0]), np.array([0.2, 0.2, 0.6])
    out = _interpolate_rc_targets(p1, p10, 6, None if gamma is None else np.array(gamma))
    assert out.sum(axis=1) == pytest.approx(np.ones(6))
    assert out[0] == pytest.approx(p1) and out[-1] == pytest.approx(p10)


def test_curvatura_menor_que_um_transita_cedo():
    p1, p10 = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    linear = _interpolate_rc_targets(p1, p10, 5)
    concava = _interpolate_rc_targets(p1, p10, 5, np.array([0.5, 0.5]))
    assert concava[1, 1] > linear[1, 1]


def test_curvatura_invalida_falha():
    with pytest.raises(ValueError):
        _interpolate_rc_targets(np.array([1.0, 0.0]), np.array([0.5, 0.5]), 3, np.array([0.0, 1.0]))


def test_espectro_converge_e_respeita_restricoes():
    cfg = _config()
    r = build_spectrum(cfg)
    assert all(r["converged_per_profile"].values())
    for p, w in r["weights"].items():
        assert w.sum() == pytest.approx(1.0, abs=1e-6)
        assert (w <= cfg.max_weights_array + 1e-6).all() and (w >= -1e-6).all()
        assert r["vol_realized"][p] / 100 == pytest.approx(r["vol_targets"][p - 1], abs=5e-4)
        assert r["rc_mode_per_profile"][p] == "hard"
        assert r["risk_contrib"][p].sum() == pytest.approx(1.0)
        assert np.abs(r["rc_realized_bucket"][p] - r["rc_target_bucket"][p]).max() <= 1e-3


def test_sigma_inviavel_nao_converge_e_cai_em_penalidade():
    cfg = _config(sigma_max_pct=40.0)  # acima do que os Max Weight permitem
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        r = build_spectrum(cfg)
    assert not all(r["converged_per_profile"].values())


def test_faixa_viavel_contem_a_faixa_da_calibracao():
    lo, hi = compute_feasible_vol_range(_config())
    assert lo < 0.025 < 0.06 < hi


def test_is_converged_exige_rc_por_bucket():
    cov = np.diag([0.01, 0.04, 0.09])
    w = np.array([0.5, 0.3, 0.2])
    vol = float(np.sqrt(w @ cov @ w))
    mw, mrc = np.ones(3), np.full(3, np.nan)
    buckets = [[0], [1], [2]]
    rc = w * (cov @ w)
    rc = rc / rc.sum()
    assert _is_converged(w, cov, vol, mw, mrc, bucket_list=buckets, rc_target_bucket=rc)
    assert not _is_converged(w, cov, vol, mw, mrc, bucket_list=buckets,
                             rc_target_bucket=np.array([0.6, 0.2, 0.2]))


def test_max_rc_violado_reprova():
    cov = np.diag([0.0001, 0.09])
    w = np.array([0.5, 0.5])
    vol = float(np.sqrt(w @ cov @ w))
    assert not _is_converged(w, cov, vol, np.ones(2), np.array([np.nan, 0.5]))
