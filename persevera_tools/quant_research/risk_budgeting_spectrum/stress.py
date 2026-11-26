"""Estresse e drawdown histórico das carteiras do espectro RBS.

O σ-alvo mede o risco em tempos normais; este módulo mostra o que as mesmas carteiras
teriam perdido em episódios reais e sob um choque de volatilidade e correlação. Funções
puras (sem banco, Fibery nem rede): quem chama traz os preços e os pesos.

- ``retornos_janelas``: retorno de cada classe em cada janela de ``JANELAS_ESTRESSE``.
- ``estresse_carteira``: retorno da carteira em cada janela.
- ``drawdown_historico``: pior mês e máximo drawdown da carteira **atual** aplicada ao
  histórico mensal, com rebalanceamento mensal para os pesos. Não é o backtest walk-forward
  (``backtest.py``), que re-estima os pesos a cada trimestre; aqui os pesos de hoje valem
  para todas as datas.
- ``choque_vol_correlacao``: σ da carteira com vols × ``CHOQUE_VOL`` e correlações
  ρ → ρ + ``CHOQUE_RHO``·(1 − ρ).

Retornos e pesos em fração (0,05 = 5%).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# nome -> (data do fechamento base, data do fechamento final). O retorno é P(final)/P(base) − 1.
# As datas casam com os retornos da análise de 2026-11-23 (docs/.../avaliacao-modelo.md §13):
# COVID e mai-jun/2018 reproduzem os retornos publicados; a janela de 2022 casa dentro de
# 0,25 p.p. (soma do erro de Ibovespa, S&P 500 e IMA-B 5+).
JANELAS_ESTRESSE: dict[str, tuple[str, str]] = {
    "Dia 26/10/2026": ("2026-10-23", "2026-10-26"),
    "Semana 26-30/10/2026": ("2026-10-23", "2026-10-30"),
    "BR mai-jun/2018": ("2018-05-17", "2018-06-29"),
    "COVID fev-mar/2020": ("2020-02-19", "2020-03-23"),
    "Juros 2022 (jan-out)": ("2021-12-30", "2022-10-14"),
}
CHOQUE_VOL = 1.5
CHOQUE_RHO = 0.5
TOLERANCIA_DIAS = 7   # fechamento mais antigo que isso em relação à data pedida conta como sem dado
PESO_MIN = 1e-9       # classe com peso abaixo disto não entra na checagem de dado faltante


def _fechamento(serie: pd.Series, data: pd.Timestamp, tolerancia_dias: int) -> float:
    """Último fechamento até ``data``; NaN se não houver ou se for mais antigo que a tolerância."""
    s = serie.dropna().loc[:data]
    if s.empty or (data - s.index[-1]).days > tolerancia_dias:
        return np.nan
    return float(s.iloc[-1])


def retorno_janela(precos: pd.DataFrame, base, fim, tolerancia_dias: int = TOLERANCIA_DIAS) -> pd.Series:
    """Retorno de cada coluna de ``precos`` entre os fechamentos de ``base`` e ``fim``."""
    base, fim = pd.Timestamp(base), pd.Timestamp(fim)
    out = {}
    for c in precos.columns:
        p0 = _fechamento(precos[c], base, tolerancia_dias)
        p1 = _fechamento(precos[c], fim, tolerancia_dias)
        out[c] = p1 / p0 - 1.0 if np.isfinite(p0) and np.isfinite(p1) and p0 > 0 else np.nan
    return pd.Series(out, dtype=float)


def retornos_janelas(precos: pd.DataFrame, janelas: dict[str, tuple[str, str]] | None = None) -> pd.DataFrame:
    """Uma linha por janela, uma coluna por série de ``precos`` (níveis diários, índice de datas)."""
    janelas = JANELAS_ESTRESSE if janelas is None else janelas
    return pd.DataFrame({nome: retorno_janela(precos, a, b) for nome, (a, b) in janelas.items()}).T


def estresse_carteira(pesos: pd.Series, retornos: pd.DataFrame) -> pd.Series:
    """Retorno da carteira em cada janela (linha de ``retornos``).

    ``pesos`` indexado pelo mesmo nome das colunas de ``retornos`` (fração; pode somar
    menos de 1). Janela em que uma classe com peso não tem retorno fica NaN: omitir a classe
    subestimaria a perda sem avisar.
    """
    usadas = pesos.index[pesos.abs() > PESO_MIN]
    r = retornos.reindex(columns=usadas)
    port = r.mul(pesos[usadas], axis=1).sum(axis=1, min_count=len(usadas))
    return port.where(r.notna().all(axis=1))


def drawdown_historico(pesos: pd.Series, retornos_mensais: pd.DataFrame) -> dict:
    """Pior mês e máximo drawdown da carteira de pesos fixos no histórico mensal.

    Só entram as classes com peso; a amostra começa no primeiro mês em que todas têm retorno
    (``desde``). Pesos rebalanceados todo mês. Devolve NaN se não houver mês em comum.
    """
    usadas = pesos.index[pesos.abs() > PESO_MIN]
    r = retornos_mensais.reindex(columns=usadas).dropna(how="any")
    if r.empty:
        return {"pior_mes": np.nan, "max_dd": np.nan, "desde": None, "meses": 0}
    carteira = r @ pesos[usadas]
    curva = (1.0 + carteira).cumprod()
    dd = curva / curva.cummax() - 1.0
    return {
        "pior_mes": float(carteira.min()),
        "max_dd": float(dd.min()),
        "desde": r.index[0],
        "meses": int(len(r)),
    }


def choque_vol_correlacao(pesos: np.ndarray, cov: np.ndarray, fator_vol: float = CHOQUE_VOL,
                          deslocamento_rho: float = CHOQUE_RHO) -> tuple[float, float]:
    """(σ base, σ sob choque) da carteira, em fração a.a.

    Vols × ``fator_vol`` e correlações ρ → ρ + ``deslocamento_rho``·(1 − ρ).
    """
    w = np.asarray(pesos, dtype=float)
    cov = np.asarray(cov, dtype=float)
    sd = np.sqrt(np.diag(cov))
    corr = cov / np.outer(sd, sd)
    corr_s = corr + deslocamento_rho * (1.0 - corr)
    np.fill_diagonal(corr_s, 1.0)
    cov_s = np.outer(sd * fator_vol, sd * fator_vol) * corr_s
    return float(np.sqrt(w @ cov @ w)), float(np.sqrt(w @ cov_s @ w))
