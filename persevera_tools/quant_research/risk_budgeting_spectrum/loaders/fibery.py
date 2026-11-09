"""
Loader Fibery → SpectrumConfig.

Pull da Calibração mais recente (ou específica por ID/data) das tabelas:
  • Inv-Rsrch-Quant/Calibração de Mercado
  • Inv-Rsrch-Quant/Classes de Ativo (catálogo)
  • Inv-Rsrch-Quant/Parâmetros de Classe (vol/maxw/maxrc)
  • Inv-Rsrch-Quant/RC Target por Bucket (RC P1/P10 + Curvatura RC opcional)
  • Inv-Rsrch-Quant/Correlação Cross-Classe (rho para TODOS os pares de classes)

Estratégia de falha: explícita. Se qualquer pull falhar, a calibração estiver
incompleta, ou os dados estiverem inconsistentes, levanta exception com
mensagem clara — sem fallback silencioso.

Livro (Onshore / Offshore): campo de seleção única em Calibração de Mercado e
em Classes de Ativo. O universo de uma calibração são as classes do mesmo
livro. Campo ausente ou vazio conta como "Onshore", para que o cadastro
anterior ao offshore continue carregando sem mudança.

Campos opcionais em Parâmetros de Classe:
  • "RC Intra-Bucket": divisão do RC do bucket entre suas classes (pesos
    relativos, renormalizados no motor). Bucket sem nenhum valor → uniforme.

Uso:
    from persevera_tools.quant_research.risk_budgeting_spectrum.loaders import load_from_fibery
    config = load_from_fibery()                    # Onshore, mais recente Aprovada
    config = load_from_fibery(status="Rascunho")   # Onshore, mais recente Rascunho
    config = load_from_fibery(livro="Offshore")    # Offshore, mais recente Aprovada
    config = load_calibration_by_date("2026-05-04")
    config = load_calibration_by_name("Calibração de Mercado 2026-05-04")
"""

from __future__ import annotations
from typing import Optional

import numpy as np
import pandas as pd

from persevera_tools.db.fibery import read_fibery

from ..config import (
    SpectrumConfig,
    AssetClass,
    BucketConfig,
    RCTarget,
)


# ── Constantes (nomes de databases e campos no Fibery) ────────────────────────

SPACE_NAME        = "Inv-Rsrch-Quant"
DB_CALIBRATION    = f"{SPACE_NAME}/Calibração de Mercado"
DB_ASSET_CLASS    = f"{SPACE_NAME}/Classes de Ativo"
DB_PARAMETERS     = f"{SPACE_NAME}/Parâmetros de Classe"
DB_RC_TARGET      = f"{SPACE_NAME}/RC Target por Bucket"
DB_CORR_CLASSES   = f"{SPACE_NAME}/Correlação Cross-Classe"

LIVRO_ONSHORE     = "Onshore"
LIVRO_OFFSHORE    = "Offshore"
LIVROS            = (LIVRO_ONSHORE, LIVRO_OFFSHORE)
_LIVRO_COLUMNS    = ["Livro", f"{SPACE_NAME}/Livro", "livro"]
_RC_INTRA_COLUMNS = ["RC Intra-Bucket", f"{SPACE_NAME}/RC Intra-Bucket", "rc_intra"]


class FiberyLoaderError(Exception):
    """Erro específico do loader Fibery (dados ausentes/inconsistentes)."""
    pass


# ── API pública ───────────────────────────────────────────────────────────────

def load_from_fibery(
    status: Optional[str] = "Aprovada",
    livro: str = LIVRO_ONSHORE,
) -> SpectrumConfig:
    """
    Carrega a Calibração mais recente do livro em status especificado.

    Parâmetros
    ----------
    status : str ou None
        Filtro por status do workflow. Default: "Aprovada".
        Se None, pega a mais recente independente de status.
    livro : "Onshore" (default) ou "Offshore".

    Levanta
    -------
    FiberyLoaderError : se nenhuma calibração do livro no status existir, ou se
        a calibração mais recente estiver incompleta.
    """
    livro = _check_livro(livro)
    calib_df = _read_calibrations()
    calib_df = calib_df[calib_df["livro"] == livro]

    if status is not None:
        calib_df = calib_df[calib_df["status"] == status]
    if calib_df.empty:
        filtro = f" em status '{status}'" if status is not None else ""
        raise FiberyLoaderError(
            f"Nenhuma Calibração de Mercado do livro '{livro}'{filtro} encontrada no Fibery"
        )

    # Mais recente por Data Calibração (desempate: created date se houver)
    calib_df = calib_df.sort_values("data_calibracao", ascending=False)
    return _build_config_from_row(calib_df.iloc[0])


def load_calibration_by_name(calibration_name: str) -> SpectrumConfig:
    """Carrega calibração específica pelo nome. O livro vem da própria calibração."""
    calib_df = _read_calibrations()
    match = calib_df[calib_df["name"] == calibration_name]
    if match.empty:
        raise FiberyLoaderError(f"Calibração com nome '{calibration_name}' não encontrada")
    return _build_config_from_row(match.iloc[0])


def load_calibration_by_date(date: str, livro: str = LIVRO_ONSHORE) -> SpectrumConfig:
    """
    Carrega calibração do livro com Data Calibração específica.
    `date` no formato 'YYYY-MM-DD'.
    """
    livro = _check_livro(livro)
    calib_df = _read_calibrations()
    match = calib_df[
        (calib_df["data_calibracao"].astype(str) == date) & (calib_df["livro"] == livro)
    ]
    if match.empty:
        raise FiberyLoaderError(f"Calibração do livro '{livro}' com data '{date}' não encontrada")
    if len(match) > 1:
        raise FiberyLoaderError(
            f"Múltiplas calibrações do livro '{livro}' com data '{date}' — use load_calibration_by_name"
        )
    return _build_config_from_row(match.iloc[0])


def _build_config_from_row(row: pd.Series) -> SpectrumConfig:
    return _build_config_from_calibration(
        calibration_name=row["name"],
        calibration_date=str(row["data_calibracao"]),
        calibration_status=row["status"],
        sigma_min=row["sigma_min"],
        sigma_max=row["sigma_max"],
        n_profiles=row["n_profiles"],
        min_weight_threshold=row["min_weight_threshold"],
        livro=row["livro"],
    )


# ── Construção da SpectrumConfig a partir da calibração ───────────────────────

def _build_config_from_calibration(
    calibration_name: str,
    calibration_date: str,
    calibration_status: str,
    sigma_min: float,
    sigma_max: float,
    n_profiles: int,
    min_weight_threshold: float,
    livro: str = LIVRO_ONSHORE,
) -> SpectrumConfig:
    """
    Pull de todas as tabelas filhas da calibração e montagem da SpectrumConfig.
    """
    # 1. Catálogo de classes do livro da calibração
    classes_df = _read_classes_catalog()
    classes_df = classes_df[classes_df["livro"] == livro].reset_index(drop=True)
    if classes_df.empty:
        raise FiberyLoaderError(
            f"Nenhuma Classe de Ativo do livro '{livro}' no catálogo "
            f"(calibração '{calibration_name}')"
        )

    # 2. Parâmetros desta calibração
    params_df = _read_parameters(calibration_name)
    if params_df.empty:
        raise FiberyLoaderError(
            f"Calibração '{calibration_name}' não tem Parâmetros de Classe associados"
        )

    # 3. RC targets desta calibração
    rc_df = _read_rc_targets(calibration_name)
    if rc_df.empty:
        raise FiberyLoaderError(
            f"Calibração '{calibration_name}' não tem RC Targets associados"
        )

    # 4. Correlações entre classes desta calibração (todos os pares)
    corr_df = _read_corr_classes(calibration_name)
    if corr_df.empty:
        raise FiberyLoaderError(
            f"Calibração '{calibration_name}' não tem Correlações Cross-Classe"
        )

    # ── Montar AssetClass list (mantendo ordem do catálogo)
    # JOIN catálogo + parâmetros via class_id
    merged = classes_df.merge(
        params_df, left_on="name", right_on="class_id", how="inner"
    )
    if len(merged) != len(classes_df):
        missing = set(classes_df["id"]) - set(merged["id"])
        missing_names = classes_df[classes_df["id"].isin(missing)]["name"].tolist()
        raise FiberyLoaderError(
            f"Calibração '{calibration_name}' não tem parâmetros para as classes: "
            f"{missing_names}"
        )

    # Bucketizar: ordem dos buckets = ordem em que aparecem nas classes
    # (fixa pelo Fibery via ordem do catálogo)
    bucket_order: list[str] = []
    for bk in merged["bucket"]:
        if bk not in bucket_order:
            bucket_order.append(bk)

    # Construir AssetClass list AGRUPADAS POR BUCKET (estrutural)
    assets: list[AssetClass] = []
    for bk in bucket_order:
        rows = merged[merged["bucket"] == bk]
        for _, r in rows.iterrows():
            assets.append(AssetClass(
                name=r["name"],
                bucket=r["bucket"],
                proxy=r["proxy"] if pd.notna(r["proxy"]) else "",
                default_vol=float(r["default_vol"]),
                max_weight=float(r["max_weight"]),
                max_rc=float(r["max_rc"]) if pd.notna(r["max_rc"]) else None,
            ))

    # ── Buckets config
    # `intra_max_weight` não está no Fibery hoje; usa None (sem limite).
    # No futuro, adicionar campo no schema se necessário.
    buckets: dict[str, BucketConfig] = {
        bk: BucketConfig(
            name=bk,
            label=bk,                 # idem name por enquanto
            intra_max_weight=None,    # TODO: adicionar campo no Fibery
        )
        for bk in bucket_order
    }

    # ── RC Targets
    # Verifica que cada bucket tem exatamente um RC target
    rc_targets: dict[str, RCTarget] = {}
    rc_curvature: dict[str, float] = {}
    for bk in bucket_order:
        rows = rc_df[rc_df["bucket"] == bk]
        if rows.empty:
            raise FiberyLoaderError(
                f"Bucket '{bk}' não tem RC Target nesta calibração"
            )
        if len(rows) > 1:
            raise FiberyLoaderError(
                f"Bucket '{bk}' tem {len(rows)} RC Targets duplicados"
            )
        r = rows.iloc[0]
        rc_targets[bk] = RCTarget(
            bucket=bk,
            rc_p1=float(r["rc_p1"]),
            rc_p10=float(r["rc_p10"]),
        )
        # Curvatura: ausente/vazia ⇒ linear (gamma=1.0). Valor > 0 obrigatório.
        gamma = r.get("curvatura")
        if gamma is not None and pd.notna(gamma):
            gamma = float(gamma)
            if gamma <= 0:
                raise FiberyLoaderError(
                    f"Bucket '{bk}': Curvatura RC = {gamma} deve ser > 0 "
                    f"(use 1.0 para interpolação linear)"
                )
            rc_curvature[bk] = gamma

    # Se nenhum bucket definiu curvatura, passa None (motor usa linear puro).
    rc_curvature_arg = rc_curvature if rc_curvature else None

    # ── Divisão do RC dentro de cada bucket (opcional)
    intra_rc_weights = _build_intra_rc_weights(merged, bucket_order)

    # ── Matriz de correlação completa (n × n, todos os pares)
    full_corr = _build_full_corr_matrix(assets, corr_df)

    # Hard validation: faixa de vol viável dado o universo
    from ..core import _feasible_vol_range
    try:
        sigma_min_feas, sigma_max_feas = _feasible_vol_range(
            vols=np.array([a.default_vol for a in assets]),
            max_weights=np.array([a.max_weight for a in assets]),
            full_corr=full_corr,
        )
    except ValueError as exc:
        raise FiberyLoaderError(str(exc)) from exc

    if sigma_min < sigma_min_feas - 1e-6:
        raise FiberyLoaderError(
            f"Calibração '{calibration_name}': Sigma Min = {sigma_min*100:.4f}% "
            f"é inviável dado o universo. Vol mínima atingível (min-variance "
            f"long-only) = {sigma_min_feas*100:.4f}%. "
            f"Ajuste Sigma Min no Fibery para >= {sigma_min_feas*100:.4f}%."
        )

    if sigma_max > sigma_max_feas + 1e-6:
        raise FiberyLoaderError(
            f"Calibração '{calibration_name}': Sigma Max = {sigma_max*100:.4f}% "
            f"é inviável dado o universo. Vol máxima atingível = "
            f"{sigma_max_feas*100:.4f}%."
        )

    if sigma_max <= sigma_min:
        raise FiberyLoaderError(
            f"Calibração '{calibration_name}': Sigma Max ({sigma_max*100:.2f}%) "
            f"deve ser estritamente maior que Sigma Min ({sigma_min*100:.2f}%)."
        )

    # ── Montar SpectrumConfig
    config = SpectrumConfig(
        assets=assets,
        buckets=buckets,
        rc_targets=rc_targets,
        full_corr=full_corr,
        sigma_min_pct=sigma_min * 100.0,        # ← converte fração → %
        sigma_max_pct=sigma_max * 100.0,
        n_profiles=int(n_profiles),
        min_weight_threshold=float(min_weight_threshold),
        rc_curvature=rc_curvature_arg,
        intra_rc_weights=intra_rc_weights,
        calibration_name=calibration_name,
        calibration_date=calibration_date,
        calibration_status=calibration_status,
        calibration_livro=livro,
    )

    # Valida antes de retornar — falha cedo se algo estiver inconsistente
    config.validate()
    return config


# ── Helpers de leitura (camada I/O) ───────────────────────────────────────────
#
# Convenção: cada função recebe o nome da calibração (quando aplicável) e retorna
# DataFrame com colunas normalizadas. Levanta FiberyLoaderError em caso de
# campos ausentes ou erros de leitura.

def _read_calibrations() -> pd.DataFrame:
    """
    Lê Calibração de Mercado. Colunas esperadas:
        id, name, data_calibracao, status
    """
    try:
        df = read_fibery(DB_CALIBRATION)
    except Exception as exc:
        raise FiberyLoaderError(
            f"Falha ao ler '{DB_CALIBRATION}' do Fibery: {exc}"
        ) from exc

    norm = _normalize_columns(df, {
        "id":                   ["fibery/id", "id", "Id"],
        "name":                 ["Inv-Rsrch-Quant/Name", "Name", "name"],
        "data_calibracao":      ["Inv-Rsrch-Quant/Data Calibração", "Data Calibração", "data_calibracao"],
        "status":               ["workflow/state", "State", "state", "Status"],
        "sigma_min":            ["Inv-Rsrch-Quant/Sigma Min", "Sigma Min", "sigma_min"],
        "sigma_max":            ["Inv-Rsrch-Quant/Sigma Max", "Sigma Max", "sigma_max"],
        "n_profiles":           ["Inv-Rsrch-Quant/N Profiles", "N Profiles", "n_profiles"],
        "min_weight_threshold": ["Inv-Rsrch-Quant/Min Weight Threshold", "Min Weight Threshold", "min_weight_threshold"],
    }, table_name=DB_CALIBRATION)
    norm["livro"] = _livro_column(df, DB_CALIBRATION)
    return norm


def _read_classes_catalog() -> pd.DataFrame:
    """
    Lê Classes de Ativo. Colunas esperadas:
        id, name, bucket, proxy
    """
    try:
        df = read_fibery(DB_ASSET_CLASS)
    except Exception as exc:
        raise FiberyLoaderError(
            f"Falha ao ler '{DB_ASSET_CLASS}' do Fibery: {exc}"
        ) from exc

    norm = _normalize_columns(df, {
        "id": ["fibery/id", "id"],
        "name": ["Inv-Rsrch-Quant/Name", "Name", "name"],
        "bucket": ["Classificação Layer 1", "bucket", "Bucket"],
        "proxy": ["Proxy", "proxy"],
    }, table_name=DB_ASSET_CLASS)
    norm["livro"] = _livro_column(df, DB_ASSET_CLASS)
    return norm


def _read_parameters(calibration_name: str) -> pd.DataFrame:
    """
    Lê Parâmetros de Classe. Filtra pela calibração especificada.
    Colunas esperadas: classe_id, default_vol, max_weight, max_rc
    """
    try:
        df = read_fibery(DB_PARAMETERS)
    except Exception as exc:
        raise FiberyLoaderError(
            f"Falha ao ler '{DB_PARAMETERS}' do Fibery: {exc}"
        ) from exc

    norm = _normalize_columns(df, {
        "calibration_name": ["Calibração", "calibracao_id", "Calibracao"],
        "class_id": ["Classe", "classe_id"],
        "default_vol": ["Default Vol", "default_vol"],
        "max_weight": ["Max Weight", "max_weight"],
        "max_rc": ["Max RC", "max_rc"]
    }, table_name=DB_PARAMETERS)

    # RC Intra-Bucket — opcional, como a Curvatura RC.
    intra_col = next((c for c in _RC_INTRA_COLUMNS if c in df.columns), None)
    norm["rc_intra"] = df[intra_col].values if intra_col is not None else np.nan

    # Filtra pela calibração
    return norm[norm["calibration_name"] == calibration_name].copy()


def _read_rc_targets(calibration_name: str) -> pd.DataFrame:
    """
    Lê RC Target por Bucket. Filtra pela calibração.

    `curvatura` é OPCIONAL: o campo 'Curvatura RC' pode não existir em
    workspaces ainda não migrados, ou estar vazio em alguns buckets. Quando
    ausente/vazio, o bucket usa gamma=1.0 (interpolação linear). Por isso a
    coluna é tratada fora do mapeamento obrigatório de `_normalize_columns`.
    """
    try:
        df = read_fibery(DB_RC_TARGET)
    except Exception as exc:
        raise FiberyLoaderError(
            f"Falha ao ler '{DB_RC_TARGET}' do Fibery: {exc}"
        ) from exc

    norm = _normalize_columns(df, {
        "calibration_name": ["Calibração", "calibracao_id"],
        "bucket": ["Classificação Layer 1", "bucket"],
        "rc_p1": ["RC P1", "rc_p1"],
        "rc_p10": ["RC P10", "rc_p10"],
    }, table_name=DB_RC_TARGET)

    # Curvatura RC — opcional. Anexa se a coluna existir no DataFrame de origem.
    curv_col = next(
        (c for c in ["Curvatura RC", "Inv-Rsrch-Quant/Curvatura RC", "curvatura"]
         if c in df.columns),
        None,
    )
    norm["curvatura"] = df[curv_col].values if curv_col is not None else np.nan

    return norm[norm["calibration_name"] == calibration_name].copy()


def _read_corr_classes(calibration_name: str) -> pd.DataFrame:
    """Lê Correlação Cross-Classe (todos os pares). Filtra pela calibração."""
    try:
        df = read_fibery(DB_CORR_CLASSES)
    except Exception as exc:
        raise FiberyLoaderError(
            f"Falha ao ler '{DB_CORR_CLASSES}' do Fibery: {exc}"
        ) from exc

    df = _normalize_columns(df, {
        "calibration_name": ["Calibração", "calibracao_id"],
        "classe_a": ["Classe A", "classe_a"],
        "classe_b": ["Classe B", "classe_b"],
        "rho": ["Rho", "rho"],
    }, table_name=DB_CORR_CLASSES)

    return df[df["calibration_name"] == calibration_name].copy()

# ── Construção da matriz de correlação ─────────────────────────────────────────

def _build_full_corr_matrix(
    assets: list[AssetClass],
    corr_df: pd.DataFrame,
) -> np.ndarray:
    """
    Monta matriz n × n a partir dos pares cadastrados em DB_CORR_CLASSES,
    na ordem de `assets`. Diagonal = 1; par (j, i) é espelhado de (i, j).

    `corr_df` tem colunas: classe_a, classe_b, rho.

    Validação dura (fail-loud):
      • Todo par (i, j) com i < j deve estar presente.
      • Não pode haver duplicatas com rho conflitante.
      • Classes referenciadas devem existir no universo.
    """
    n = len(assets)
    name_to_idx = {a.name: i for i, a in enumerate(assets)}
    M = np.eye(n)

    # Conjunto de pares preenchidos (em forma normalizada: tupla ordenada)
    seen: dict[tuple[int, int], float] = {}

    for _, row in corr_df.iterrows():
        ca = row["classe_a"]
        cb = row["classe_b"]
        rho = float(row["rho"])

        ca_name = _resolve_relation_value(ca, assets)
        cb_name = _resolve_relation_value(cb, assets)

        if ca_name is None or cb_name is None:
            raise FiberyLoaderError(
                f"Não consegui resolver par de correlação: ({ca}, {cb})"
            )
        if ca_name not in name_to_idx or cb_name not in name_to_idx:
            raise FiberyLoaderError(
                f"Par de correlação referencia classe inexistente no universo: "
                f"('{ca_name}', '{cb_name}')"
            )

        i = name_to_idx[ca_name]
        j = name_to_idx[cb_name]
        if i == j:
            # Diagonal: deve ser 1.0 (tolerância numérica)
            if abs(rho - 1.0) > 1e-9:
                raise FiberyLoaderError(
                    f"Par diagonal ('{ca_name}','{cb_name}') tem rho={rho}, "
                    f"esperado 1.0"
                )
            continue

        key = (min(i, j), max(i, j))
        if key in seen and abs(seen[key] - rho) > 1e-9:
            raise FiberyLoaderError(
                f"Par ('{ca_name}','{cb_name}') aparece duplicado com rho "
                f"conflitante: {seen[key]} vs {rho}"
            )
        seen[key] = rho
        M[i, j] = rho
        M[j, i] = rho

    # ── Completude: todos os pares i<j devem estar presentes ────────────
    missing: list[tuple[str, str]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if (i, j) not in seen:
                missing.append((assets[i].name, assets[j].name))

    if missing:
        preview = ", ".join(f"({a}, {b})" for a, b in missing[:10])
        more = f" ... e mais {len(missing) - 10}" if len(missing) > 10 else ""
        raise FiberyLoaderError(
            f"Correlações Cross-Classe incompletas: faltam {len(missing)} "
            f"pares no Fibery. Exemplos: {preview}{more}"
        )

    return M


# ── Livro e RC intra-bucket ───────────────────────────────────────────────────

def _check_livro(livro: str) -> str:
    """Normaliza o livro pedido pelo chamador ('offshore' → 'Offshore')."""
    norm = {lv.lower(): lv for lv in LIVROS}.get(str(livro).strip().lower())
    if norm is None:
        raise FiberyLoaderError(f"Livro '{livro}' inválido; use um de {list(LIVROS)}")
    return norm


def _livro_column(df: pd.DataFrame, table_name: str) -> np.ndarray:
    """
    Coluna Livro normalizada. Campo ausente ou valor vazio → "Onshore"
    (cadastro anterior ao offshore). Valor fora de Onshore/Offshore → erro.
    """
    col = next((c for c in _LIVRO_COLUMNS if c in df.columns), None)
    if col is None:
        return np.full(len(df), LIVRO_ONSHORE, dtype=object)

    out = []
    for value in df[col].tolist():
        if isinstance(value, dict):
            value = value.get("Name") or value.get("name") or value.get("enum/name")
        if value is None or (isinstance(value, float) and np.isnan(value)) or str(value).strip() == "":
            out.append(LIVRO_ONSHORE)
            continue
        try:
            out.append(_check_livro(value))
        except FiberyLoaderError as exc:
            raise FiberyLoaderError(f"{table_name}: {exc}") from exc
    return np.array(out, dtype=object)


def _build_intra_rc_weights(
    merged: pd.DataFrame,
    bucket_order: list[str],
) -> Optional[dict[str, np.ndarray]]:
    """
    Divisão do RC entre as classes de cada bucket, a partir de "RC Intra-Bucket".

    Bucket sem nenhum valor → uniforme (fica fora do dict). Bucket com valores
    em só parte das classes, valor negativo ou soma zero → erro: preenchimento
    parcial quase sempre é esquecimento, e o motor não deve adivinhar.
    """
    if "rc_intra" not in merged.columns:
        return None

    weights: dict[str, np.ndarray] = {}
    for bk in bucket_order:
        rows = merged[merged["bucket"] == bk]
        vals = pd.to_numeric(rows["rc_intra"], errors="coerce")
        if vals.isna().all():
            continue
        if vals.isna().any():
            faltando = rows.loc[vals.isna(), "name"].tolist()
            raise FiberyLoaderError(
                f"Bucket '{bk}': RC Intra-Bucket preenchido só em parte das "
                f"classes; faltam {faltando}"
            )
        if (vals < 0).any() or vals.sum() <= 0:
            raise FiberyLoaderError(
                f"Bucket '{bk}': RC Intra-Bucket deve ser >= 0 com soma > 0"
            )
        weights[bk] = vals.to_numpy(dtype=float)
    return weights or None


# ── Utilitários ───────────────────────────────────────────────────────────────

def _normalize_columns(
    df: pd.DataFrame,
    mapping: dict[str, list[str]],
    table_name: str,
) -> pd.DataFrame:
    """
    Normaliza colunas do DataFrame retornado por read_fibery para nomes
    canônicos esperados pelo loader. Cada chave do mapping é o nome
    canônico; o valor é uma lista de nomes possíveis no DataFrame.

    Levanta FiberyLoaderError se uma coluna obrigatória não for encontrada.
    """
    normalized = pd.DataFrame()
    missing: list[str] = []

    for canonical, candidates in mapping.items():
        found = None
        for cand in candidates:
            if cand in df.columns:
                found = cand
                break
        if found is None:
            missing.append(f"{canonical} (tentou: {candidates})")
        else:
            normalized[canonical] = df[found].values

    if missing:
        raise FiberyLoaderError(
            f"Colunas ausentes em '{table_name}':\n  " + "\n  ".join(missing) +
            f"\nColunas disponíveis: {list(df.columns)}"
        )

    return normalized


def _resolve_relation_value(value, assets: list[AssetClass]) -> Optional[str]:
    """
    Resolve um valor de campo de relação para o nome da classe.
    Pode vir como:
      • string (nome direto)
      • dict {'Name': '...', 'Id': '...'}
      • UUID (se for ID, busca na lista de assets pelo ID)
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, str):
        # Tenta como nome direto primeiro
        for a in assets:
            if a.name == value:
                return a.name
        # Não bateu como nome — pode ser ID
        return value  # devolve a string e deixa o caller validar
    if isinstance(value, dict):
        return value.get("Name") or value.get("name")
    return None
