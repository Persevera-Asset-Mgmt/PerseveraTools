"""
Robustez do espectro a perturbações de vol — checagem antes de aprovar uma calibração.

Motivo (docs/risk_budgeting_spectrum/avaliacao-modelo.md): o P10 converge na calibração
vigente, mas com vols um pouco diferentes (a cada recalibração, p.ex.) cai em modo
"penalty". Esta checagem refaz o espectro com vols perturbadas e diz quais perfis são
instáveis. Regra de recalibração (CLAUDE.md do módulo): se algum perfil for instável,
reduzir o σ máximo até convergir antes de aprovar.

Perturbação: cada vol é multiplicada por exp(N(0, sigma)), com semente fixa por sorteio;
as correlações não mudam. Um perfil é *estável* se convergiu no caso base e em todos os
sorteios.
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

DEFAULT_SIGMA = 0.15      # desvio-padrão do log da vol (15%)
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
) -> list[RunResult]:
    """Caso base (draw 0) e `n_draws` sorteios (draws 1..n, semente `seed + draw`)."""
    jobs = [(0, config)] + [
        (i, perturb_vols(config, sigma, seed + i)) for i in range(1, n_draws + 1)
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
) -> dict:
    """
    Roda o caso base e os sorteios e resume por perfil.

    Retorna dict com:
      perfis         → DataFrame (índice = perfil): base_ok, falhas (sorteios sem convergir),
                       penalty (sorteios em modo penalty), estavel
      instaveis      → list[int] de perfis com estavel == False
      teto_viavel    → DataFrame por sorteio: teto_viavel_pct, sigma_max_sobre_teto
      peso_std_pp    → DataFrame (classe × perfil): desvio-padrão do peso (p.p.) nos sorteios
      n_draws, sigma → parâmetros usados
    """
    runs = run_draws(config, n_draws, sigma, seed, n_jobs)
    base, draws = runs[0], runs[1:]
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
        "peso_std_pp": peso_std,
        "n_draws": n_draws,
        "sigma": sigma,
    }


def suggest_sigma_max(
    config: SpectrumConfig,
    n_draws: int = DEFAULT_DRAWS,
    sigma: float = DEFAULT_SIGMA,
    seed: int = DEFAULT_SEED,
    step_pct: float = 0.5,
    max_steps: int = 4,
    n_jobs: int = 1,
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
            replace(config, sigma_max_pct=candidate), n_draws, sigma, seed, n_jobs
        )
        tried.append((candidate, rep["instaveis"]))
        if not rep["instaveis"]:
            return candidate, tried
    return None, tried
