"""
Estimação de vols e correlações das classes a partir do histórico dos proxies.

Gera sugestões para uma calibração nova (Default Vol e a matriz Cross-Classe),
no formato das tabelas do Fibery. A calibração aprovada continua sendo a que
está no Fibery: o que sai daqui é congelado lá, para que o espectro de uma
calibração seja reproduzível e não mude a cada carga do banco.

Retornos semanais de log-preço (sexta-feira), anualizados por √52. Dois métodos:

- ``"common"``: janela comum a todas as classes (linhas com qualquer NaN saem).
  Simples, mas a classe de histórico mais curto encurta a janela de todas.
- ``"pairwise"``: vol de cada classe no seu próprio histórico e correlação de
  cada par no período em comum do par. A matriz par a par pode deixar de ser
  positiva semidefinida; ela é corrigida para a correlação PSD mais próxima
  (Higham, 2002). Usar quando as proxies começam em datas muito diferentes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

# Convenção da Cross-Classe: Classe A no bucket de menor índice.
BUCKET_ORDER = ("Renda Fixa", "Alternativos", "Renda Variável")
MIN_WEEKS = 52


@dataclass(frozen=True)
class ClassSpec:
    """Classe a estimar: nome no Fibery, bucket (Classificação Layer 1) e proxy (code em `indicadores`)."""
    name: str
    bucket: str
    proxy: str


@dataclass
class ClassStats:
    vols: pd.Series            # vol anual (fração), indexada pelo nome da classe
    corr: pd.DataFrame         # correlação n × n, na ordem das classes
    start: pd.Timestamp        # primeira semana usada (qualquer classe)
    end: pd.Timestamp          # última semana usada
    n_weeks: int               # semanas da classe com menos histórico
    coverage: pd.DataFrame = field(default_factory=pd.DataFrame)  # inicio, fim, semanas por classe
    psd_adjustment: float = 0.0  # maior |Δρ| aplicado pela correção PSD (0 se não precisou)


def weekly_log_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Preços diários (index = data, colunas = séries) → retornos semanais de log (sexta)."""
    px = prices.sort_index()
    px.index = pd.to_datetime(px.index)
    return np.log(px.resample("W-FRI").last()).diff()


def nearest_correlation(corr: np.ndarray, tol: float = 1e-10, max_iter: int = 500) -> np.ndarray:
    """
    Correlação positiva semidefinida mais próxima (norma de Frobenius), pelo
    método de projeções alternadas de Higham (2002) com correção de Dykstra.
    Devolve a própria matriz (simetrizada) se ela já for PSD.
    """
    a = (np.asarray(corr, dtype=float) + np.asarray(corr, dtype=float).T) / 2
    if np.linalg.eigvalsh(a).min() >= -tol:
        return a
    y = a.copy()
    ds = np.zeros_like(a)
    for _ in range(max_iter):
        r = y - ds
        w, v = np.linalg.eigh(r)
        x = (v * np.clip(w, 0.0, None)) @ v.T
        ds = x - r
        y_new = x.copy()
        np.fill_diagonal(y_new, 1.0)
        if np.linalg.norm(y_new - y, "fro") < tol:
            y = y_new
            break
        y = y_new
    return (y + y.T) / 2


def estimate_class_stats(
    classes: list[ClassSpec],
    prices: pd.DataFrame,
    start: Optional[str] = None,
    end: Optional[str] = None,
    method: str = "common",
    class_start: Optional[dict[str, str]] = None,
    min_weeks: int = MIN_WEEKS,
) -> ClassStats:
    """
    Vols e correlações das `classes` a partir de `prices` (colunas = proxies).

    `class_start`: início próprio por classe (nome → 'YYYY-MM-DD'), aplicado
    depois de `start`; útil para cortar trechos que não representam a classe
    (ex.: os primeiros anos do bitcoin).

    Levanta ValueError se faltar a série de algum proxy, se uma classe (ou, no
    método pareado, um par) tiver menos de `min_weeks` semanas.
    """
    _check_classes(classes)
    if method not in ("common", "pairwise"):
        raise ValueError(f"method deve ser 'common' ou 'pairwise', recebido {method!r}")
    proxies = [c.proxy for c in classes]
    missing = [p for p in proxies if p not in prices.columns]
    if missing:
        raise ValueError(f"Sem histórico para os proxies: {missing}")

    names = [c.name for c in classes]
    rets = weekly_log_returns(prices[list(dict.fromkeys(proxies))]).loc[start:end]
    rets = pd.DataFrame({c.name: rets[c.proxy] for c in classes}, index=rets.index)
    for name, dt in (class_start or {}).items():
        if name not in rets.columns:
            raise ValueError(f"class_start para classe inexistente: {name!r}")
        rets.loc[rets.index < pd.Timestamp(dt), name] = np.nan

    if method == "common":
        rets = rets.dropna()
        if len(rets) < min_weeks:
            raise ValueError(
                f"Janela comum com {len(rets)} semanas; mínimo de {min_weeks}. "
                "Algum proxy tem histórico curto demais; considere method='pairwise'."
            )
        corr_raw = rets.corr()
    else:
        counts = rets.count()
        short = counts[counts < min_weeks]
        if not short.empty:
            raise ValueError(f"Classes com menos de {min_weeks} semanas: {short.to_dict()}")
        corr_raw = rets.corr(min_periods=min_weeks)
        if corr_raw.isna().any().any():
            pares = [(a, b) for a in names for b in names
                     if a < b and pd.isna(corr_raw.loc[a, b])]
            raise ValueError(f"Pares sem {min_weeks} semanas em comum: {pares}")

    corr = nearest_correlation(corr_raw.values)
    coverage = pd.DataFrame({
        "inicio": rets.apply(lambda s: s.first_valid_index()),
        "fim": rets.apply(lambda s: s.last_valid_index()),
        "semanas": rets.count(),
    })
    return ClassStats(
        vols=rets.std() * np.sqrt(52),
        corr=pd.DataFrame(corr, index=names, columns=names),
        start=coverage["inicio"].min(),
        end=coverage["fim"].max(),
        n_weeks=int(coverage["semanas"].min()),
        coverage=coverage,
        psd_adjustment=float(np.abs(corr - corr_raw.values).max()),
    )


def correlation_pairs(classes: list[ClassSpec], corr: pd.DataFrame) -> pd.DataFrame:
    """
    Todos os pares i<j no formato da Cross-Classe (Classe A, Classe B, Rho),
    com Classe A no bucket de menor índice (RF < Alt < RV) e, dentro do mesmo
    bucket, na ordem de `classes`.
    """
    _check_classes(classes)
    ordered = sorted(
        range(len(classes)),
        key=lambda i: (BUCKET_ORDER.index(classes[i].bucket), i),
    )
    rows = []
    for a_pos, i in enumerate(ordered):
        for j in ordered[a_pos + 1:]:
            a, b = classes[i].name, classes[j].name
            rows.append({"Classe A": a, "Classe B": b, "Rho": round(float(corr.loc[a, b]), 4)})
    return pd.DataFrame(rows, columns=["Classe A", "Classe B", "Rho"])


def _check_classes(classes: list[ClassSpec]) -> None:
    names = [c.name for c in classes]
    if len(set(names)) != len(names):
        raise ValueError("Nomes de classe repetidos")
    bad = [c.name for c in classes if c.bucket not in BUCKET_ORDER]
    if bad:
        raise ValueError(f"Bucket fora de {BUCKET_ORDER} nas classes: {bad}")
