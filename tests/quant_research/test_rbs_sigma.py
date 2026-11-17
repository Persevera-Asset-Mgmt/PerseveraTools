"""Checagem de viabilidade de σ do RBS (loader e overrides). Offline."""
import numpy as np
import pytest

from persevera_tools.quant_research.risk_budgeting_spectrum import (
    AssetClass, BucketConfig, RCTarget, SpectrumConfig, check_feasible_sigma,
    compute_feasible_vol_range)
from persevera_tools.quant_research.risk_budgeting_spectrum.core import check_sigma_range

RF, RV = "Renda Fixa", "Renda Variável"


def _config(**kw) -> SpectrumConfig:
    assets = [
        AssetClass("RF Curta", RF, "", 0.02, 1.0),
        AssetClass("RV Brasil", RV, "", 0.20, 0.60),
        AssetClass("RV EUA", RV, "", 0.16, 0.60),
    ]
    corr = np.full((3, 3), 0.2)
    np.fill_diagonal(corr, 1.0)
    base = dict(
        assets=assets,
        buckets={RF: BucketConfig(RF, RF), RV: BucketConfig(RV, RV)},
        rc_targets={RF: RCTarget(RF, 1.0, 0.2), RV: RCTarget(RV, 0.0, 0.8)},
        full_corr=corr, sigma_min_pct=2.5, sigma_max_pct=8.0, n_profiles=3,
        min_weight_threshold=0.0, calibration_name="Calibração Onshore - teste",
    )
    base.update(kw)
    return SpectrumConfig(**base)


def test_faixa_dentro_do_viavel_passa():
    check_feasible_sigma(_config())


def test_sigma_min_abaixo_do_minimo_viavel_falha_com_a_faixa_na_mensagem():
    lo, _ = compute_feasible_vol_range(_config())
    cfg = _config(sigma_min_pct=lo * 100 - 0.5)
    with pytest.raises(ValueError, match=r"Calibração Onshore - teste.*Sigma Min .* inviável.*= 2\.0000%"):
        check_feasible_sigma(cfg)


def test_sigma_max_acima_do_maximo_viavel_falha():
    with pytest.raises(ValueError, match=r"Sigma Max .* inviável"):
        check_feasible_sigma(_config(sigma_max_pct=40.0))


def test_override_de_sigma_e_revalidado():
    ok = _config()
    check_feasible_sigma(ok.with_overrides(sigma_max_pct=7.0))
    with pytest.raises(ValueError):
        check_feasible_sigma(ok.with_overrides(sigma_max_pct=40.0))


def test_sigma_max_nao_maior_que_min_falha():
    cfg = _config()
    with pytest.raises(ValueError, match="estritamente maior"):
        check_sigma_range(0.05, 0.05, cfg.vols_array, cfg.max_weights_array, cfg.full_corr)


def test_label_e_opcional():
    cfg = _config()
    with pytest.raises(ValueError) as exc:
        check_sigma_range(0.001, 0.05, cfg.vols_array, cfg.max_weights_array, cfg.full_corr)
    assert not str(exc.value).startswith("Calibração")
