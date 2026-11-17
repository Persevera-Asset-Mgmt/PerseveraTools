"""
Robustez do espectro a perturbações de vol — checagem antes de aprovar uma calibração.

Motivo (docs/risk_budgeting_spectrum/avaliacao-modelo.md): o P10 converge na calibração
vigente, mas com vols um pouco diferentes (a cada recalibração, p.ex.) cai em modo
"penalty". Esta checagem refaz o espectro com vols perturbadas e diz quais perfis são
instáveis. Regra de recalibração (CLAUDE.md do módulo): se algum perfil for instável,
reduzir o σ máximo até convergir antes de aprovar.

Perturbação: cada vol é multiplicada por exp(N(0, sigma)), com semente fixa por sorteio;
as correlações não mudam. Um perfil é *estável* se convergiu no caso base e em todos os
sorteios *válidos*.

Sorteio válido: aquele em que o σ máximo da calibração ainda cabe no teto de vol viável do
universo perturbado. Se o teto cai abaixo do σ máximo, a calibração seria recusada pelo
loader nesse cenário (é calibração inválida, não fragilidade do modelo), então o sorteio é
ignorado (`exclude_infeasible=False` o mantém). Em `suggest_sigma_max` o conjunto de
sorteios ignorados é fixo, definido pelo σ máximo original: senão o sorteio voltaria a
valer assim que o candidato caísse abaixo do teto dele.
"""

from __future__ import annotations

import warnings
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from typing import Optional

import numpy as np
import pandas as pd

from .config import SpectrumConfig
from .core import build_spectrum, compute_feasible_vol_range
from .estimation import nearest_correlation

DEFAULT_SIGMA = 0.15      # desvio-padrão do log da vol (15%)
DEFAULT_CORR_SIGMA = 0.10  # desvio-padrão do z de Fisher de cada correlação (0 = não perturba)
DEFAULT_DRAWS = 6
DEFAULT_SEED = 100


@dataclass
class RunResult:
    """Resultado de um espectro (base ou sorteio)."""
    draw: int                          # 0 = caso base
    converged: dict[int, bool]
    modes: dict[int, str]
    weights: dict[int, np.ndarray]     # fração
    feasible_max_pct: Optional[float]  # teto de vol viável do universo (%), None se falhou


def perturb_vols(config: SpectrumConfig, sigma: float = DEFAULT_SIGMA, seed: int = 0) -> SpectrumConfig:
    """Nova config com cada vol × exp(N(0, sigma)); não modifica `config`."""
    rng = np.random.default_rng(seed)
    mult = np.exp(rng.normal(0.0, sigma, len(config.assets)))
    assets = [replace(a, default_vol=a.default_vol * float(m)) for a, m in zip(config.assets, mult)]
    return replace(config, assets=assets)


def perturb_corr(config: SpectrumConfig, sigma: float, seed: int = 0) -> SpectrumConfig:
    """
    Nova config com cada correlação ρ → tanh(arctanh(ρ) + N(0, sigma)), simétrica e
    reprojetada na correlação PSD mais próxima; não modifica `config`.
    """
    if sigma <= 0:
        return config
    rng = np.random.default_rng(seed)
    n = config.full_corr.shape[0]
    iu = np.triu_indices(n, k=1)
    z = np.arctanh(np.clip(config.full_corr[iu], -0.999999, 0.999999))
    noisy = np.tanh(z + rng.normal(0.0, sigma, len(z)))
    corr = np.eye(n)
    corr[iu] = noisy
    corr = corr + corr.T - np.eye(n)
    return replace(config, full_corr=nearest_correlation(corr))


def _run(args: tuple[int, SpectrumConfig]) -> RunResult:
    draw, config = args
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            feasible = compute_feasible_vol_range(config)[1] * 100.0
        except Exception:
            feasible = None
        res = build_spectrum(config)
    return RunResult(
        draw=draw,
        converged=dict(res["converged_per_profile"]),
        modes=dict(res["rc_mode_per_profile"]),
        weights={p: np.asarray(w) for p, w in res["weights"].items()},
        feasible_max_pct=feasible,
    )


def run_draws(
    config: SpectrumConfig,
    n_draws: int = DEFAULT_DRAWS,
    sigma: float = DEFAULT_SIGMA,
    seed: int = DEFAULT_SEED,
    n_jobs: int = 1,
    corr_sigma: float = DEFAULT_CORR_SIGMA,
) -> list[RunResult]:
    """
    Caso base (draw 0) e `n_draws` sorteios (draws 1..n, semente `seed + draw`; as
    correlações usam uma semente à parte, então os sorteios de vol não mudam com `corr_sigma`).
    """
    jobs = [(0, config)] + [
        (i, perturb_corr(perturb_vols(config, sigma, seed + i), corr_sigma, seed + 10_000 + i))
        for i in range(1, n_draws + 1)
    ]
    if n_jobs > 1:
        with ProcessPoolExecutor(max_workers=n_jobs) as ex:
            return list(ex.map(_run, jobs))
    return [_run(j) for j in jobs]


def robustness_report(
    config: SpectrumConfig,
    n_draws: int = DEFAULT_DRAWS,
    sigma: float = DEFAULT_SIGMA,
    seed: int = DEFAULT_SEED,
    n_jobs: int = 1,
    exclude_infeasible: bool = True,
    reference_sigma_max_pct: Optional[float] = None,
    corr_sigma: float = DEFAULT_CORR_SIGMA,
) -> dict:
    """
    Roda o caso base e os sorteios e resume por perfil.

    `exclude_infeasible`: ignora os sorteios cujo teto viável fica abaixo de
    `reference_sigma_max_pct` (default: o σ máximo da própria `config`).

    Retorna dict com:
      perfis         → DataFrame (índice = perfil): base_ok, falhas (sorteios válidos sem
                       convergir), penalty (sorteios válidos em modo penalty), estavel
      instaveis      → list[int] de perfis com estavel == False
      teto_viavel    → DataFrame por caso: teto_viavel_pct, sigma_max_sobre_teto, ignorado
      sorteios_ignorados → list[int] de sorteios fora do critério (teto < σ máximo)
      peso_std_pp    → DataFrame (classe × perfil): desvio-padrão do peso (p.p.) nos sorteios
                       válidos
      n_draws, sigma → parâmetros usados
    """
    runs = run_draws(config, n_draws, sigma, seed, n_jobs, corr_sigma)
    base, all_draws = runs[0], runs[1:]
    ref = config.sigma_max_pct if reference_sigma_max_pct is None else reference_sigma_max_pct

    def ignorado(r: RunResult) -> bool:
        # mesma tolerância do check_sigma_range (1e-6 em fração = 1e-4 em %)
        return (exclude_infeasible and r.feasible_max_pct is not None
                and ref > r.feasible_max_pct + 1e-4)

    ignorados = [r.draw for r in all_draws if ignorado(r)]
    draws = [r for r in all_draws if r.draw not in ignorados]
    profiles = sorted(base.converged)

    rows = []
    for p in profiles:
        falhas = sum(not r.converged[p] for r in draws)
        rows.append({
            "perfil": p,
            "base_ok": bool(base.converged[p]),
            "falhas": falhas,
            "penalty": sum(r.modes[p] == "penalty" for r in draws),
            "estavel": bool(base.converged[p]) and falhas == 0,
        })
    perfis = pd.DataFrame(rows).set_index("perfil")

    teto = pd.DataFrame({
        "teto_viavel_pct": [r.feasible_max_pct for r in runs],
    }, index=[r.draw for r in runs])
    teto["sigma_max_sobre_teto"] = config.sigma_max_pct / teto["teto_viavel_pct"]
    teto["ignorado"] = [r.draw in ignorados for r in runs]

    names = config.class_names
    if draws:
        cube = np.stack([[r.weights[p] for p in profiles] for r in draws])  # draws × perfis × classes
        std = cube.std(axis=0).T * 100.0                                    # classes × perfis
    else:
        std = np.zeros((len(names), len(profiles)))
    peso_std = pd.DataFrame(std, index=names, columns=profiles)

    return {
        "perfis": perfis,
        "instaveis": [int(p) for p in perfis.index[~perfis["estavel"]]],
        "teto_viavel": teto,
        "sorteios_ignorados": ignorados,
        "peso_std_pp": peso_std,
        "n_draws": n_draws,
        "sigma": sigma,
        "corr_sigma": corr_sigma,
    }


def suggest_sigma_max(
    config: SpectrumConfig,
    n_draws: int = DEFAULT_DRAWS,
    sigma: float = DEFAULT_SIGMA,
    seed: int = DEFAULT_SEED,
    step_pct: float = 0.5,
    max_steps: int = 4,
    n_jobs: int = 1,
    exclude_infeasible: bool = True,
    corr_sigma: float = DEFAULT_CORR_SIGMA,
) -> tuple[Optional[float], list[tuple[float, list[int]]]]:
    """
    Maior σ máximo (%) que deixa todos os perfis estáveis, testando `config.sigma_max_pct`
    e depois reduzindo de `step_pct` em `step_pct`, até `max_steps` reduções.

    Retorna (sugestão, tentativas); sugestão é None se nenhum valor testado serviu.
    `tentativas` = [(σ máximo testado, perfis instáveis), ...].
    """
    tried: list[tuple[float, list[int]]] = []
    for k in range(max_steps + 1):
        candidate = round(config.sigma_max_pct - k * step_pct, 6)
        if candidate <= config.sigma_min_pct:
            break
        rep = robustness_report(
            replace(config, sigma_max_pct=candidate), n_draws, sigma, seed, n_jobs,
            exclude_infeasible=exclude_infeasible,
            reference_sigma_max_pct=config.sigma_max_pct,  # conjunto de ignorados fixo
            corr_sigma=corr_sigma,
        )
        tried.append((candidate, rep["instaveis"]))
        if not rep["instaveis"]:
            return candidate, tried
    return None, tried
