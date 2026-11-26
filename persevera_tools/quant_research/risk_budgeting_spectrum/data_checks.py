"""Checagens de qualidade das séries que alimentam o RBS (proxies das classes de ativo).

Funções puras (sem banco nem Fibery): quem chama traz os preços diários. Para cada série:

- frescor: último dado e dias sem dado em relação a ``hoje``;
- buracos: maior intervalo entre observações e quantos passam de ``max_gap_dias``;
- valores inválidos (zero ou negativos) e retornos exatamente zero (valor repetido);
- retornos extremos (|r| > ``k_extremo`` desvios-padrão) e a vol anualizada com e sem eles.

Retorno extremo não é erro por si: em crise ele é real e a vol com todos os dias é a correta.
A coluna ``vol_sem_extremos`` mostra só a sensibilidade; ``dif_vol`` marca as séries em que ela
passa de ``tol_vol``. Quem decide se um alerta é problema de dado é quem lê.

``ALERTAS`` lista o que ``checar_series`` marca: ``atrasada``, ``buracos``, ``nao_positivos``
(contagem; alerta se > 0), ``muitos_zeros`` e ``vol_sensivel``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DIAS_UTEIS_ANO = 252
JANELA_ANOS = 15
MAX_ATRASO_DIAS = 5       # dias corridos; cobre fim de semana e feriado
MAX_GAP_DIAS = 10
K_EXTREMO = 6.0
FRACAO_ZEROS = 0.03       # acima disto os retornos zero sugerem valor repetido
TOL_VOL = 0.15            # diferença relativa de vol com e sem extremos
ALERTAS = ("atrasada", "buracos", "nao_positivos", "muitos_zeros", "vol_sensivel")


def _top_extremos(ret: pd.Series, n: int = 3) -> str:
    maiores = ret.reindex(ret.abs().sort_values(ascending=False).index[:n])
    return " | ".join(f"{d:%Y-%m-%d} {r * 100:+.2f}%" for d, r in maiores.items())


def checar_serie(precos: pd.Series, hoje, janela_anos: int = JANELA_ANOS,
                 max_atraso_dias: int = MAX_ATRASO_DIAS, max_gap_dias: int = MAX_GAP_DIAS,
                 k_extremo: float = K_EXTREMO, fracao_zeros: float = FRACAO_ZEROS,
                 tol_vol: float = TOL_VOL) -> dict:
    """Métricas e alertas de uma série de níveis diários (índice de datas)."""
    hoje = pd.Timestamp(hoje)
    s = precos.dropna().sort_index()
    if s.empty:
        return {"n": 0, "atrasada": True, "buracos": False, "nao_positivos": False,
                "muitos_zeros": False, "vol_sensivel": False}
    janela = s.loc[s.index >= hoje - pd.DateOffset(years=janela_anos)]
    ret = (janela / janela.shift(1) - 1.0).iloc[1:]
    ret = ret.replace([np.inf, -np.inf], np.nan).dropna()
    gap = pd.Series(janela.index, index=janela.index).diff().dt.days.dropna()
    dias_sem_dado = int((hoje - s.index[-1]).days)
    sd = float(ret.std()) if len(ret) > 1 else np.nan
    extremo = ret.abs() > k_extremo * sd if np.isfinite(sd) else pd.Series(False, index=ret.index)
    anual = np.sqrt(DIAS_UTEIS_ANO) * 100
    vol = float(ret.std()) * anual if len(ret) > 1 else np.nan
    vol_sem = float(ret[~extremo].std()) * anual if (~extremo).sum() > 1 else np.nan
    dif_vol = vol_sem / vol - 1.0 if np.isfinite(vol) and np.isfinite(vol_sem) and vol > 0 else np.nan
    zeros = int((ret == 0).sum())
    out = {
        "inicio_serie": s.index[0].date(),
        "fim": s.index[-1].date(),
        "dias_sem_dado": dias_sem_dado,
        "n": int(len(janela)),
        "maior_gap": int(gap.max()) if len(gap) else 0,
        "gaps_acima": int((gap > max_gap_dias).sum()),
        "retornos_zero": zeros,
        "nao_positivos": int((janela <= 0).sum()),
        "extremos": int(extremo.sum()),
        "vol_%": vol,
        "vol_sem_extremos_%": vol_sem,
        "dif_vol": dif_vol,
        "maior_alta_%": float(ret.max()) * 100 if len(ret) else np.nan,
        "maior_queda_%": float(ret.min()) * 100 if len(ret) else np.nan,
        "top3": _top_extremos(ret) if len(ret) else "",
        "atrasada": dias_sem_dado > max_atraso_dias,
        "buracos": bool((gap > max_gap_dias).any()),
        "muitos_zeros": bool(len(ret) and zeros / len(ret) > fracao_zeros),
        "vol_sensivel": bool(np.isfinite(dif_vol) and abs(dif_vol) > tol_vol),
    }
    return out


def checar_series(precos: pd.DataFrame, hoje, esperadas: list[str] | None = None, **kwargs) -> pd.DataFrame:
    """Uma linha por série (coluna de ``precos``); ``esperadas`` sem coluna entram como atrasadas, n = 0."""
    colunas = list(precos.columns)
    for c in esperadas or []:
        if c not in colunas:
            colunas.append(c)
    linhas = {c: checar_serie(precos[c] if c in precos.columns else pd.Series(dtype=float), hoje, **kwargs)
              for c in colunas}
    df = pd.DataFrame(linhas).T
    df.index.name = "code"
    return df


def com_alerta(resultado: pd.DataFrame) -> pd.DataFrame:
    """Linhas do resultado com algum dos alertas ligado (``nao_positivos`` conta se > 0)."""
    booleanos = [a for a in ALERTAS if a != "nao_positivos"]
    marca = resultado[booleanos].astype(bool).any(axis=1) | (resultado["nao_positivos"].astype(float) > 0)
    return resultado[marca]
