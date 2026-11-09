"""
Estruturas de configuração do espectro de alocação.

`SpectrumConfig` é a "interface estável" entre os loaders (Fibery, defaults,
YAML futuro) e o motor (`core.build_spectrum`). Toda hidratação de dados
externos converge para este dataclass; o motor consome apenas isso.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np


# ── Estruturas atômicas ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class AssetClass:
    """
    Uma classe de ativo no universo. Combina informações estáticas (catálogo)
    com parâmetros calibrados (vols, max weight, max RC).

    Tipo de campos:
      • name, bucket, proxy   → catálogo (Inv-Rsrch-Quant/Classes de Ativo)
      • default_vol, max_*    → calibração (Inv-Rsrch-Quant/Parâmetros de Classe)
    """
    name: str
    bucket: str
    proxy: str
    default_vol: float          # fração; 0.10 = 10% a.a.
    max_weight: float           # fração; 0.20 = 20%
    max_rc: Optional[float] = None  # fração; None = sem limite explícito


@dataclass(frozen=True)
class RCTarget:
    """
    Distribuição-alvo de contribuição de risco para um bucket nos endpoints
    do espectro (perfil 1 e perfil N). O motor interpola linearmente entre
    os endpoints para gerar os RC-targets dos perfis intermediários.

    A soma de `rc_p1` (e `rc_p10`) entre todos os buckets deve ser 1.0.
    """
    bucket: str
    rc_p1: float
    rc_p10: float


@dataclass(frozen=True)
class BucketConfig:
    """
    Configuração estrutural de um bucket. Independente de calibração:
    define como o bucket se comporta na otimização.
    """
    name: str
    label: str
    intra_max_weight: Optional[float] = None  # peso máx por classe no RP intra-bucket


# ── Configuração principal ───────────────────────────────────────────────────

@dataclass
class SpectrumConfig:
    """
    Conjunto coerente de inputs para `build_spectrum`. Tipicamente populado
    por um loader, mas pode ser construído manualmente para testes/cenários.

    Campos obrigatórios:
      assets, buckets, rc_targets, full_corr

    Campos opcionais (com defaults razoáveis):
      sigma_min_pct, sigma_max_pct, n_profiles, min_weight_threshold,
      intra_rc_weights

    Metadados (opcionais, vindos do Fibery):
      calibration_name, calibration_status
    """
    # Universo de ativos — ORDEM IMPORTA (define indexação posicional)
    assets: list[AssetClass]

    # Configuração estrutural dos buckets — chave é o `bucket` em AssetClass
    buckets: dict[str, BucketConfig]

    # RC-targets nos endpoints — chave é o nome do bucket
    rc_targets: dict[str, RCTarget]

    # Matriz de correlação completa (n × n), na ordem de `assets`.
    # Contém TODOS os pares (intra-bucket e cross-bucket).
    full_corr: np.ndarray

    # Parâmetros do motor
    sigma_min_pct: float
    sigma_max_pct: float
    n_profiles: int
    min_weight_threshold: float

    # Distribuição não-uniforme de RC dentro de bucket (opcional)
    # intra_rc_weights[bucket] = array de pesos (soma > 0) para distribuir
    # o RC-target do bucket entre suas classes. Se ausente, distribuição
    # é uniforme entre as classes do bucket.
    intra_rc_weights: Optional[dict[str, np.ndarray]] = None

    # Curvatura da interpolação de RC por bucket (opcional)
    # rc_curvature[bucket] = expoente gamma_b > 0 da progressão de RC entre
    # os endpoints P1 e P10. gamma=1 → linear (default); gamma<1 → bucket
    # transita cedo (côncavo); gamma>1 → transita tarde (convexo). Buckets
    # ausentes do dict assumem gamma=1.0. A soma de RC por perfil é sempre
    # renormalizada para 1.0 no motor.
    rc_curvature: Optional[dict[str, float]] = None

    # Metadados de procedência (opcional)
    calibration_name: Optional[str] = None
    calibration_date: Optional[str] = None
    calibration_status: Optional[str] = None
    calibration_livro: Optional[str] = None    # "Onshore" ou "Offshore"

    # ── Propriedades derivadas ───────────────────────────────────────────

    @property
    def class_names(self) -> list[str]:
        """Nomes das classes na ordem em que aparecem em `assets`."""
        return [a.name for a in self.assets]

    @property
    def bucket_keys(self) -> list[str]:
        """Buckets na ordem em que aparecem em `buckets`."""
        return list(self.buckets.keys())

    @property
    def n_assets(self) -> int:
        return len(self.assets)

    @property
    def n_buckets(self) -> int:
        return len(self.buckets)

    @property
    def bucket_indices(self) -> dict[str, list[int]]:
        """
        Mapeamento bucket → índices das classes naquele bucket, na ordem
        em que aparecem em `assets`.
        """
        indices: dict[str, list[int]] = {bk: [] for bk in self.buckets}
        for i, a in enumerate(self.assets):
            if a.bucket not in indices:
                raise ValueError(
                    f"AssetClass '{a.name}' tem bucket '{a.bucket}' que não "
                    f"está em `buckets`: {list(self.buckets.keys())}"
                )
            indices[a.bucket].append(i)
        return indices

    @property
    def vols_array(self) -> np.ndarray:
        return np.array([a.default_vol for a in self.assets])

    @property
    def max_weights_array(self) -> np.ndarray:
        return np.array([a.max_weight for a in self.assets])

    @property
    def max_rc_array(self) -> np.ndarray:
        """Vetor (n,) com NaN onde não há limite explícito."""
        return np.array([a.max_rc if a.max_rc is not None else np.nan for a in self.assets])

    @property
    def intra_bounds(self) -> dict[str, Optional[float]]:
        return {bk: b.intra_max_weight for bk, b in self.buckets.items()}

    @property
    def bucket_labels(self) -> dict[str, str]:
        return {bk: b.label for bk, b in self.buckets.items()}

    @property
    def rc_curvature_array(self) -> Optional[np.ndarray]:
        """
        Vetor (n_buckets,) de expoentes gamma na ordem de `bucket_keys`.
        Buckets ausentes de `rc_curvature` recebem gamma=1.0 (linear).
        Retorna None se `rc_curvature` não foi fornecido (motor usa linear).
        """
        if self.rc_curvature is None:
            return None
        return np.array(
            [float(self.rc_curvature.get(bk, 1.0)) for bk in self.bucket_keys]
        )

    # ── Validação ────────────────────────────────────────────────────────

    def validate(self, tol: float = 1e-6) -> None:
        """
        Valida consistência interna. Chamado automaticamente pelo motor.
        Levanta ValueError com mensagem específica em caso de erro.
        """
        # Universo não-vazio
        if not self.assets:
            raise ValueError("SpectrumConfig.assets está vazio")
        if not self.buckets:
            raise ValueError("SpectrumConfig.buckets está vazio")

        # Cada classe pertence a um bucket conhecido
        for a in self.assets:
            if a.bucket not in self.buckets:
                raise ValueError(
                    f"Classe '{a.name}' tem bucket '{a.bucket}' que não está em "
                    f"`buckets`: {list(self.buckets.keys())}"
                )

        # Cada bucket tem pelo menos uma classe
        bi = self.bucket_indices
        for bk, idxs in bi.items():
            if not idxs:
                raise ValueError(f"Bucket '{bk}' não tem classes associadas")

        # Vols e max_weight válidos
        for a in self.assets:
            if a.default_vol <= 0:
                raise ValueError(f"default_vol de '{a.name}' deve ser > 0")
            if not 0.0 < a.max_weight <= 1.0:
                raise ValueError(
                    f"max_weight de '{a.name}' deve estar em (0, 1], recebido {a.max_weight}"
                )
            if a.max_rc is not None and not 0.0 < a.max_rc <= 1.0:
                raise ValueError(
                    f"max_rc de '{a.name}' deve estar em (0, 1] ou ser None"
                )

        # RC-targets cobrem exatamente os buckets
        rc_keys = set(self.rc_targets.keys())
        bk_keys = set(self.buckets.keys())
        if rc_keys != bk_keys:
            raise ValueError(
                f"rc_targets ({sorted(rc_keys)}) não cobre exatamente os "
                f"buckets ({sorted(bk_keys)})"
            )
        # Soma dos RC em cada endpoint deve ser 1
        for endpoint in ("p1", "p10"):
            attr = "rc_p1" if endpoint == "p1" else "rc_p10"
            total = sum(getattr(self.rc_targets[bk], attr) for bk in self.bucket_keys)
            if abs(total - 1.0) > tol:
                raise ValueError(
                    f"Soma de rc_targets[*].{attr} = {total:.6f}, esperado 1.0"
                )
            # Cada valor em [0, 1]
            for bk in self.bucket_keys:
                v = getattr(self.rc_targets[bk], attr)
                if not 0.0 <= v <= 1.0:
                    raise ValueError(f"rc_targets['{bk}'].{attr} = {v} fora de [0,1]")

        # Full corr: shape e PSD (cobre todos os pares intra + cross-bucket)
        n = self.n_assets
        if self.full_corr.shape != (n, n):
            raise ValueError(
                f"full_corr tem shape {self.full_corr.shape}, esperado ({n}, {n})"
            )
        _check_correlation_matrix(self.full_corr, "full_corr")

        # intra_rc_weights (se fornecido)
        if self.intra_rc_weights is not None:
            for bk, w in self.intra_rc_weights.items():
                if bk not in self.buckets:
                    raise ValueError(f"intra_rc_weights['{bk}'] — bucket desconhecido")
                if len(w) != len(bi[bk]):
                    raise ValueError(
                        f"intra_rc_weights['{bk}'] tem {len(w)} elementos, "
                        f"esperado {len(bi[bk])}"
                    )
                if w.sum() <= 0:
                    raise ValueError(f"intra_rc_weights['{bk}'] tem soma <= 0")

        # rc_curvature (se fornecido)
        if self.rc_curvature is not None:
            for bk, g in self.rc_curvature.items():
                if bk not in self.buckets:
                    raise ValueError(f"rc_curvature['{bk}'] — bucket desconhecido")
                if not g > 0:
                    raise ValueError(
                        f"rc_curvature['{bk}'] = {g} deve ser > 0"
                    )

        # Parâmetros do motor
        if self.sigma_min_pct >= self.sigma_max_pct:
            raise ValueError(
                f"sigma_min_pct ({self.sigma_min_pct}) deve ser < "
                f"sigma_max_pct ({self.sigma_max_pct})"
            )
        if self.n_profiles < 1:
            raise ValueError(f"n_profiles deve ser >= 1, recebido {self.n_profiles}")
        if not 0.0 <= self.min_weight_threshold < 1.0:
            raise ValueError(
                f"min_weight_threshold deve estar em [0, 1), recebido {self.min_weight_threshold}"
            )

    # ── Construtores convenientes ────────────────────────────────────────

    def with_overrides(self, **kwargs) -> "SpectrumConfig":
        """
        Cria nova SpectrumConfig com campos sobrescritos. Útil para
        cenários (ex.: rodar a calibração atual, mas com sigma_max_pct
        diferente).

        Não modifica `self`.
        """
        from dataclasses import replace
        return replace(self, **kwargs)


# ── Helpers internos ──────────────────────────────────────────────────────

def _check_correlation_matrix(m: np.ndarray, name: str, tol: float = 1e-8) -> None:
    """Verifica simetria, diagonal=1 e PSD."""
    if not np.allclose(m, m.T, atol=tol):
        raise ValueError(f"{name} não é simétrica")
    if not np.allclose(np.diag(m), 1.0, atol=tol):
        raise ValueError(f"{name} não tem diagonal 1.0")
    min_eig = float(np.linalg.eigvalsh(m).min())
    if min_eig < -tol:
        raise ValueError(
            f"{name} não é positiva semi-definida (menor autovalor: {min_eig:.6f})"
        )
