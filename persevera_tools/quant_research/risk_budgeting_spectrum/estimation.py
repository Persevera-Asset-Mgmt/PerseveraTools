"""
Estimação de vols e correlações das classes a partir do histórico dos proxies.

Gera sugestões para uma calibração nova (Default Vol e a matriz Cross-Classe),
no formato das tabelas do Fibery. A calibração aprovada continua sendo a que
está no Fibery: o que sai daqui é congelado lá via importação, para que o
espectro de uma calibração seja reproduzível e não mude a cada carga do banco.

Retornos semanais de log-preço (sexta-feira), anualizados por √52, numa janela
comum a todas as classes (linhas com qualquer NaN saem), o que garante matriz
de correlação positiva semidefinida.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

# Convenção da Cross-Classe: Classe A no bucket de menor índice.
BUCKET_ORDER = ("Renda Fixa", "Alternativos", "Renda Variável")


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
    start: pd.Timestamp        # primeira semana usada
    end: pd.Timestamp          # última semana usada
    n_weeks: int


def weekly_log_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Preços diários (index = data, colunas = séries) → retornos semanais de log (sexta)."""
    px = prices.sort_index()
    px.index = pd.to_datetime(px.index)
    return np.log(px.resample("W-FRI").last()).diff()


def estimate_class_stats(
    classes: list[ClassSpec],
    prices: pd.DataFrame,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> ClassStats:
    """
    Vols e correlações das `classes` a partir de `prices` (colunas = proxies).

    Levanta ValueError se faltar a série de algum proxy ou se a janela comum
    tiver menos de 52 semanas.
    """
    _check_classes(classes)
    proxies = [c.proxy for c in classes]
    missing = [p for p in proxies if p not in prices.columns]
    if missing:
        raise ValueError(f"Sem histórico para os proxies: {missing}")

    rets = weekly_log_returns(prices[proxies]).loc[start:end].dropna()
    if len(rets) < 52:
        raise ValueError(
            f"Janela comum com {len(rets)} semanas; mínimo de 52. "
            "Algum proxy tem histórico curto demais para o período pedido."
        )

    names = [c.name for c in classes]
    rets.columns = names
    return ClassStats(
        vols=rets.std() * np.sqrt(52),
        corr=rets.corr(),
        start=rets.index[0],
        end=rets.index[-1],
        n_weeks=len(rets),
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
