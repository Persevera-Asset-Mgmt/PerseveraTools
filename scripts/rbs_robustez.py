"""Checa a robustez de uma calibração do RBS a perturbações de vol (antes de aprovar).

Lê a calibração do Fibery (``load_from_fibery``; só leitura), refaz o espectro com vols
perturbadas (cada vol × exp(N(0, sigma)), semente fixa) e lista os perfis instáveis: os que
não convergem no caso base ou em algum sorteio válido (sorteio em que o teto de vol viável
cai abaixo do σ máximo da calibração é ignorado; ``--incluir-inviaveis`` o mantém). Código de saída 1 se houver perfil
instável, 0 se todos forem estáveis.

Regra de recalibração (CLAUDE.md do módulo): perfil instável → reduzir o σ máximo até
convergir antes de aprovar. ``--sugerir-sigma-max`` testa σ máximos menores.

Uso::

    python scripts/rbs_robustez.py --livro Onshore --status Rascunho
    python scripts/rbs_robustez.py --livro Offshore --sorteios 10 --jobs 4
    python scripts/rbs_robustez.py --livro Onshore --status Rascunho --sugerir-sigma-max

Sem ``--status``, usa a calibração mais recente do livro, em qualquer status. Cada espectro
leva de ~30 s a ~3 min; ``--jobs`` roda os sorteios em paralelo.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from persevera_tools.quant_research.risk_budgeting_spectrum.loaders import load_from_fibery
from persevera_tools.quant_research.risk_budgeting_spectrum.robustness import (
    DEFAULT_DRAWS, DEFAULT_SEED, DEFAULT_SIGMA, robustness_report, suggest_sigma_max)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # console do Windows (cp1252) não codifica "σ"
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--livro", default="Onshore", choices=["Onshore", "Offshore"])
    ap.add_argument("--status", default=None,
                    help="status da calibração (default: a mais recente, qualquer status)")
    ap.add_argument("--sorteios", type=int, default=DEFAULT_DRAWS)
    ap.add_argument("--sigma", type=float, default=DEFAULT_SIGMA,
                    help="desvio-padrão do log da vol (default 0,15)")
    ap.add_argument("--semente", type=int, default=DEFAULT_SEED)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--incluir-inviaveis", action="store_true",
                    help="não ignora sorteios cujo teto viável fica abaixo do σ máximo")
    ap.add_argument("--sugerir-sigma-max", action="store_true",
                    help="se houver perfil instável, testa σ máximos menores (passo de 0,5 p.p.)")
    args = ap.parse_args()

    config = load_from_fibery(status=args.status, livro=args.livro)
    print(f"{config.calibration_name} [{config.calibration_status}]  σ {config.sigma_min_pct:.2f}%"
          f"–{config.sigma_max_pct:.2f}%  {len(config.assets)} classes  {config.n_profiles} perfis")
    print(f"{args.sorteios} sorteios, vol × exp(N(0, {args.sigma})), semente {args.semente}\n")

    excluir = not args.incluir_inviaveis
    rep = robustness_report(config, args.sorteios, args.sigma, args.semente, args.jobs,
                            exclude_infeasible=excluir)
    if rep["sorteios_ignorados"]:
        print("Sorteios ignorados (teto viável < σ máximo da calibração):", rep["sorteios_ignorados"])
        print()
    pd.set_option("display.width", 200)
    print(rep["perfis"].assign(perfil=lambda d: [f"P{p}" for p in d.index]).set_index("perfil").to_string())
    print("\nTeto de vol viável por caso (0 = base):")
    print(rep["teto_viavel"].round(3).to_string())
    print("\nDesvio-padrão do peso nos sorteios (p.p.), maior por classe:")
    print(rep["peso_std_pp"].max(axis=1).round(1).sort_values(ascending=False).to_string())

    if not rep["instaveis"]:
        print("\nTodos os perfis são estáveis.")
        return 0
    print(f"\nPerfis instáveis: {['P%d' % p for p in rep['instaveis']]}")
    if args.sugerir_sigma_max:
        best, tried = suggest_sigma_max(config, args.sorteios, args.sigma, args.semente,
                                   n_jobs=args.jobs, exclude_infeasible=excluir)
        for sig, bad in tried:
            print(f"  σ máximo {sig:.2f}%: instáveis {['P%d' % p for p in bad] or 'nenhum'}")
        print(f"Sugestão: σ máximo {best:.2f}%" if best is not None
              else "Nenhum σ máximo testado resolveu; revisar tetos de peso ou as vols.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
