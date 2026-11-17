"""Checagem de robustez do RBS a perturbações de vol. Offline."""
import numpy as np
import pytest

from persevera_tools.quant_research.risk_budgeting_spectrum import (
    AssetClass, BucketConfig, RCTarget, SpectrumConfig)
from persevera_tools.quant_research.risk_budgeting_spectrum.robustness import (
    perturb_vols, robustness_report, suggest_sigma_max)

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
        full_corr=corr, sigma_min_pct=2.5, sigma_max_pct=6.0, n_profiles=2, min_weight_threshold=0.0,
    )
    base.update(kw)
    return SpectrumConfig(**base)


def test_perturbacao_e_reprodutivel_e_nao_altera_o_original():
    cfg = _config()
    a, b = perturb_vols(cfg, 0.15, seed=1), perturb_vols(cfg, 0.15, seed=1)
    c = perturb_vols(cfg, 0.15, seed=2)
    assert [x.default_vol for x in a.assets] == [x.default_vol for x in b.assets]
    assert [x.default_vol for x in a.assets] != [x.default_vol for x in c.assets]
    assert [x.default_vol for x in cfg.assets] == [0.02, 0.06, 0.15, 0.20, 0.16]
    assert (a.full_corr == cfg.full_corr).all()
    assert [x.max_weight for x in a.assets] == [x.max_weight for x in cfg.assets]


def test_sigma_zero_nao_perturba():
    cfg = _config()
    assert [x.default_vol for x in perturb_vols(cfg, 0.0, seed=3).assets] == [
        x.default_vol for x in cfg.assets]


def test_relatorio_de_calibracao_estavel():
    rep = robustness_report(_config(), n_draws=2, sigma=0.05)
    assert list(rep["perfis"].index) == [1, 2]
    assert rep["instaveis"] == []
    assert rep["perfis"]["estavel"].all()
    assert list(rep["teto_viavel"].index) == [0, 1, 2]
    assert (rep["teto_viavel"]["sigma_max_sobre_teto"] < 1).all()
    assert rep["peso_std_pp"].shape == (5, 2)
    assert (rep["peso_std_pp"].values >= 0).all()


def test_sigma_inviavel_aparece_como_perfil_instavel():
    rep = robustness_report(_config(sigma_max_pct=40.0), n_draws=0)
    assert rep["instaveis"], "σ máximo acima do teto viável deveria deixar perfil instável"
    assert not rep["perfis"].loc[rep["instaveis"], "estavel"].any()
    assert (rep["teto_viavel"]["sigma_max_sobre_teto"] > 1).any()


def test_sugestao_devolve_o_proprio_sigma_max_se_ja_for_estavel():
    best, tried = suggest_sigma_max(_config(), n_draws=1, sigma=0.05, max_steps=2)
    assert best == 6.0
    assert tried == [(6.0, [])]


def test_sugestao_none_quando_nada_resolve():
    best, tried = suggest_sigma_max(_config(sigma_max_pct=40.0), n_draws=0, step_pct=0.5, max_steps=1)
    assert best is None
    assert [t[0] for t in tried] == [40.0, 39.5]
    assert all(bad for _, bad in tried)


def test_sugestao_para_antes_de_chegar_ao_sigma_min():
    cfg = _config(sigma_min_pct=5.8, sigma_max_pct=6.0)
    best, tried = suggest_sigma_max(cfg, n_draws=0, step_pct=0.5, max_steps=4)
    assert [t[0] for t in tried] == [6.0] or best == 6.0
    assert all(sig > cfg.sigma_min_pct for sig, _ in tried)


# ---------------------------------------------------------------------------
# Sorteios válidos: teto viável abaixo do σ máximo = calibração inválida, ignorado

from persevera_tools.quant_research.risk_budgeting_spectrum import robustness as rb  # noqa: E402


def _run(draw, ok2=True, feasible=20.0):
    """Resultado simulado: perfil 1 sempre ok; perfil 2 converge se `ok2`."""
    w = {1: np.full(5, 0.2), 2: np.full(5, 0.2)}
    return rb.RunResult(draw=draw, converged={1: True, 2: ok2},
                        modes={1: "hard", 2: "hard" if ok2 else "penalty"},
                        weights=w, feasible_max_pct=feasible)


def test_sorteio_com_teto_abaixo_do_sigma_max_e_ignorado(monkeypatch):
    # σ máx = 6: o sorteio 1 (teto 5,0) é inválido; o 2 (teto 8,0) vale e passa
    monkeypatch.setattr(rb, "run_draws", lambda *a, **k: [
        _run(0), _run(1, ok2=False, feasible=5.0), _run(2, ok2=True, feasible=8.0)])
    rep = robustness_report(_config(), n_draws=2)
    assert rep["sorteios_ignorados"] == [1]
    assert rep["instaveis"] == []
    assert list(rep["teto_viavel"]["ignorado"]) == [False, True, False]


def test_sorteio_valido_que_falha_deixa_o_perfil_instavel(monkeypatch):
    monkeypatch.setattr(rb, "run_draws", lambda *a, **k: [
        _run(0), _run(1, ok2=True, feasible=8.0), _run(2, ok2=False, feasible=8.0)])
    rep = robustness_report(_config(), n_draws=2)
    assert rep["sorteios_ignorados"] == []
    assert rep["instaveis"] == [2]
    assert rep["perfis"].loc[2, "falhas"] == 1


def test_incluir_inviaveis_restaura_o_criterio_antigo(monkeypatch):
    monkeypatch.setattr(rb, "run_draws", lambda *a, **k: [
        _run(0), _run(1, ok2=False, feasible=5.0), _run(2, ok2=True, feasible=8.0)])
    rep = robustness_report(_config(), n_draws=2, exclude_infeasible=False)
    assert rep["sorteios_ignorados"] == []
    assert rep["instaveis"] == [2]


def test_teto_igual_ao_sigma_max_nao_e_ignorado(monkeypatch):
    monkeypatch.setattr(rb, "run_draws", lambda *a, **k: [
        _run(0), _run(1, ok2=False, feasible=6.0)])
    rep = robustness_report(_config(), n_draws=1)
    assert rep["sorteios_ignorados"] == []  # cabe exatamente
    assert rep["instaveis"] == [2]


def test_base_que_nao_converge_continua_instavel_mesmo_sem_sorteios_validos(monkeypatch):
    monkeypatch.setattr(rb, "run_draws", lambda *a, **k: [
        _run(0, ok2=False), _run(1, ok2=True, feasible=2.0)])
    rep = robustness_report(_config(), n_draws=1)
    assert rep["sorteios_ignorados"] == [1]
    assert rep["instaveis"] == [2]


def test_sugestao_usa_conjunto_fixo_de_sorteios_ignorados(monkeypatch):
    # Sorteio 1 (teto 5,0) sempre falha; o 2 (teto 8,0) só passa com σ máx ≤ 5,5.
    # O 1 é ignorado em todos os candidatos, mesmo quando o candidato (5,0) o alcança.
    def fake(config, *a, **k):
        return [_run(0), _run(1, ok2=False, feasible=5.0),
                _run(2, ok2=config.sigma_max_pct <= 5.5, feasible=8.0)]
    monkeypatch.setattr(rb, "run_draws", fake)
    best, tried = suggest_sigma_max(_config(), n_draws=2, step_pct=0.5, max_steps=4)
    assert best == 5.5
    assert tried == [(6.0, [2]), (5.5, [])]


def test_sugestao_sem_criterio_de_ignorados_nao_resolve(monkeypatch):
    def fake(config, *a, **k):
        return [_run(0), _run(1, ok2=False, feasible=5.0),
                _run(2, ok2=config.sigma_max_pct <= 5.5, feasible=8.0)]
    monkeypatch.setattr(rb, "run_draws", fake)
    best, tried = suggest_sigma_max(_config(), n_draws=2, step_pct=0.5, max_steps=3,
                                    exclude_infeasible=False)
    assert best is None
    assert all(bad for _, bad in tried)


def test_script_de_robustez_compila_e_aceita_os_argumentos():
    """O script não é importado pelos outros testes; pega erro de sintaxe e de argumentos."""
    import pathlib
    import subprocess
    import sys

    script = pathlib.Path(__file__).parents[2] / "scripts" / "rbs_robustez.py"
    compile(script.read_text(encoding="utf-8"), str(script), "exec")
    out = subprocess.run([sys.executable, "-X", "utf8", str(script), "--help"],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    assert "--incluir-inviaveis" in out.stdout and "--sugerir-sigma-max" in out.stdout


def test_perturb_corr_e_psd_simetrica_e_reprodutivel():
    from persevera_tools.quant_research.risk_budgeting_spectrum.robustness import perturb_corr
    cfg = _config()
    a, b = perturb_corr(cfg, 0.2, seed=1), perturb_corr(cfg, 0.2, seed=1)
    c = perturb_corr(cfg, 0.2, seed=2)
    assert (a.full_corr == b.full_corr).all()
    assert not np.allclose(a.full_corr, c.full_corr)
    assert np.allclose(a.full_corr, a.full_corr.T)
    assert np.allclose(np.diag(a.full_corr), 1.0)
    assert np.linalg.eigvalsh(a.full_corr).min() >= -1e-8
    assert not np.allclose(a.full_corr, cfg.full_corr)
    assert (cfg.full_corr[np.triu_indices(5, 1)] == 0.2).all()  # original intacto


def test_perturb_corr_sigma_zero_devolve_a_propria_config():
    from persevera_tools.quant_research.risk_budgeting_spectrum.robustness import perturb_corr
    cfg = _config()
    assert perturb_corr(cfg, 0.0, seed=3) is cfg


def test_sorteios_de_vol_nao_mudam_com_corr_sigma():
    from persevera_tools.quant_research.risk_budgeting_spectrum.robustness import run_draws
    cfg = _config()
    a = run_draws(cfg, 1, 0.1, 5, corr_sigma=0.0)[1]
    b = run_draws(cfg, 1, 0.1, 5, corr_sigma=0.2)[1]
    assert a.draw == b.draw == 1
    assert not np.allclose(a.weights[1], b.weights[1])  # a correlação mudou o resultado


def test_relatorio_registra_corr_sigma():
    rep = robustness_report(_config(), n_draws=1, sigma=0.05, corr_sigma=0.1)
    assert rep["corr_sigma"] == 0.1
    assert rep["instaveis"] == []
