"""Backtest walk-forward do espectro RBS.

A cada data de rebalanceamento re-estima vols e correlações só com dados até a data,
roda ``build_spectrum`` e segura os pesos até o próximo rebalanceamento (buy-and-hold dentro
do período). Compara o σ realizado de cada perfil com o σ-alvo.

Funciona em qualquer frequência de dados (``periodos_por_ano``: 12 mensal, 52 semanal,
252 diária): o λ do EWMA é ajustado para manter a mesma meia-vida da estimativa diária
(``EWMA_DECAY`` por dia útil). Com dado mensal, a vol e a correlação são aproximações das
estimativas da página Capital Market Assumptions (que usam dado diário e semanal).

Quem lê a política (σ min/máx, RC targets, tetos) é o ``base`` (uma ``SpectrumConfig``); o
backtest só troca as vols e a matriz de correlação. A política é a de hoje em todas as datas
(não há versão histórica das calibrações), então o resultado testa o método, não a política
que valia na época.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from .config import SpectrumConfig
from .core import build_spectrum
from .estimation import CMA_LOOKBACK_YEARS, EWMA_DECAY, nearest_correlation

DIAS_UTEIS_ANO = 252
MIN_OBS_ANOS = 3          # histórico mínimo de uma classe para entrar na estimativa
PESO_MAX_EXCLUIDA = 1e-6  # o motor exige max_weight > 0; o piso de peso mínimo zera a classe


def ewma_vol_periodica(ret: pd.DataFrame, periodos_por_ano: int) -> pd.Series:
    """Vol EWMA anualizada de retornos simples, com a meia-vida do ``EWMA_DECAY`` diário."""
    lam = EWMA_DECAY ** (DIAS_UTEIS_ANO / periodos_por_ano)
    out = {}
    for c in ret.columns:
        r = ret[c].dropna()
        if r.empty:
            continue
        var = (r ** 2).ewm(alpha=1 - lam, adjust=False).mean().iloc[-1]
        out[c] = float(np.sqrt(var * periodos_por_ano))
    return pd.Series(out, dtype=float)


def estimar(precos: pd.DataFrame, periodos_por_ano: int, anos: int = CMA_LOOKBACK_YEARS,
            so_ewma: tuple[str, ...] = ()) -> tuple[pd.Series, pd.DataFrame]:
    """Vol (50% EWMA + 50% desvio da janela; só EWMA para ``so_ewma``) e correlação.

    ``precos`` já cortados na data de estimação. Colunas com menos de ``MIN_OBS_ANOS`` de
    retornos ficam de fora. Correlação par a par, sem correção PSD (como no Hub).
    """
    ret = precos.pct_change(fill_method=None)
    ret = ret.iloc[-anos * periodos_por_ano:]
    ok = [c for c in ret.columns if ret[c].count() >= MIN_OBS_ANOS * periodos_por_ano]
    ret = ret[ok]
    ew = ewma_vol_periodica(ret, periodos_por_ano)
    jan = ret.std() * np.sqrt(periodos_por_ano)
    vol = 0.5 * ew + 0.5 * jan[ew.index]
    for c in so_ewma:
        if c in vol.index:
            vol[c] = ew[c]
    return vol, ret.corr(min_periods=MIN_OBS_ANOS * periodos_por_ano)


def config_na_data(base: SpectrumConfig, proxies: dict[str, str | None], vol: pd.Series,
                   corr: pd.DataFrame) -> tuple[SpectrumConfig, list[str]]:
    """``base`` com vols e correlações re-estimadas; devolve também as classes excluídas.

    Classe sem proxy (valores manuais) mantém vol e correlações do ``base``. Classe com proxy
    sem histórico suficiente na data é excluída (``max_weight`` = ``PESO_MAX_EXCLUIDA``). Pares com um lado manual
    ou sem dado ficam com a correlação do ``base``.
    """
    n = len(base.assets)
    full = np.array(base.full_corr, dtype=float)
    assets, excluidas = [], []
    for a in base.assets:
        p = proxies.get(a.name)
        if p is None:
            assets.append(a)
        elif p in vol.index:
            assets.append(replace(a, default_vol=float(vol[p])))
        else:
            assets.append(replace(a, max_weight=PESO_MAX_EXCLUIDA))
            excluidas.append(a.name)
    for i in range(n):
        for j in range(i + 1, n):
            pi, pj = proxies.get(base.assets[i].name), proxies.get(base.assets[j].name)
            if pi in corr.index and pj in corr.columns:
                r = corr.loc[pi, pj]
                if np.isfinite(r):
                    full[i, j] = full[j, i] = r
    np.fill_diagonal(full, 1.0)
    return replace(base, assets=assets, full_corr=nearest_correlation(full)), excluidas


@dataclass
class ResultadoBacktest:
    datas: list[pd.Timestamp]                       # datas de rebalanceamento usadas
    pesos: dict[pd.Timestamp, dict[int, np.ndarray]]
    alvo: dict[pd.Timestamp, dict[int, float]]      # σ-alvo (%) por perfil
    retornos: pd.DataFrame                          # retorno por período (colunas = perfis)
    giro: pd.DataFrame                              # giro a cada rebalanceamento, por perfil
    falhas: list[tuple[pd.Timestamp, str]]
    excluidas: dict[pd.Timestamp, list[str]]


def datas_rebalanceamento(indice: pd.DatetimeIndex, inicio, meses: int = 3) -> list[pd.Timestamp]:
    """Último ponto do índice de cada trimestre (``meses``=3), a partir de ``inicio``."""
    s = pd.Series(indice, index=indice)
    s = s[s >= pd.Timestamp(inicio)]
    grupo = s.groupby([s.index.year, (s.index.month - 1) // meses])
    return list(grupo.max())


def rodar(base: SpectrumConfig, proxies: dict[str, str | None], precos: pd.DataFrame,
          retornos_classe: pd.DataFrame, periodos_por_ano: int, inicio, meses: int = 3,
          so_ewma: tuple[str, ...] = ()) -> ResultadoBacktest:
    """Walk-forward.

    ``precos``: níveis dos proxies usados na estimação (colunas = códigos de ``proxies``).
    ``retornos_classe``: retorno de cada classe por período (colunas = nomes das classes),
    já na moeda do livro e com classes sem proxy resolvidas; é o que a carteira realiza.
    """
    datas = datas_rebalanceamento(precos.index, inicio, meses)
    pesos, alvo, falhas, excl = {}, {}, [], {}
    for t in datas:
        vol, corr = estimar(precos.loc[:t], periodos_por_ano, so_ewma=so_ewma)
        cfg, ex = config_na_data(base, proxies, vol, corr)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                res = build_spectrum(cfg)
        except Exception as exc:  # noqa: BLE001  registra e segue com os pesos anteriores
            falhas.append((t, f"{type(exc).__name__}: {exc}"))
            continue
        perfis = sorted(res["weights"])
        pesos[t] = {p: np.asarray(res["weights"][p]) for p in perfis}
        alvo[t] = {p: float(v) * 100 for p, v in zip(perfis, res["vol_targets"])}
        excl[t] = ex
        sem = [p for p, c in res["converged_per_profile"].items() if not c]
        if sem:
            falhas.append((t, f"perfis sem convergência: {sem}"))
    return realizar(pesos, alvo, retornos_classe, falhas, excl)


def realizar(pesos: dict, alvo: dict, retornos_classe: pd.DataFrame,
             falhas: list | None = None, excl: dict | None = None) -> ResultadoBacktest:
    """Aplica os pesos de cada rebalanceamento aos retornos por classe (deriva sem rebalancear).

    Útil para trocar só os retornos (ex.: outro substituto para classes sem série) sem
    refazer a otimização.
    """
    datas = sorted(pesos)
    idx = retornos_classe.index
    n = len(next(iter(pesos[datas[0]].values())))
    nomes = list(retornos_classe.columns)
    if len(nomes) != n:
        raise ValueError("retornos_classe deve ter uma coluna por classe, na ordem da config")
    linhas, giros = {}, {}
    final = None
    for k, t in enumerate(datas):
        fim = datas[k + 1] if k + 1 < len(datas) else idx[-1]
        if fim <= t:
            break
        w0 = pesos[t]
        giros[t] = {}
        if final is not None:
            for p in w0:
                giros[t][p] = float(np.abs(w0[p] - final[p]).sum() / 2)
        sel = (idx > t) & (idx <= fim)
        acum = {p: w.copy() for p, w in w0.items()}
        for d, r in zip(idx[sel], retornos_classe.loc[sel].to_numpy()):
            for p, w in acum.items():
                linhas.setdefault(d, {})[p] = float(w @ r)
                w *= 1 + r                      # deriva sem rebalancear
                s = w.sum()
                if s > 0:
                    w /= s
        final = acum
    ret = pd.DataFrame.from_dict(linhas, orient="index").sort_index()
    ret.columns = [f"P{c}" for c in ret.columns]
    giro = pd.DataFrame.from_dict(giros, orient="index").sort_index()
    giro.columns = [f"P{c}" for c in giro.columns]
    return ResultadoBacktest(datas, pesos, alvo, ret, giro, falhas or [], excl or {})


def resumo(res: ResultadoBacktest, periodos_por_ano: int, caixa: pd.Series | None = None) -> pd.DataFrame:
    """Métricas por perfil: retorno e vol anualizados, σ realizado/alvo, pior período, drawdown."""
    r = res.retornos
    alvo_medio = pd.Series({f"P{p}": np.mean([a[p] for a in res.alvo.values() if p in a])
                            for p in next(iter(res.alvo.values()))})
    anos = len(r) / periodos_por_ano
    cresc = (1 + r).prod()
    out = pd.DataFrame({
        "retorno_aa_%": (cresc ** (1 / anos) - 1) * 100,
        "vol_real_%": r.std() * np.sqrt(periodos_por_ano) * 100,
        "alvo_%": alvo_medio,
    })
    out["real/alvo"] = out["vol_real_%"] / out["alvo_%"]
    if caixa is not None:
        ex = r.sub(caixa.reindex(r.index).fillna(0.0), axis=0)
        out["exc_caixa_aa_%"] = ex.mean() * periodos_por_ano * 100
        out["índice_sharpe"] = ex.mean() / ex.std() * np.sqrt(periodos_por_ano)
    out["pior_período_%"] = r.min() * 100
    curva = (1 + r).cumprod()
    out["max_dd_%"] = ((curva / curva.cummax()) - 1).min() * 100
    if not res.giro.empty:
        out["giro_médio_%"] = res.giro.mean() * 100
    return out
