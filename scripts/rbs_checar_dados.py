"""Checa a qualidade das séries de proxy que alimentam o RBS (antes de recalibrar ou aprovar).

Só leitura (Fibery e banco). Para cada classe com Proxy dos livros pedidos, lê o fechamento
diário de ``indicadores`` e mede (``data_checks.py``): frescor, buracos, valores não positivos,
retornos zero (valor repetido), retornos extremos (|r| > k desvios-padrão) e a vol anualizada com
e sem eles. Retorno extremo não é erro: em crise ele é real, e a coluna mostra só a sensibilidade.
Código de saída 1 se houver algum alerta.

Uso::

    python scripts/rbs_checar_dados.py
    python scripts/rbs_checar_dados.py --livro Offshore --status "Em Comitê" --max-atraso 3
    python scripts/rbs_checar_dados.py --extremo 5 --tol-vol 0.10 --todas
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import pandas as pd

from persevera_tools.db.operations import read_sql
from persevera_tools.quant_research.risk_budgeting_spectrum import data_checks as dc
from persevera_tools.quant_research.risk_budgeting_spectrum.loaders import load_from_fibery


def _proxies(livro: str, status: str | None) -> dict[str, list[str]]:
    """{proxy: [classes]} da calibração do livro; classes sem proxy ficam em ``""``."""
    cfg = load_from_fibery(status=status, livro=livro)
    out: dict[str, list[str]] = {}
    for a in cfg.assets:
        out.setdefault(a.proxy if isinstance(a.proxy, str) and a.proxy else "", []).append(a.name)
    return out


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # console do Windows (cp1252) não codifica "σ"
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--livro", default="todos", choices=["Onshore", "Offshore", "todos"])
    ap.add_argument("--status", default=None,
                    help="status da calibração (default: a mais recente do livro, qualquer status)")
    ap.add_argument("--anos", type=int, default=dc.JANELA_ANOS, help="janela, em anos")
    ap.add_argument("--max-atraso", type=int, default=dc.MAX_ATRASO_DIAS, help="dias corridos sem dado")
    ap.add_argument("--max-gap", type=int, default=dc.MAX_GAP_DIAS, help="buraco máximo entre observações (dias)")
    ap.add_argument("--extremo", type=float, default=dc.K_EXTREMO, help="retorno extremo, em desvios-padrão")
    ap.add_argument("--tol-vol", type=float, default=dc.TOL_VOL, help="dif. relativa da vol sem extremos")
    ap.add_argument("--todas", action="store_true", help="mostra também as séries sem alerta")
    args = ap.parse_args()

    livros = ["Onshore", "Offshore"] if args.livro == "todos" else [args.livro]
    por_livro = {l: _proxies(l, args.status) for l in livros}
    codes = sorted({p for m in por_livro.values() for p in m if p})
    inicio = (date.today() - timedelta(days=args.anos * 365 + 30)).isoformat()
    lista = ",".join(f"'{c}'" for c in codes)
    raw = read_sql(f"SELECT code, date, value FROM indicadores WHERE field = 'close' "
                   f"AND code IN ({lista}) AND date >= '{inicio}'")
    precos = raw.pivot(index="date", columns="code", values="value")
    precos.index = pd.to_datetime(precos.index)

    res = dc.checar_series(precos, pd.Timestamp.today().normalize(), esperadas=codes,
                           janela_anos=args.anos, max_atraso_dias=args.max_atraso, max_gap_dias=args.max_gap,
                           k_extremo=args.extremo, tol_vol=args.tol_vol)
    usadas = {c: [f"{l}: {', '.join(m[c])}" for l, m in por_livro.items() if c in m] for c in codes}
    res.insert(0, "classes", [" / ".join(usadas[c]) for c in res.index])
    mostrar = res if args.todas else dc.com_alerta(res)

    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 70)
    print(f"{len(codes)} proxies, janela de {args.anos} anos, extremo = {args.extremo:g}σ, "
          f"atraso máx. {args.max_atraso} d, buraco máx. {args.max_gap} d")
    sem = {l: m[""] for l, m in por_livro.items() if "" in m}
    if sem:
        print(f"Classes sem proxy (não checadas): {sem}")
    cols = ["classes", "fim", "dias_sem_dado", "n", "maior_gap", "retornos_zero", "extremos",
            "vol_%", "vol_sem_extremos_%", "maior_queda_%", "maior_alta_%", "top3"]
    print()
    if mostrar.empty:
        print("Nenhum alerta.")
    else:
        print(mostrar[cols + [a for a in dc.ALERTAS if a != "nao_positivos"]].round(2).to_string())
    ruim = dc.com_alerta(res)
    print(f"\nAlertas: {len(ruim)} de {len(res)} séries" + (f" ({', '.join(ruim.index)})" if len(ruim) else ""))
    return 1 if len(ruim) else 0


if __name__ == "__main__":
    sys.exit(main())
