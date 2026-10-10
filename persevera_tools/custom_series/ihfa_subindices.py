"""
Sub-índices do IHFA por estratégia ANBIMA.

Reconstrói a carteira teórica do IHFA filtrando os fundos cuja classificação
ANBIMA vigente em cada rebalanceamento corresponde à estratégia, renormaliza
pesos e calcula a performance como carteira buy-and-hold entre
rebalanceamentos (quantidade teórica fixa, pesos derivam com a cota), usando
as cotas de ``fundos_cvm``.

Sub-índices disponíveis em ``SUBINDICES``; cada um grava um código
``persevera_anbima_ihfa_*`` (field ``close``) em ``indicadores``.

Uso::

    python -m persevera_tools.custom_series.ihfa_subindices --subindice long_short [--no-upload]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from ..db.operations import to_sql
from ..utils.logging import get_logger
from ..data.funds import get_funds_data, clean_fund_returns
from ..data.providers.anbima_feed import (
    AnbimaFeedNotFoundError,
    AnbimaFeedProvider,
    AnbimaFundosProvider,
)

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Subindice:
    """Estratégia filtrada pelo ``tipo_anbima`` (substring, sem caixa)."""

    padrao_tipo_anbima: str
    code: str


SUBINDICES: dict[str, Subindice] = {
    "long_short": Subindice("long and short", "persevera_anbima_ihfa_long_short"),
}

INDEX_CODE = SUBINDICES["long_short"].code
INDEX_FIELD = "close"
INDEX_BASE = 100.0

QUARTER_MONTHS = (1, 4, 7, 10)

# v1: histórico até jul/2024; v2 (RCVM 175) a partir de out/2024
V1_ENDPOINT = "anbima_feed_indices_mais_carteira_teorica_ihfa"
V1_START = (2014, 1)
V1_END = (2024, 7)

V2_ENDPOINT = "anbima_feed_indices_mais_carteira_teorica_ihfa_v2"
V2_START = (2024, 10)

# Cobertura mínima (fração do peso do sub-índice com cota) aceita em cada vigência.
MIN_COVERAGE = 0.80
# Os últimos dias só entram no índice se ao menos esta fração do peso tiver
# cota publicada no dia (e não repetida da anterior); a CVM publica com atraso
# e um dia com poucas cotas sairia com retorno ~0, revisado na rodada seguinte.
MIN_FRESH_COVERAGE = 0.50


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _digits(cnpj: object) -> str:
    return re.sub(r"\D", "", str(cnpj))


def format_cnpj(cnpj: object) -> str:
    """Normaliza CNPJ para o formato XX.XXX.XXX/XXXX-XX usado em ``fundos_cvm``."""
    digits = _digits(cnpj)
    if len(digits) == 14:
        return (
            f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/"
            f"{digits[8:12]}-{digits[12:]}"
        )
    return str(cnpj)


def _explode_nested(df: pd.DataFrame) -> pd.DataFrame:
    """Explode ``fundos`` (v1) ou ``componentes`` (v2) em linhas flat."""
    nested_col = next((c for c in ("fundos", "componentes") if c in df.columns), None)
    if nested_col is None:
        return df
    ctx_cols = [c for c in df.columns if c != nested_col]
    rows: list[dict] = []
    for _, row in df.iterrows():
        ctx = row[ctx_cols].to_dict()
        for item in (row.get(nested_col) or []):
            if isinstance(item, dict):
                rows.append({**ctx, **item})
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _fetch_quarters(
    feed: AnbimaFeedProvider,
    endpoint: str,
    start: tuple[int, int],
    end: tuple[int, int],
) -> pd.DataFrame:
    """
    Baixa todas as carteiras trimestrais no intervalo ``start``–``end``.

    Trimestres sem carteira publicada (404) são ignorados; qualquer outro erro
    é propagado para não gravar um índice com trimestres faltando.
    """
    today = pd.Timestamp.today()
    sy, sm = start
    ey, em = end
    frames: list[pd.DataFrame] = []

    for year in range(sy, ey + 1):
        for month in QUARTER_MONTHS:
            if (year, month) < (sy, sm) or (year, month) > (ey, em):
                continue
            if (year, month) > (today.year, today.month):
                break
            try:
                df = feed.get_data(endpoint, mes=month, ano=year)
            except AnbimaFeedNotFoundError:
                logger.debug("Sem carteira %s %04d-%02d", endpoint, year, month)
                continue
            if not df.empty:
                df["_mes"], df["_ano"] = month, year
                frames.append(df)
                logger.info("%s %04d-%02d: %d periodos", endpoint, year, month, len(df))

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _add_effective_end(df_comp: pd.DataFrame) -> pd.DataFrame:
    """
    Adiciona ``vig_fim``: cada carteira vale até a véspera do próximo
    rebalanceamento (a última, até ``data_fim``).

    A API publica vigências que se sobrepõem (rebalanceamentos intra-trimestre
    em 2018) e vigências com ``data_fim`` antes do próximo início (2017/2018);
    encadear pelos inícios elimina dupla contagem e buracos.
    """
    starts = np.sort(df_comp["data_inicio"].unique())
    nxt = dict(zip(starts[:-1], starts[1:]))
    last_end = df_comp.loc[df_comp["data_inicio"] == starts[-1], "data_fim"].max()
    df = df_comp.copy()
    df["vig_fim"] = df["data_inicio"].map(
        lambda s: nxt[s] - pd.Timedelta(days=1) if s in nxt else last_end
    )
    return df


# --------------------------------------------------------------------------- #
# Etapas do pipeline
# --------------------------------------------------------------------------- #

def fetch_composition(
    feed: Optional[AnbimaFeedProvider] = None,
) -> pd.DataFrame:
    """
    Retorna a composição histórica consolidada do IHFA (v1 + v2).

    Colunas principais: ``identificador`` (CNPJ só dígitos), ``nome``,
    ``data_inicio``, ``data_fim``, ``vig_fim``, ``peso``, ``_versao``.
    """
    feed = feed or AnbimaFeedProvider()
    today = pd.Timestamp.today()

    logger.info("Baixando carteiras IHFA v1...")
    raw_v1 = _fetch_quarters(feed, V1_ENDPOINT, V1_START, V1_END)

    logger.info("Baixando carteiras IHFA v2...")
    raw_v2 = _fetch_quarters(feed, V2_ENDPOINT, V2_START, (today.year, 12))

    parts: list[pd.DataFrame] = []

    if not raw_v1.empty:
        df = _explode_nested(raw_v1).rename(columns={
            "cnpj_fundo": "cnpj",
            "nome_fundo": "nome",
            "valor_quota": "valor_cota",
            "valor_patrimonio_liquido": "pl",
        })
        df["_versao"] = "v1"
        parts.append(df)

    if not raw_v2.empty:
        # v2: componentes trazem cnpj_classe (não codigo_subclasse)
        df = _explode_nested(raw_v2).rename(columns={
            "cnpj_classe": "cnpj",
            "razao_social_classe": "nome",
            "valor_patrimonio_liquido": "pl",
        })
        df["_versao"] = "v2"
        parts.append(df)

    if not parts:
        raise RuntimeError("Nenhuma carteira IHFA retornada pela API ANBIMA.")

    df_comp = pd.concat(parts, ignore_index=True)
    df_comp["identificador"] = df_comp["cnpj"].map(
        lambda c: _digits(c) if pd.notna(c) else None
    )
    for col in ("data_inicio", "data_fim"):
        df_comp[col] = pd.to_datetime(df_comp[col], errors="coerce")
    df_comp["peso"] = pd.to_numeric(df_comp["peso"], errors="coerce")

    antes = len(df_comp)
    df_comp = (
        df_comp
        .dropna(subset=["identificador", "data_inicio", "data_fim"])
        .drop_duplicates(subset=["identificador", "data_inicio", "data_fim"])
    )
    df_comp = _add_effective_end(df_comp)
    logger.info(
        "Composicao: %d registros (%d removidos) | %d fundos | %d carteiras | cobertura %s -> %s",
        len(df_comp),
        antes - len(df_comp),
        df_comp["identificador"].nunique(),
        df_comp["data_inicio"].nunique(),
        df_comp["data_inicio"].min().date(),
        df_comp["vig_fim"].max().date(),
    )
    return df_comp


def classify_and_filter(
    df_comp: pd.DataFrame,
    padrao_tipo_anbima: str,
    fundos: Optional[AnbimaFundosProvider] = None,
    df_cls: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Classifica cada fundo pela classificação ANBIMA vigente no início de cada
    carteira e mantém os que contêm ``padrao_tipo_anbima`` no ``tipo_anbima``.

    Antes do primeiro registro do histórico de um fundo usa-se o mais antigo
    disponível. Adiciona ``tipo_anbima`` e ``peso_sub`` (renormalizado para 100%
    em cada carteira).

    Args:
        df_cls: Histórico de classificação já obtido (saída de
            :meth:`AnbimaFundosProvider.get_classificacao_historica`); se
            ``None``, é buscado na ANBIMA.
    """
    if df_cls is None:
        fundos = fundos or AnbimaFundosProvider()
        df_cls = fundos.get_classificacao_historica(list(df_comp["identificador"].unique()))

    hist = df_cls.sort_values("data_vigencia")
    left = df_comp.reset_index(drop=True).sort_values("data_inicio")
    asof = pd.merge_asof(
        left, hist, left_on="data_inicio", right_on="data_vigencia",
        by="identificador", direction="backward",
    )
    first = hist.groupby("identificador")["tipo_anbima"].first()
    asof["tipo_anbima"] = asof["tipo_anbima"].fillna(asof["identificador"].map(first))
    df = asof.drop(columns="data_vigencia")

    sem = df["tipo_anbima"].isna()
    peso_sem = df[sem].groupby("data_inicio")["peso"].sum()
    if sem.any():
        logger.warning(
            "Sem classificacao: %d fundos | peso no IHFA por carteira: medio %.1f%%, max %.1f%% (%s)",
            df.loc[sem, "identificador"].nunique(),
            peso_sem.reindex(df["data_inicio"].unique(), fill_value=0).mean(),
            peso_sem.max(),
            peso_sem.idxmax().date(),
        )

    mask = df["tipo_anbima"].fillna("").str.lower().str.contains(padrao_tipo_anbima.lower())
    df_sub = df[mask].copy()
    if df_sub.empty:
        raise RuntimeError(
            f"Nenhum fundo com tipo ANBIMA '{padrao_tipo_anbima}' na composição IHFA."
        )

    df_sub["peso_sub"] = df_sub.groupby("data_inicio")["peso"].transform(
        lambda x: x / x.sum() * 100
    )

    logger.info(
        "'%s': %d fundos | %d carteiras | %s -> %s | peso medio no IHFA: %.1f%%",
        padrao_tipo_anbima,
        df_sub["identificador"].nunique(),
        df_sub["data_inicio"].nunique(),
        df_sub["data_inicio"].min().date(),
        df_sub["vig_fim"].max().date(),
        df_sub.groupby("data_inicio")["peso"].sum().mean(),
    )
    return df_sub


def build_index(
    df_sub: pd.DataFrame,
    nav: Optional[pd.DataFrame] = None,
    min_coverage: float = MIN_COVERAGE,
    code: str = INDEX_CODE,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """
    Constrói o sub-índice a partir dos pesos filtrados e das cotas.

    Em cada carteira o peso inicial ``peso_sub`` deriva com o retorno acumulado
    de cada fundo (quantidade teórica fixa). Fundos sem retorno no dia saem do
    cálculo daquele dia e os demais são renormalizados.

    Args:
        df_sub: Saída de :func:`classify_and_filter`.
        nav: Cotas ``date x CNPJ formatado``; se ``None``, lê de ``fundos_cvm``.
        min_coverage: Fração mínima do peso com cota, em média, em cada
            carteira. Abaixo disso o pipeline falha.
        code: Nome da série retornada.

    Returns:
        ``(indice, retorno_diario, cobertura)`` — índice base 100, retornos e
        cobertura por carteira.
    """
    pesos = (
        df_sub.assign(cnpj=df_sub["identificador"].map(format_cnpj))
        .groupby(["data_inicio", "vig_fim", "cnpj"], as_index=False)["peso_sub"].sum()
    )

    if nav is None:
        dt_inicio = (pesos["data_inicio"].min() - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        cnpjs = pesos["cnpj"].unique().tolist()
        logger.info("Buscando cotas de %d fundos a partir de %s...", len(cnpjs), dt_inicio)
        nav = get_funds_data(cnpjs=cnpjs, start_date=dt_inicio, fields=["fund_nav"])
    if nav.empty:
        raise RuntimeError("Nenhuma cota encontrada em fundos_cvm para os CNPJs do sub-índice.")

    ret_all = clean_fund_returns(nav)
    observed = nav.sort_index().notna()

    rets: list[pd.Series] = []
    fresh: list[pd.Series] = []
    cov_rows: list[dict] = []
    for (inicio, fim), grp in pesos.groupby(["data_inicio", "vig_fim"]):
        w0 = grp.set_index("cnpj")["peso_sub"]
        days = ret_all.index[(ret_all.index >= inicio) & (ret_all.index <= fim)]
        if days.empty:
            continue
        r = ret_all.reindex(index=days, columns=w0.index)
        # valor relativo de cada posição no fechamento anterior
        growth = (1 + r.fillna(0)).cumprod().shift(1).fillna(1.0)
        w = (growth * w0).where(r.notna(), 0.0)
        w_sum = w.sum(axis=1)
        rets.append(((w * r).sum(axis=1) / w_sum.replace(0, np.nan)).rename("retorno"))
        obs = observed.reindex(index=days, columns=w0.index, fill_value=False)
        fresh.append(obs.mul(w0).sum(axis=1) / w0.sum())

        cov = r.notna().mul(w0).sum(axis=1) / w0.sum()
        cov_rows.append({
            "data_inicio": inicio, "vig_fim": fim, "n_fundos": len(w0),
            "n_com_cota": int(r.notna().any().sum()),
            "cobertura_media": cov.mean(), "cobertura_min": cov.min(),
        })

    if not rets:
        raise RuntimeError("Nenhum dia com cota dentro das vigências do sub-índice.")
    ret = pd.concat(rets).sort_index()
    ret = ret[~ret.index.duplicated(keep="first")]
    cobertura = pd.DataFrame(cov_rows)

    fresh_cov = pd.concat(fresh).sort_index()
    fresh_cov = fresh_cov[~fresh_cov.index.duplicated(keep="first")]
    ok = fresh_cov[fresh_cov >= MIN_FRESH_COVERAGE].index
    if len(ok) and ok.max() < ret.index.max():
        cortados = ret.index[ret.index > ok.max()]
        logger.info(
            "Ultimos %d dia(s) fora do indice (menos de %.0f%% do peso com cota publicada): %s",
            len(cortados), MIN_FRESH_COVERAGE * 100, [d.date() for d in cortados],
        )
        ret = ret.loc[: ok.max()]

    logger.info(
        "Cobertura do peso por carteira: media %.1f%% | pior %.1f%% (%s)",
        cobertura["cobertura_media"].mean() * 100,
        cobertura["cobertura_media"].min() * 100,
        cobertura.loc[cobertura["cobertura_media"].idxmin(), "data_inicio"].date(),
    )
    baixa = cobertura[cobertura["cobertura_media"] < min_coverage]
    if not baixa.empty:
        raise RuntimeError(
            f"Cobertura de cotas abaixo de {min_coverage:.0%} em {len(baixa)} carteira(s):\n"
            + baixa.to_string(index=False)
        )
    if ret.isna().any():
        raise RuntimeError(
            f"{int(ret.isna().sum())} dia(s) sem nenhum fundo com retorno: "
            f"{[d.date() for d in ret[ret.isna()].index[:5]]}"
        )

    prev = nav.index[nav.index < ret.index[0]]
    base_date = prev.max() if len(prev) else ret.index[0] - pd.offsets.BDay(1)
    indice = pd.concat([
        pd.Series([INDEX_BASE], index=[base_date]),
        (1 + ret).cumprod() * INDEX_BASE,
    ]).rename(code)
    indice.index.name = "date"
    return indice, ret, cobertura


def index_to_long(
    indice: pd.Series,
    code: str = INDEX_CODE,
    field: str = INDEX_FIELD,
) -> pd.DataFrame:
    """Converte a série do índice para o formato long de ``indicadores``."""
    out = (
        indice.dropna()
        .rename("value")
        .reset_index()
        .rename(columns={indice.index.name or "index": "date"})
    )
    if out.columns[0] != "date":
        out = out.rename(columns={out.columns[0]: "date"})
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["code"] = code
    out["field"] = field
    out = out.dropna(subset=["date", "value"])
    return out[["date", "code", "field", "value"]]


# --------------------------------------------------------------------------- #
# Entrypoints
# --------------------------------------------------------------------------- #

def run_ihfa_subindex(
    subindice: str = "long_short",
    *,
    upload: bool = True,
    table_name: str = "indicadores",
    primary_keys: Sequence[str] = ("code", "date", "field"),
    batch_size: int = 5000,
    field: str = INDEX_FIELD,
    min_coverage: float = MIN_COVERAGE,
) -> pd.DataFrame:
    """
    Executa o pipeline completo de um sub-índice do IHFA.

    Args:
        subindice: Chave de ``SUBINDICES``.
        upload: Se ``True``, faz upsert em ``table_name``.
        table_name: Tabela destino (padrão ``indicadores``).
        primary_keys: Chaves de conflito do upsert.
        batch_size: Tamanho do batch em ``to_sql``.
        field: Campo persistido (padrão ``close``).
        min_coverage: Cobertura mínima de cotas por carteira (ver
            :func:`build_index`).

    Returns:
        DataFrame long ``[date, code, field, value]`` pronto para ``indicadores``.
    """
    cfg = SUBINDICES[subindice]
    logger.info("=== Pipeline IHFA %s (%s) ===", subindice, cfg.code)

    df_comp = fetch_composition(AnbimaFeedProvider())
    df_sub = classify_and_filter(df_comp, cfg.padrao_tipo_anbima, AnbimaFundosProvider())
    indice, ret, _ = build_index(df_sub, min_coverage=min_coverage, code=cfg.code)

    result = index_to_long(indice, code=cfg.code, field=field)
    result = result.drop_duplicates(subset=list(primary_keys), keep="last")

    if not result.empty:
        logger.info(
            "Indice %s: %s -> %s | ultimo=%.2f | %d linhas",
            cfg.code,
            result["date"].min().date(),
            result["date"].max().date(),
            result["value"].iloc[-1],
            len(result),
        )
        anual = (
            ret.resample("YE")
            .apply(lambda x: (1 + x).prod() - 1)
            .mul(100)
            .round(2)
        )
        logger.info("Retorno anual (%%):\n%s", anual.to_string())
    else:
        logger.warning("Pipeline não produziu linhas")

    if upload and not result.empty:
        logger.info("Upserting %d rows into '%s' (code=%s)", len(result), table_name, cfg.code)
        to_sql(
            result.reset_index(drop=True),
            table_name=table_name,
            primary_keys=list(primary_keys),
            update=True,
            batch_size=batch_size,
        )
    elif upload:
        logger.info("Skip upload - sem linhas")

    return result


def run_anbima_ihfa_ls_pipeline(**kwargs) -> pd.DataFrame:
    """Atalho para ``run_ihfa_subindex("long_short", ...)``."""
    return run_ihfa_subindex("long_short", **kwargs)


def _main(argv: Sequence[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="Calcula sub-índices do IHFA por estratégia ANBIMA.")
    p.add_argument(
        "--subindice",
        choices=sorted(SUBINDICES),
        action="append",
        help="Sub-índice a calcular (repetível). Padrão: todos.",
    )
    p.add_argument("--no-upload", action="store_true", help="Calcula sem gravar em indicadores.")
    p.add_argument(
        "--min-coverage",
        type=float,
        default=MIN_COVERAGE,
        help="Fração mínima do peso com cota em cada carteira (padrão: %(default)s).",
    )
    args = p.parse_args(argv)

    for nome in args.subindice or sorted(SUBINDICES):
        run_ihfa_subindex(nome, upload=not args.no_upload, min_coverage=args.min_coverage)


if __name__ == "__main__":
    _main()
