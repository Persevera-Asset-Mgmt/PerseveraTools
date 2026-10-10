"""
ANBIMA Feed API provider with OAuth2 authentication.

Uses client_credentials flow per ANBIMA documentation:
https://developers.anbima.com.br/pt/documentacao/visao-geral/autenticacao/#oauth2

Production: https://api.anbima.com.br
Sandbox: https://api-sandbox.anbima.com.br (hyphen; cert is for this hostname)

Covers two API families:
- Preços e Índices (AnbimaFeedProvider): /feed/precos-indices/...
- Fundos v2 RCVM 175 (AnbimaFundosProvider): /feed/fundos/v2/fundos/...
"""

import base64
import re
import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

from .base import DataProvider, DataRetrievalError


class AnbimaFeedNotFoundError(DataRetrievalError):
    """Raised when the ANBIMA Feed API returns 404 (no data for the requested date)."""
from ...config import settings
from ...utils.logging import get_logger

logger = get_logger(__name__)

# Default base URLs (documentation)
ANBIMA_FEED_PRODUCTION = "https://api.anbima.com.br"
ANBIMA_FEED_SANDBOX = "https://api-sandbox.anbima.com.br"
OAUTH_PATH = "/oauth/access-token"
FEED_BASE_PATH = "/feed/precos-indices"
FUNDOS_BASE_PATH = "/feed/fundos/v2/fundos"

# Map category -> (path_suffix, date_param_name)
# path_suffix includes version (v1/ or v2/) and is appended to FEED_BASE_PATH
FEED_ENDPOINTS: Dict[str, tuple] = {
    # --- Títulos Públicos (v1) ---
    "anbima_feed_titulos_publicos_mercado_secundario": (
        "v1/titulos-publicos/mercado-secundario-TPF",
        "data",
    ),
    "anbima_feed_titulos_publicos_vna": ("v1/titulos-publicos/vna", "data"),
    "anbima_feed_titulos_publicos_curvas_juros": (
        "v1/titulos-publicos/curvas-juros",
        "data",
    ),
    "anbima_feed_titulos_publicos_curva_intradiaria": (
        "v1/titulos-publicos/curva-intradiaria",
        "data",
    ),
    "anbima_feed_titulos_publicos_pu_intradiario": (
        "v1/titulos-publicos/pu-intradiario",
        "data",
    ),
    "anbima_feed_titulos_publicos_difusao_taxas": (
        "v1/titulos-publicos/difusao-taxas",
        "data",
    ),
    "anbima_feed_titulos_publicos_estimativa_selic": (
        "v1/titulos-publicos/estimativa-selic",
        "data",
    ),
    "anbima_feed_titulos_publicos_projecoes": (
        "v1/titulos-publicos/projecoes",
        None,
    ),
    # --- Debêntures (v1) ---
    "anbima_feed_debentures_mercado_secundario": (
        "v1/debentures/mercado-secundario",
        "data",
    ),
    "anbima_feed_debentures_curvas_credito": (
        "v1/debentures/curvas-credito",
        "data",
    ),
    "anbima_feed_debentures_projecoes": ("v1/debentures/projecoes", None),
    # --- Debêntures+ (v1) ---
    "anbima_feed_debentures_mais_mercado_secundario": (
        "v1/debentures-mais/mercado-secundario",
        "data",
    ),
    # --- CRI/CRA (v1) ---
    "anbima_feed_cri_cra_mercado_secundario": (
        "v1/cri-cra/mercado-secundario",
        "data",
    ),
    "anbima_feed_cri_cra_projecoes": ("v1/cri-cra/projecoes", None),
    # --- FIDC (v1) ---
    "anbima_feed_fidc_mercado_secundario": (
        "v1/fidc/mercado-secundario",
        "data",
    ),
    # --- Letras Financeiras (v1) ---
    "anbima_feed_letras_financeiras_matrizes_vertices": (
        "v1/letras-financeiras/matrizes-vertices-emissor",
        "data",
    ),
    # --- REUNE (v1) - requer instrumento= (debenture, cra, cri, cff) ---
    "anbima_feed_reune_previas": ("v1/reune/previas-do-reune", "data"),
    # --- Índices (v1) ---
    "anbima_feed_indices_carteira_teorica_ida": (
        "v1/indices/carteira-teorica-ida",
        None,
    ),
    "anbima_feed_indices_resultados_ida_fechado": (
        "v1/indices/resultados-ida-fechado",
        "data",
    ),
    "anbima_feed_indices_carteira_teorica_ihfa": (
        "v1/indices/carteira-teorica-ihfa",
        None,
    ),
    "anbima_feed_indices_resultados_ihfa_fechado": (
        "v1/indices/resultados-ihfa-fechado",
        "data",
    ),
    "anbima_feed_indices_resultados_idka": (
        "v1/indices/resultados-idka",
        "data",
    ),
    "anbima_feed_indices_carteira_teorica_ima": (
        "v1/indices/carteira-teorica-ima",
        None,
    ),
    "anbima_feed_indices_resultados_ima": (
        "v1/indices/resultados-ima",
        "data",
    ),
    "anbima_feed_indices_resultados_intradiarios_ima": (
        "v1/indices/resultados-intradiarios-ima",
        "data",
    ),
    # --- Índices v2 (IHFA RCVM 175) ---
    "anbima_feed_indices_carteira_teorica_ihfa_v2": (
        "v2/indices/carteira-teorica-ihfa",
        None,
    ),
    # --- Índices+ (v1) ---
    "anbima_feed_indices_mais_previa_carteira_ida": (
        "v1/indices-mais/previa-carteira-teorica-ida",
        None,
    ),
    "anbima_feed_indices_mais_carteira_teorica_ida": (
        "v1/indices-mais/carteira-teorica-ida",
        None,
    ),
    "anbima_feed_indices_mais_resultados_ida": (
        "v1/indices-mais/resultados-ida",
        "data",
    ),
    "anbima_feed_indices_mais_carteira_teorica_ihfa": (
        "v1/indices-mais/carteira-teorica-ihfa",
        None,
    ),
    "anbima_feed_indices_mais_resultados_ihfa": (
        "v1/indices-mais/resultados-ihfa",
        "data",
    ),
    "anbima_feed_indices_mais_resultados_idka": (
        "v1/indices-mais/resultados-idka",
        "data",
    ),
    "anbima_feed_indices_mais_previa_carteira_ima": (
        "v1/indices-mais/previa-carteira-teorica-ima",
        None,
    ),
    "anbima_feed_indices_mais_carteira_teorica_ima": (
        "v1/indices-mais/carteira-teorica-ima",
        None,
    ),
    "anbima_feed_indices_mais_resultados_ima": (
        "v1/indices-mais/resultados-ima",
        "data",
    ),
    "anbima_feed_indices_mais_resultados_intradiarios_ima": (
        "v1/indices-mais/resultados-intradiarios-ima",
        "data",
    ),
    # --- Índices+ v2 (IHFA/resultados RCVM 175) ---
    "anbima_feed_indices_mais_carteira_teorica_ihfa_v2": (
        "v2/indices-mais/carteira-teorica-ihfa",
        None,
    ),
    "anbima_feed_indices_mais_resultados_ihfa_v2": (
        "v2/indices-mais/resultados-ihfa",
        "data",
    ),
    # --- IDA LIQ (v1) ---
    "anbima_feed_ida_liq_previa_carteira": (
        "v1/ida-liq/previa-carteira-teorica-ida-liq",
        None,
    ),
    "anbima_feed_ida_liq_carteira_teorica": (
        "v1/ida-liq/carteira-teorica-ida",
        None,
    ),
    "anbima_feed_ida_liq_resultados": (
        "v1/ida-liq/resultados-ida",
        "data",
    ),
    # --- IMA para ETFs (v2) ---
    "anbima_feed_ima_etf_previa_carteira": (
        "v2/ima-etf/previa-carteira-teorica",
        None,
    ),
    "anbima_feed_ima_etf_carteira_teorica": (
        "v2/ima-etf/carteira-teorica",
        None,
    ),
    "anbima_feed_ima_etf_resultado_diario": (
        "v2/ima-etf/resultado-diario",
        "data",
    ),
    "anbima_feed_ima_etf_composicao_diaria": (
        "v2/ima-etf/composicao-diaria",
        "data",
    ),
    "anbima_feed_ima_etf_resultado_intradiario": (
        "v2/ima-etf/resultado-intradiario",
        "data",
    ),
    "anbima_feed_ima_etf_pu_intradiario": (
        "v2/ima-etf/pu-intradiario",
        "data",
    ),
    "anbima_feed_ima_etf_negocios_extra": (
        "v2/ima-etf/negocios-extra",
        "data",
    ),
}

# Per-endpoint normalization specs.
#
# Quando uma categoria possui entrada aqui, ``AnbimaFeedProvider.get_data`` converte
# o DataFrame "wide" devolvido pela API para o formato long padronizado
# ``[date, code, field, value, source]`` — esperado pelas tabelas
# ``credito_privado_historico`` / ``indicadores`` e compatível com o
# ``AnbimaProvider`` legado. Use ``raw=True`` em ``get_data`` para receber o JSON
# bruto sem transformação.
NORMALIZE_CONFIG: Dict[str, Dict[str, Any]] = {
    "anbima_feed_titulos_publicos_mercado_secundario": {
        "rename": {
            "tipo_titulo": "code",
            "data_referencia": "date",
            "data_vencimento": "maturity",
            "taxa_indicativa": "yield_to_maturity",
            "taxa_compra": "bid_rate",
            "taxa_venda": "offer_rate",
            "pu": "price_close",
            "desvio_padrao": "std_dev",
            "intervalo_min_d0": "min_d0",
            "intervalo_max_d0": "max_d0",
            "intervalo_min_d1": "min_d1",
            "intervalo_max_d1": "max_d1",
        },
        "id_vars": ["code", "date", "maturity"],
        "datetime_vars": ["date", "maturity"],
        "value_vars": [
            "yield_to_maturity",
            "bid_rate",
            "offer_rate",
            "price_close",
            "std_dev",
            "min_d0",
            "max_d0",
            "min_d1",
            "max_d1",
        ],
        "source": "anbima",
    },
    "anbima_feed_debentures_mercado_secundario": {
        "rename": {
            "codigo_ativo": "code",
            "data_referencia": "date",
            "taxa_indicativa": "yield_to_maturity",
            "pu": "price_close",
            "duration": "duration",
            "percent_pu_par": "par_percentage",
        },
        "id_vars": ["code", "date"],
        "value_vars": [
            "yield_to_maturity",
            "price_close",
            "duration",
            "par_percentage",
        ],
        "source": "anbima",
    },
    "anbima_feed_debentures_mais_mercado_secundario": {
        "rename": {
            "codigo_ativo": "code",
            "data_referencia": "date",
            "taxa_indicativa": "yield_to_maturity",
            "pu": "price_close",
            "duration": "duration",
            "percent_pu_par": "par_percentage",
            "pu_par": "par_value",
        },
        "id_vars": ["code", "date"],
        "value_vars": [
            "yield_to_maturity",
            "price_close",
            "duration",
            "par_percentage",
            "par_value",
        ],
        "source": "anbima",
    },
    "anbima_feed_cri_cra_mercado_secundario": {
        "rename": {
            "codigo_ativo": "code",
            "data_referencia": "date",
            "taxa_indicativa": "yield_to_maturity",
            "pu": "price_close",
            "duration": "duration",
            "percent_pu_par": "par_percentage",
        },
        "id_vars": ["code", "date"],
        "value_vars": [
            "yield_to_maturity",
            "price_close",
            "duration",
            "par_percentage",
        ],
        "source": "anbima",
    },
}

# Fundos v2 (RCVM 175) – endpoints that do NOT require a path-level code.
# Value is the path suffix appended to FUNDOS_BASE_PATH.
FUNDOS_ENDPOINTS: Dict[str, str] = {
    # Lista de fundos (page, size, tipo_fundo)
    "anbima_fundos_lista": "",
    # Lista de instituições (page, size)
    "anbima_fundos_instituicoes": "/instituicoes",
    # Lote dados cadastrais (data_atualizacao obrigatório, tipo_fundo, page, size)
    "anbima_fundos_lote_dados_cadastrais": "/dados-cadastrais/lote",
    # Lote série histórica (data_atualizacao obrigatório, tipo_fundo, size, cursor)
    "anbima_fundos_lote_serie_historica": "/serie-historica/lote",
}


class AnbimaFeedProvider(DataProvider):
    """
    Provider for ANBIMA Feed API (OAuth2).

    Requires PERSEVERA_ANBIMA_FEED_CLIENT_ID and PERSEVERA_ANBIMA_FEED_CLIENT_SECRET.
    Padrão é sandbox (true). Use PERSEVERA_ANBIMA_FEED_SANDBOX=false para produção (requer acesso liberado pela ANBIMA).

    Categorias disponíveis: Títulos Públicos, Debêntures, Debêntures+, CRI/CRA, FIDC,
    Letras Financeiras, REUNE, Índices, Índices+, IDA LIQ, IMA para ETFs.
    Para anbima_feed_reune_previas é obrigatório passar instrumento= ('debenture', 'cra', 'cri' ou 'cff').
    Endpoints com mes/ano: passe mes= e ano= em kwargs. IMA ETF aceita etf= (ex: 'IMA_B5_MAIS,IRF_M_P2').

    Formato de saída:
        Por padrão, categorias com entrada em ``NORMALIZE_CONFIG`` são convertidas
        para o formato long ``[date, code, field, value, source]``,
        pronto para upsert em ``credito_privado_historico``. Categorias sem
        normalização (ou ``get_data(..., raw=True)``) devolvem o JSON da API
        convertido em DataFrame "wide".
    """

    def __init__(self, start_date: str = "1980-01-01", sandbox: Optional[bool] = None):
        super().__init__(start_date)
        self._client_id = getattr(settings, "ANBIMA_FEED_CLIENT_ID", None)
        self._client_secret = getattr(settings, "ANBIMA_FEED_CLIENT_SECRET", None)
        if sandbox is not None:
            self._sandbox = sandbox
        else:
            sandbox_env = getattr(settings, "ANBIMA_FEED_SANDBOX", None)
            self._sandbox = str(sandbox_env).lower() in ("1", "true", "yes")
        base_url = getattr(settings, "ANBIMA_FEED_BASE_URL", None)
        self._base_url = (base_url or (ANBIMA_FEED_SANDBOX if self._sandbox else ANBIMA_FEED_PRODUCTION)).rstrip("/")
        # OAuth endpoint exists only on production; token is valid for both prod and sandbox
        self._oauth_base_url = ANBIMA_FEED_PRODUCTION.rstrip("/")
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._buffer_seconds = 60

    def _ensure_credentials(self) -> None:
        if not self._client_id or not self._client_secret:
            raise DataRetrievalError(
                "ANBIMA Feed requires PERSEVERA_ANBIMA_FEED_CLIENT_ID and "
                "PERSEVERA_ANBIMA_FEED_CLIENT_SECRET in settings/env."
            )

    def _get_access_token(self) -> str:
        self._ensure_credentials()
        now = time.time()
        if self._access_token and now < self._token_expires_at:
            return self._access_token
        url = f"{self._oauth_base_url}{OAUTH_PATH}"
        credentials = f"{self._client_id}:{self._client_secret}"
        b64 = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {b64}",
        }
        payload = {"grant_type": "client_credentials"}
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("ANBIMA Feed OAuth request failed: %s", e)
            raise DataRetrievalError(f"ANBIMA Feed OAuth failed: {e}") from e
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        if not token:
            raise DataRetrievalError(
                "ANBIMA Feed OAuth response did not contain access_token"
            )
        self._access_token = token
        self._token_expires_at = now + expires_in - self._buffer_seconds
        logger.debug("ANBIMA Feed access token obtained, expires in %ss", expires_in)
        return token

    def _auth_headers(self) -> Dict[str, str]:
        token = self._get_access_token()
        return {
            "Content-Type": "application/json",
            "client_id": self._client_id,
            "access_token": token,
            "Authorization": f"Bearer {token}",
        }

    _RETRY_STATUS_CODES = {502, 503, 504}
    _MAX_RETRIES = 3
    _RETRY_BACKOFF = 2.0  # segundos; multiplicado por 2^tentativa

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self._base_url}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(self._MAX_RETRIES):
            try:
                resp = requests.get(
                    url,
                    headers=self._auth_headers(),
                    params=params,
                    timeout=60,
                )
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status in self._RETRY_STATUS_CODES and attempt < self._MAX_RETRIES - 1:
                    wait = self._RETRY_BACKOFF * (2 ** attempt)
                    logger.warning(
                        "ANBIMA Feed HTTP %s em %s (tentativa %d/%d); "
                        "aguardando %.0fs antes de tentar novamente.",
                        status, url, attempt + 1, self._MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    last_exc = e
                    continue
                if e.response is not None:
                    if status == 401:
                        self._access_token = None
                        self._token_expires_at = 0.0
                        msg = (
                            e.response.text[:500]
                            + " | Confira: use credenciais de produção (sem SANDBOX) para "
                            "api.anbima.com.br; credenciais de sandbox para api-sandbox.anbima.com.br."
                        )
                        logger.error("ANBIMA Feed API error %s: %s", status, msg)
                    elif status == 404:
                        # 404 é condição normal: sem dados para a data solicitada ou fim de paginação
                        logger.debug("ANBIMA Feed API 404 em %s: %s", url, e.response.text[:200])
                        raise AnbimaFeedNotFoundError(
                            f"ANBIMA Feed API request failed: {e}"
                        ) from e
                    else:
                        msg = e.response.text[:500]
                        logger.error("ANBIMA Feed API error %s: %s", status, msg)
                raise DataRetrievalError(f"ANBIMA Feed API request failed: {e}") from e
            except requests.exceptions.RequestException as e:
                logger.error("ANBIMA Feed request failed: %s", e)
                raise DataRetrievalError(f"ANBIMA Feed request failed: {e}") from e
        raise DataRetrievalError(
            f"ANBIMA Feed API request failed após {self._MAX_RETRIES} tentativas: {last_exc}"
        ) from last_exc

    def _json_to_long(
        self,
        data: Any,
        date_col: str = "data_referencia",
        code_col: Optional[str] = "codigo_selic",
        value_columns: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """
        Convert API JSON (list of dicts or nested structure) to long format
        [date, code, field, value].
        """
        if isinstance(data, dict) and "data" in data:
            rows = data["data"]
        elif isinstance(data, list):
            rows = data
        else:
            rows = [data] if isinstance(data, dict) else []

        if not rows:
            return pd.DataFrame(columns=["date", "code", "field", "value"])

        df = pd.DataFrame(rows)
        # Normalize column names (API may use snake_case)
        df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

        date_col_lower = date_col.lower().replace(" ", "_")
        code_col_lower = (code_col or "code").lower().replace(" ", "_")

        id_cols = []
        if date_col_lower in df.columns:
            id_cols.append(date_col_lower)
        if code_col_lower in df.columns:
            id_cols.append(code_col_lower)

        # Use first column as code if no code column
        if not id_cols and len(df.columns) > 0:
            id_cols = [df.columns[0]]

        value_cols = value_columns
        if value_cols is None:
            value_cols = [c for c in df.columns if c not in id_cols]
        else:
            value_cols = [c.lower().replace(" ", "_") for c in value_cols]
            value_cols = [c for c in value_cols if c in df.columns]

        out = df.melt(
            id_vars=[c for c in id_cols if c in df.columns],
            value_vars=value_cols,
            var_name="field",
            value_name="value",
        )
        out = out.dropna(subset=["value"])
        out["value"] = pd.to_numeric(out["value"], errors="coerce")
        out = out.dropna(subset=["value"])

        rename = {}
        if date_col_lower in out.columns:
            rename[date_col_lower] = "date"
        if code_col_lower in out.columns:
            rename[code_col_lower] = "code"
        if rename:
            out = out.rename(columns=rename)

        if "date" not in out.columns and date_col_lower in df.columns:
            out["date"] = df[date_col_lower].iloc[0]
        if "code" not in out.columns:
            out["code"] = "series"

        out["date"] = pd.to_datetime(out["date"], errors="coerce")
        out = out.dropna(subset=["date"])
        return out[["date", "code", "field", "value"]]

    @staticmethod
    def _normalize_long(df: pd.DataFrame, category: str) -> pd.DataFrame:
        """
        Converte o DataFrame "wide" devolvido pela API para o formato long
        padronizado ``[date, code, field, value, source]`` segundo
        a entrada de ``NORMALIZE_CONFIG`` para ``category``.

        Colunas ausentes em ``value_vars`` são silenciosamente descartadas
        (a API ocasionalmente omite campos quando não há valor).
        """
        spec = NORMALIZE_CONFIG[category]
        df = df.rename(columns=spec["rename"])

        id_vars = [c for c in spec["id_vars"] if c in df.columns]
        value_vars = [c for c in spec["value_vars"] if c in df.columns]
        if not value_vars:
            return pd.DataFrame(
                columns=id_vars + ["field", "value", "source"]
            )

        # Convert all declared datetime id_vars (default: just "date")
        for dt_col in spec.get("datetime_vars", ["date"]):
            if dt_col in df.columns:
                df[dt_col] = pd.to_datetime(df[dt_col], errors="coerce")

        out = df.melt(
            id_vars=id_vars,
            value_vars=value_vars,
            var_name="field",
            value_name="value",
        )
        out["source"] = spec.get("source", "anbima")
        out = out.dropna(subset=["value"])
        # Iterating datetime64[ns] yields pd.Timestamp or pd.NaT; pd.isna(pd.NaT) is True.
        # This is the only reliable way to replace NaT with None before DB insertion.
        for col in out.select_dtypes(include=["datetime64[ns]"]).columns:
            out[col] = [None if pd.isna(v) else v for v in out[col]]
        return out.replace({np.nan: None})

    def get_data(self, category: str, raw: bool = False, **kwargs) -> pd.DataFrame:
        """
        Recupera dados de uma categoria do AnbimaFeed.

        Args:
            category: Identificador do endpoint (chave de ``FEED_ENDPOINTS``).
            raw: Se ``True``, devolve o JSON da API convertido em DataFrame sem
                qualquer transformação. Por padrão (``False``), categorias com
                entrada em ``NORMALIZE_CONFIG`` são automaticamente convertidas
                para o formato long ``[date, code, field, value, source]``.
            **kwargs: Parâmetros específicos do endpoint:
                ``data`` (str, YYYY-MM-DD): data pontual. Se omitido em
                    endpoints com parâmetro de data, itera automaticamente
                    sobre o intervalo de dias úteis desde ``start_date`` até
                    hoje, concatenando os resultados. Datas sem dados são
                    silenciosamente ignoradas.
                ``mes``, ``ano``: para endpoints de resultados mensais.
                ``instrumento``: obrigatório para anbima_feed_reune_previas
                    (``'debenture'``, ``'cra'``, ``'cri'`` ou ``'cff'``).
                ``faixa``: faixa horária para REUNE (``'11:00'`` … ``'18:00'``).
                ``etf``: filtro de ETF para endpoints IMA ETF.
        """
        self._ensure_credentials()
        self._log_processing(category)

        if category not in FEED_ENDPOINTS:
            raise ValueError(
                f"Unknown category: {category}. "
                f"Supported: {list(FEED_ENDPOINTS.keys())}"
            )

        path_suffix, date_param = FEED_ENDPOINTS[category]
        path = f"{FEED_BASE_PATH}/{path_suffix}"

        def _build_params(date_str: Optional[str] = None) -> Dict[str, Any]:
            p: Dict[str, Any] = {}
            if date_param and date_str:
                p[date_param] = date_str
            if kwargs.get("mes") is not None:
                p["mes"] = kwargs["mes"]
            if kwargs.get("ano") is not None:
                p["ano"] = kwargs["ano"]
            # REUNE: instrumento obrigatório (debenture, cra, cri, cff); faixa opcional
            if kwargs.get("instrumento") is not None:
                p["instrumento"] = kwargs["instrumento"]
            if kwargs.get("faixa") is not None:
                p["faixa"] = kwargs["faixa"]
            # IMA ETF: etf opcional (ex: IMA_B5_MAIS,IRF_M_P2)
            if kwargs.get("etf") is not None:
                p["etf"] = kwargs["etf"]
            return p

        def _fetch_single(params: Dict[str, Any]) -> pd.DataFrame:
            payload = self._get(path, params=params or None)
            df = pd.DataFrame(payload)
            if df.empty:
                return df
            if not raw and category in NORMALIZE_CONFIG:
                return self._normalize_long(df, category)
            return df

        # Date-range mode: endpoint has a date param but caller did not pin a specific date.
        # Iterate over business days from start_date to today and concatenate results.
        if date_param and not kwargs.get("data"):
            end_date = pd.Timestamp.today().normalize()
            dates = pd.bdate_range(start=self.start_date, end=end_date)
            if len(dates) == 0:
                return pd.DataFrame()
            logger.info(
                "%s: iterando sobre %d dias uteis (%s -> %s).",
                category, len(dates),
                dates[0].strftime("%Y-%m-%d"), dates[-1].strftime("%Y-%m-%d"),
            )
            frames: List[pd.DataFrame] = []
            for dt in dates:
                date_str = dt.strftime("%Y-%m-%d")
                try:
                    logger.info(f"Iterando sobre {category} em {date_str}")
                    df = _fetch_single(_build_params(date_str))
                    if not df.empty:
                        frames.append(df)
                except AnbimaFeedNotFoundError:
                    # Feriado ou dia sem negociação — sem dados esperado, ignorar
                    logger.debug("Sem dados para %s em %s, ignorando.", category, date_str)
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True)

        # Single-date or no-date mode (data= fornecido ou endpoint sem parâmetro de data)
        return _fetch_single(_build_params(kwargs.get("data")))


class AnbimaFundosProvider(AnbimaFeedProvider):
    """
    Provider for ANBIMA Fundos v2 API – RCVM 175.

    Reutiliza a autenticação OAuth2 de AnbimaFeedProvider.

    Todos os métodos que aceitam ``codigo`` também aceitam CNPJ (formatado ou apenas
    dígitos). A resolução CNPJ → código ANBIMA é feita automaticamente via
    ``anbima_fundos_lista`` e é cacheada na instância.

    Categorias disponíveis em get_data():
      - anbima_fundos_lista                  : lista de fundos
          kwargs: page, size, tipo_fundo
      - anbima_fundos_instituicoes           : lista de instituições prestadoras de serviço
          kwargs: page, size
      - anbima_fundos_lote_dados_cadastrais  : lote com dados cadastrais de todos os fundos
          kwargs: data_atualizacao (obrigatório), tipo_fundo, page, size
      - anbima_fundos_lote_serie_historica   : lote com série histórica (PL & cota) de todos os fundos
          kwargs: data_atualizacao (obrigatório), tipo_fundo, size, cursor

    Métodos para endpoints com parâmetro de caminho (aceitam código ANBIMA **ou CNPJ**):
      - get_fundo_detalhes(codigo_ou_cnpj)           : detalhes completos de um fundo/classe/subclasse
      - get_fundo_historico(codigo_ou_cnpj)           : histórico de alterações cadastrais de um fundo
      - get_serie_historica(codigo_ou_cnpj, **kwargs) : série histórica PL & cota de uma classe/subclasse
          kwargs: data_inicio, data_fim, data_atualizacao
      - get_instituicao_detalhes(cnpj)                : detalhes de uma instituição
      - get_segmento_investidor(codigo_ou_cnpj, **kwargs): PL por segmento do investidor
          kwargs: mes_referencia, ano_referencia
      - get_notas_explicativas(codigo_ou_cnpj, **kwargs): notas explicativas de uma classe/subclasse
          kwargs: page, size
      - get_classificacao_historica(codigos_ou_cnpjs) : tipo ANBIMA vigente em cada data, por CNPJ

    Resolução de CNPJ:
      - cnpj_to_codigo_fundo(cnpj) → código F (fundo); aceita CNPJ de fundo ou de classe
      - Para métodos que precisam de código de classe (C/S), um CNPJ de classe resolve
        para a própria classe; um CNPJ de fundo, para a primeira classe (via
        get_fundo_detalhes).
    """

    def __init__(self, start_date: str = "1980-01-01", sandbox: Optional[bool] = None):
        super().__init__(start_date=start_date, sandbox=sandbox)
        # CNPJ (14 dígitos sem formatação) → codigo_fundo (prefixo F)
        self._cnpj_fundo_cache: Dict[str, str] = {}
        # codigo_fundo (F) → CNPJ formatado (cache reverso)
        self._fundo_cnpj_cache: Dict[str, str] = {}
        # codigo_fundo (F) → primeiro codigo_classe (C)
        self._fundo_classe_cache: Dict[str, str] = {}
        # CNPJ da classe (14 dígitos) → codigo_classe (C). Após a RCVM 175 a
        # classe tem CNPJ próprio, que pode diferir do CNPJ do fundo.
        self._cnpj_classe_cache: Dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # CNPJ helpers                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_cnpj(cnpj: str) -> str:
        """Remove formatação do CNPJ e retorna apenas os 14 dígitos."""
        return re.sub(r"[.\-/\s]", "", cnpj).strip()

    @staticmethod
    def _is_cnpj(value: str) -> bool:
        """Retorna True se o valor parece um CNPJ (14 dígitos, com ou sem formatação)."""
        digits = re.sub(r"[.\-/\s]", "", value).strip()
        return digits.isdigit() and len(digits) == 14

    def _cache_list_rows(self, rows: List[Dict]) -> int:
        """
        Indexa linhas de ``anbima_fundos_lista``: CNPJ do fundo e de cada classe
        → código do fundo, e CNPJ da classe → código da classe.

        Returns:
            Número de CNPJs novos no cache de fundos.
        """
        added = 0
        for item in rows:
            cnpj_value = str(item.get("identificador_fundo") or "")
            fund_code = str(item.get("codigo_fundo") or "")
            if not fund_code:
                continue
            keys = []
            if cnpj_value:
                keys.append(self._normalize_cnpj(cnpj_value))
                self._fundo_cnpj_cache.setdefault(fund_code, cnpj_value)
            for cls in item.get("classes") or []:
                cnpj_classe = str(cls.get("identificador_classe") or "")
                codigo_classe = cls.get("codigo_classe")
                if cnpj_classe and codigo_classe:
                    key = self._normalize_cnpj(cnpj_classe)
                    self._cnpj_classe_cache[key] = codigo_classe
                    keys.append(key)
            for key in keys:
                if key not in self._cnpj_fundo_cache:
                    self._cnpj_fundo_cache[key] = fund_code
                    added += 1
        return added

    def cnpj_to_codigo_fundo(self, cnpj: str, tipo_fundo: Optional[str] = None) -> str:
        """
        Resolve um CNPJ (do fundo ou de uma de suas classes) para o código
        ANBIMA do fundo (prefixo F).

        Faz paginação automática em ``anbima_fundos_lista`` até encontrar o CNPJ.
        Os resultados são cacheados na instância (válido enquanto o objeto existir).

        Args:
            cnpj: CNPJ do fundo (formatado ou apenas 14 dígitos).
            tipo_fundo: Filtra a busca por tipo de fundo (``"FIF"``, ``"FII"``,
                ``"FIP"``, ``"FIDC"``, ``"FIAGRO"``, ``"ETF"``, ``"OFFSHORE"``).
                Reduz significativamente o número de páginas varridas quando
                o tipo é conhecido.

        Returns:
            Código do fundo no formato ``F0000000191``.

        Raises:
            DataRetrievalError: Se o CNPJ não for encontrado na listagem.
        """
        normalized = self._normalize_cnpj(cnpj)
        if normalized in self._cnpj_fundo_cache:
            return self._cnpj_fundo_cache[normalized]

        page = 0
        size = 1000
        list_params: Dict[str, Any] = {"size": size}
        if tipo_fundo:
            list_params["tipo-fundo"] = tipo_fundo

        while True:
            list_params["page"] = page
            try:
                raw = self._get(FUNDOS_BASE_PATH, params=list_params)
            except DataRetrievalError as e:
                if "404" in str(e):
                    logger.debug(
                        "cnpj_to_codigo_fundo: página %d retornou 404 — fim da listagem.", page
                    )
                    break
                raise
            rows: List[Dict] = []
            if isinstance(raw, dict):
                rows = raw.get("content", raw) if isinstance(raw.get("content"), list) else []
            elif isinstance(raw, list):
                rows = raw

            self._cache_list_rows(rows)

            if normalized in self._cnpj_fundo_cache:
                return self._cnpj_fundo_cache[normalized]

            if not rows or len(rows) < size:
                break
            page += 1
            logger.debug("cnpj_to_codigo_fundo: varrendo página %d (CNPJ ainda não encontrado).", page)

        raise DataRetrievalError(
            f"CNPJ {cnpj} não encontrado na listagem de fundos ANBIMA"
            + (f" (tipo_fundo={tipo_fundo!r})" if tipo_fundo else "")
            + ". Verifique o CNPJ ou informe tipo_fundo= para restringir a busca."
        )

    def _get_first_classe_codigo(self, codigo_fundo: str) -> str:
        """
        Retorna o código da primeira classe (prefixo C) de um fundo.

        Faz cache por codigo_fundo na instância.
        """
        if codigo_fundo in self._fundo_classe_cache:
            return self._fundo_classe_cache[codigo_fundo]

        details = self.get_fundo_detalhes(codigo_fundo)
        classes = details.get("classes", [])
        if not classes:
            raise DataRetrievalError(
                f"Nenhuma classe encontrada para o fundo {codigo_fundo}."
            )
        classe_codigo = classes[0].get("codigo_classe") if isinstance(classes[0], dict) else None
        if not classe_codigo:
            raise DataRetrievalError(
                f"Não foi possível extrair o código de classe para o fundo {codigo_fundo}."
            )
        self._fundo_classe_cache[codigo_fundo] = classe_codigo
        return classe_codigo

    def _resolve_fundo_codigo(self, value: str, tipo_fundo: Optional[str] = None) -> str:
        """
        Resolve para um código de fundo/classe/subclasse.
        Se o valor for CNPJ, resolve via ``cnpj_to_codigo_fundo``.
        """
        if self._is_cnpj(value):
            return self.cnpj_to_codigo_fundo(value, tipo_fundo=tipo_fundo)
        return value

    def _resolve_classe_codigo(self, value: str, tipo_fundo: Optional[str] = None) -> str:
        """
        Resolve para um código de **classe ou subclasse** (prefixo C ou S).

        - C/S → retorna como está.
        - F (código de fundo) → retorna o código da primeira classe.
        - CNPJ de classe → código dessa classe.
        - CNPJ de fundo → resolve para F, depois para a primeira classe.
        """
        if self._is_cnpj(value):
            codigo_fundo = self.cnpj_to_codigo_fundo(value, tipo_fundo=tipo_fundo)
            codigo_classe = self._cnpj_classe_cache.get(self._normalize_cnpj(value))
            if codigo_classe:
                return codigo_classe
            return self._get_first_classe_codigo(codigo_fundo)
        if value.upper().startswith("F"):
            return self._get_first_classe_codigo(value)
        return value

    # ------------------------------------------------------------------ #
    # get_data (list/bulk endpoints)                                       #
    # ------------------------------------------------------------------ #

    def get_data(self, category: str, **kwargs) -> pd.DataFrame:
        self._ensure_credentials()
        self._log_processing(category)

        if category not in FUNDOS_ENDPOINTS:
            raise ValueError(
                f"Unknown category: {category}. "
                f"Supported: {list(FUNDOS_ENDPOINTS.keys())}"
            )

        path_suffix = FUNDOS_ENDPOINTS[category]
        path = f"{FUNDOS_BASE_PATH}{path_suffix}"

        params: Dict[str, Any] = {}
        if kwargs.get("page") is not None:
            params["page"] = kwargs["page"]
        if kwargs.get("size") is not None:
            params["size"] = kwargs["size"]
        if kwargs.get("tipo_fundo") is not None:
            params["tipo-fundo"] = kwargs["tipo_fundo"]
        if kwargs.get("data_atualizacao") is not None:
            params["data-atualizacao"] = kwargs["data_atualizacao"]
        if kwargs.get("cursor") is not None:
            params["cursor"] = kwargs["cursor"]

        raw = self._get(path, params=params or None)
        if isinstance(raw, dict):
            rows = raw.get("content", raw)
        else:
            rows = raw
        df = pd.DataFrame(rows if isinstance(rows, list) else [rows])
        return df

    # ------------------------------------------------------------------ #
    # Per-fund endpoints (aceitam código ANBIMA ou CNPJ)                  #
    # ------------------------------------------------------------------ #

    def get_fundo_detalhes(
        self, codigo_ou_cnpj: str, tipo_fundo: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retorna todos os dados cadastrais de um fundo/classe/subclasse específico.

        Args:
            codigo_ou_cnpj: Código ANBIMA (F…, C…, S…) **ou CNPJ** do fundo.
            tipo_fundo: Tipo do fundo (``"FIF"``, ``"FII"``, etc.) usado para
                restringir a busca por CNPJ e reduzir o número de páginas varridas.
        """
        codigo = self._resolve_fundo_codigo(codigo_ou_cnpj, tipo_fundo=tipo_fundo)
        return self._get(f"{FUNDOS_BASE_PATH}/{codigo}")

    def get_fundo_historico(
        self, codigo_ou_cnpj: str, tipo_fundo: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retorna o histórico completo de alterações cadastrais de um fundo.

        Args:
            codigo_ou_cnpj: Código ANBIMA do fundo (F…) **ou CNPJ**.
            tipo_fundo: Tipo do fundo usado para restringir a busca por CNPJ.
        """
        codigo = self._resolve_fundo_codigo(codigo_ou_cnpj, tipo_fundo=tipo_fundo)
        return self._get(f"{FUNDOS_BASE_PATH}/{codigo}/historico")

    def get_classificacao_historica(self, codigos_ou_cnpjs: List[str]) -> pd.DataFrame:
        """
        Histórico da classificação ANBIMA (``tipo_anbima``) de cada fundo.

        Lê ``historico_composicao_classe`` da classe correspondente: a própria
        classe para CNPJ de classe, a primeira classe para CNPJ ou código (F)
        de fundo. Faz no máximo uma varredura da listagem e uma chamada de
        histórico por fundo.

        Args:
            codigos_ou_cnpjs: CNPJs (fundo ou classe, com ou sem formatação)
                e/ou códigos de fundo (F…).

        Returns:
            DataFrame ``[identificador, data_vigencia, tipo_anbima]`` com o
            identificador como recebido. Os não encontrados na ANBIMA ficam de
            fora, com aviso no log.
        """
        ids = list(dict.fromkeys(codigos_ou_cnpjs))
        if any(
            self._is_cnpj(v) and self._normalize_cnpj(v) not in self._cnpj_fundo_cache
            for v in ids
        ):
            self.pre_load_cnpj_cache()

        historicos: Dict[str, Dict[str, Any]] = {}
        rows: List[Dict[str, Any]] = []
        missing: List[str] = []
        for k, ident in enumerate(ids, 1):
            codigo_classe: Optional[str] = None
            if self._is_cnpj(ident):
                key = self._normalize_cnpj(ident)
                codigo_fundo = self._cnpj_fundo_cache.get(key)
                codigo_classe = self._cnpj_classe_cache.get(key)
            else:
                codigo_fundo = ident
            if not codigo_fundo:
                missing.append(ident)
                continue

            if codigo_fundo not in historicos:
                try:
                    historicos[codigo_fundo] = self._get(
                        f"{FUNDOS_BASE_PATH}/{codigo_fundo}/historico"
                    )
                except AnbimaFeedNotFoundError:
                    historicos[codigo_fundo] = {}
            classes = historicos[codigo_fundo].get("classes") or []
            match = [c for c in classes if codigo_classe and c.get("codigo_classe") == codigo_classe]
            for cls in match or classes[:1]:
                for h in cls.get("historico_composicao_classe") or []:
                    if h.get("tipo_anbima"):
                        rows.append({
                            "identificador": ident,
                            "data_vigencia": h.get("data_vigencia"),
                            "tipo_anbima": h["tipo_anbima"],
                        })
            if k % 100 == 0 or k == len(ids):
                logger.info("get_classificacao_historica: [%d/%d] fundos", k, len(ids))

        if missing:
            logger.warning(
                "get_classificacao_historica: %d/%d identificadores não encontrados na ANBIMA: %s",
                len(missing), len(ids), missing[:10],
            )

        df = pd.DataFrame(rows, columns=["identificador", "data_vigencia", "tipo_anbima"])
        df["data_vigencia"] = pd.to_datetime(df["data_vigencia"], errors="coerce")
        return df.dropna(subset=["data_vigencia"]).drop_duplicates().reset_index(drop=True)

    _SERIE_HISTORICA_PAGE_SIZE = 1000  # limite hard da API por requisição

    def get_serie_historica(self, codigo_ou_cnpj: str, **kwargs) -> pd.DataFrame:
        """
        Retorna a série histórica de PL e cota de uma classe ou subclasse.

        A API devolve no máximo 1 000 registros por requisição. Este método faz
        paginação automática por intervalo de datas: quando uma página cheia é
        recebida, a próxima requisição começa no dia seguinte ao último
        ``data_competencia`` retornado, repetindo até o fim do período.

        Se um CNPJ ou código de fundo (F…) for passado, o código da primeira
        classe é resolvido automaticamente.

        Args:
            codigo_ou_cnpj: Código de classe/subclasse (C…/S…), código de fundo
                (F…) **ou CNPJ** do fundo.

        kwargs:
          data_inicio (str)      : data inicial no formato YYYY-MM-DD
          data_fim    (str)      : data final no formato YYYY-MM-DD
          data_atualizacao (str) : filtra por data de atualização
                                   (YYYY-MM-DDTHH:MM:SS.SSS)
          tipo_fundo (str)       : tipo do fundo (``"FIF"``, ``"FII"``, etc.)
                                   usado para restringir a busca por CNPJ
        """
        codigo = self._resolve_classe_codigo(
            codigo_ou_cnpj, tipo_fundo=kwargs.get("tipo_fundo")
        )
        data_inicio = kwargs.get("data_inicio")
        data_atualizacao = kwargs.get("data_atualizacao")

        # A API retorna em ordem decrescente (mais recente primeiro).
        # Paginamos recuando data_fim para o dia anterior ao mínimo de cada página.
        current_fim: Optional[str] = kwargs.get("data_fim")
        pages: List[pd.DataFrame] = []

        while True:
            params: Dict[str, Any] = {}
            if data_inicio:
                params["data-inicio"] = data_inicio
            if current_fim:
                params["data-fim"] = current_fim
            if data_atualizacao:
                params["data-atualizacao"] = data_atualizacao

            raw = self._get(
                f"{FUNDOS_BASE_PATH}/{codigo}/serie-historica",
                params=params or None,
            )
            rows = raw.get("content", raw) if isinstance(raw, dict) else raw
            df_page = pd.DataFrame(rows if isinstance(rows, list) else [rows])

            if df_page.empty:
                break

            pages.append(df_page)

            # Se recebemos menos que o limite, não há mais páginas
            if len(df_page) < self._SERIE_HISTORICA_PAGE_SIZE:
                break

            # Identifica a coluna de data para recuar a janela
            date_col = next(
                (c for c in ("data_competencia", "data_referencia") if c in df_page.columns),
                None,
            )
            if date_col is None:
                logger.warning(
                    "get_serie_historica: coluna de data não encontrada na resposta; "
                    "paginação automática interrompida após %d registros.",
                    sum(len(f) for f in pages),
                )
                break

            min_date = pd.to_datetime(df_page[date_col], errors="coerce").min()
            if pd.isna(min_date):
                break

            # Próxima janela: termina no dia anterior ao mínimo desta página
            next_fim = (min_date - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

            # Guarda de segurança: para se já chegamos antes do data_inicio
            if data_inicio and next_fim < data_inicio:
                break
            # Evita loop infinito se a data não recuar
            if current_fim and next_fim >= current_fim:
                break

            logger.debug(
                "get_serie_historica: página com %d registros (até %s); "
                "continuando até %s.",
                len(df_page),
                min_date.strftime("%Y-%m-%d"),
                next_fim,
            )
            current_fim = next_fim

        if not pages:
            return pd.DataFrame()

        result = pd.concat(pages, ignore_index=True).drop_duplicates()

        # Insere coluna cnpj_fundo
        cnpj_fundo: Optional[str] = None
        if self._is_cnpj(codigo_ou_cnpj):
            cnpj_fundo = codigo_ou_cnpj
        else:
            # Tenta resolver via cache reverso usando o codigo_fundo da resposta
            codigo_fundo_resp = (
                result["codigo_fundo"].iloc[0]
                if "codigo_fundo" in result.columns and not result.empty
                else None
            )
            if codigo_fundo_resp and codigo_fundo_resp in self._fundo_cnpj_cache:
                cnpj_fundo = self._fundo_cnpj_cache[codigo_fundo_resp]
            elif codigo_fundo_resp:
                # Busca nos detalhes do fundo (uma chamada extra, resultado é cacheado)
                try:
                    details = self.get_fundo_detalhes(codigo_fundo_resp)
                    cnpj_fundo = details.get("identificador_fundo")
                    if cnpj_fundo:
                        self._fundo_cnpj_cache[codigo_fundo_resp] = cnpj_fundo
                except DataRetrievalError:
                    pass

        if cnpj_fundo is not None:
            result.insert(0, "cnpj_fundo", cnpj_fundo)

        return result

    def pre_load_cnpj_cache(self, tipo_fundo: Optional[str] = None) -> int:
        """
        Pré-carrega o cache de CNPJ (de fundos e de classes) varrendo **todas**
        as páginas de ``anbima_fundos_lista`` em uma única passagem.

        Útil antes de chamar :meth:`get_series_historicas` com muitos CNPJs:
        elimina o custo de varredura incremental por fundo e torna a resolução
        de todos os CNPJs subsequentes instantânea (hit de cache).

        Args:
            tipo_fundo: Filtra a listagem por tipo de fundo (``"FIF"``,
                ``"FII"``, ``"FIP"``, etc.). Reduz o número de páginas quando
                todos os fundos de interesse são do mesmo tipo.

        Returns:
            Número de CNPJs adicionados ao cache nesta chamada.
        """
        page = 0
        size = 1000
        added = 0
        list_params: Dict[str, Any] = {"size": size}
        if tipo_fundo:
            list_params["tipo-fundo"] = tipo_fundo

        logger.info(
            "pre_load_cnpj_cache: iniciando varredura completa da lista de fundos ANBIMA%s…",
            f" (tipo_fundo={tipo_fundo!r})" if tipo_fundo else "",
        )
        t0 = time.time()
        while True:
            list_params["page"] = page
            try:
                raw = self._get(FUNDOS_BASE_PATH, params=list_params)
            except DataRetrievalError as e:
                if "404" in str(e):
                    logger.debug(
                        "pre_load_cnpj_cache: página %d retornou 404 — fim da listagem.", page
                    )
                    break
                raise
            rows: List[Dict] = []
            if isinstance(raw, dict):
                rows = raw.get("content", raw) if isinstance(raw.get("content"), list) else []
            elif isinstance(raw, list):
                rows = raw

            added += self._cache_list_rows(rows)

            logger.debug(
                "pre_load_cnpj_cache: página %d concluída — %d fundos lidos nesta página.",
                page, len(rows),
            )

            if not rows or len(rows) < size:
                break
            page += 1

        elapsed = time.time() - t0
        logger.info(
            "pre_load_cnpj_cache: concluído em %.1fs — "
            "%d páginas varridas, %d fundos em cache (%d novos).",
            elapsed, page + 1, len(self._cnpj_fundo_cache), added,
        )
        return added

    def get_series_historicas(
        self,
        codigos_ou_cnpjs: List[str],
        **kwargs,
    ) -> pd.DataFrame:
        """
        Retorna a série histórica de PL e cota para **múltiplos fundos** em um
        único DataFrame, com paginação automática por fundo.

        Quando a lista contém mais de um CNPJ ainda não resolvido, o método
        executa :meth:`pre_load_cnpj_cache` automaticamente antes das consultas
        individuais. Isso substitui N varreduras sequenciais da listagem por uma
        única passagem, reduzindo drasticamente o tempo total.

        Args:
            codigos_ou_cnpjs: Lista de CNPJs (formatados ou 14 dígitos) e/ou
                códigos ANBIMA (F…, C…, S…).

        kwargs: Mesmos parâmetros de :meth:`get_serie_historica`:
          data_inicio (str)      : data inicial no formato YYYY-MM-DD
          data_fim    (str)      : data final no formato YYYY-MM-DD
          data_atualizacao (str) : filtra por data de atualização
          tipo_fundo (str)       : tipo do fundo para restringir busca por CNPJ

        Returns:
            DataFrame concatenado com coluna ``cnpj_fundo`` e todos os registros
            de todos os fundos solicitados.
        """
        tipo_fundo = kwargs.get("tipo_fundo")
        t_total = time.time()

        # Identifica CNPJs ainda não resolvidos
        unknown_cnpjs = [
            v for v in codigos_ou_cnpjs
            if self._is_cnpj(v) and self._normalize_cnpj(v) not in self._cnpj_fundo_cache
        ]
        if len(unknown_cnpjs) > 1:
            logger.info(
                "get_series_historicas: %d/%d CNPJs não estão em cache — "
                "executando pre_load_cnpj_cache para resolver todos de uma vez.",
                len(unknown_cnpjs), len(codigos_ou_cnpjs),
            )
            self.pre_load_cnpj_cache(tipo_fundo=tipo_fundo)
        elif unknown_cnpjs:
            logger.info(
                "get_series_historicas: 1 CNPJ não está em cache (%s) — "
                "será resolvido sob demanda.",
                unknown_cnpjs[0],
            )
        else:
            logger.info(
                "get_series_historicas: todos os %d identificadores já estão em cache.",
                len(codigos_ou_cnpjs),
            )

        frames: List[pd.DataFrame] = []
        total = len(codigos_ou_cnpjs)
        for i, codigo_ou_cnpj in enumerate(codigos_ou_cnpjs, 1):
            t_fund = time.time()
            logger.info(
                "get_series_historicas: [%d/%d] iniciando – %s",
                i, total, codigo_ou_cnpj,
            )
            try:
                df = self.get_serie_historica(codigo_ou_cnpj, **kwargs)
                elapsed_fund = time.time() - t_fund
                if not df.empty:
                    frames.append(df)
                    logger.info(
                        "get_series_historicas: [%d/%d] concluído em %.1fs — "
                        "%d registros retornados.",
                        i, total, elapsed_fund, len(df),
                    )
                else:
                    logger.warning(
                        "get_series_historicas: [%d/%d] sem dados para %s (%.1fs).",
                        i, total, codigo_ou_cnpj, elapsed_fund,
                    )
            except DataRetrievalError as exc:
                elapsed_fund = time.time() - t_fund
                logger.error(
                    "get_series_historicas: [%d/%d] erro em %s após %.1fs – %s",
                    i, total, codigo_ou_cnpj, elapsed_fund, exc,
                )

        if not frames:
            return pd.DataFrame()

        result = pd.concat(frames, ignore_index=True)
        logger.info(
            "get_series_historicas: finalizado em %.1fs — "
            "%d fundos, %d registros no total.",
            time.time() - t_total, len(frames), len(result),
        )
        return result

    def get_instituicao_detalhes(self, cnpj: str) -> Dict[str, Any]:
        """Retorna os detalhes de uma instituição pelo CNPJ."""
        return self._get(f"{FUNDOS_BASE_PATH}/instituicoes/{cnpj}")

    def get_segmento_investidor(self, codigo_ou_cnpj: str, **kwargs) -> Dict[str, Any]:
        """
        Retorna a distribuição percentual de PL por segmento do investidor.

        Args:
            codigo_ou_cnpj: Código de classe/subclasse (C…/S…), código de fundo (F…)
                **ou CNPJ** do fundo.

        kwargs:
          mes_referencia (int): mês de referência (1-12)
          ano_referencia (int): ano de referência
          tipo_fundo (str)    : tipo do fundo usado para restringir a busca por CNPJ
        """
        codigo = self._resolve_classe_codigo(
            codigo_ou_cnpj, tipo_fundo=kwargs.get("tipo_fundo")
        )
        params: Dict[str, Any] = {}
        if kwargs.get("mes_referencia") is not None:
            params["mes-referencia"] = kwargs["mes_referencia"]
        if kwargs.get("ano_referencia") is not None:
            params["ano-referencia"] = kwargs["ano_referencia"]

        return self._get(
            f"{FUNDOS_BASE_PATH}/segmento-investidor/{codigo}/patrimonio-liquido",
            params=params or None,
        )

    def get_notas_explicativas(self, codigo_ou_cnpj: str, **kwargs) -> pd.DataFrame:
        """
        Retorna as notas explicativas de uma classe ou subclasse.

        Args:
            codigo_ou_cnpj: Código de classe/subclasse (C…/S…), código de fundo (F…)
                **ou CNPJ** do fundo.

        kwargs:
          page (int)       : página (default 0)
          size (int)       : registros por página (default 1000)
          tipo_fundo (str) : tipo do fundo usado para restringir a busca por CNPJ
        """
        codigo = self._resolve_classe_codigo(
            codigo_ou_cnpj, tipo_fundo=kwargs.get("tipo_fundo")
        )
        params: Dict[str, Any] = {}
        if kwargs.get("page") is not None:
            params["page"] = kwargs["page"]
        if kwargs.get("size") is not None:
            params["size"] = kwargs["size"]

        raw = self._get(
            f"{FUNDOS_BASE_PATH}/{codigo}/notas-explicativas",
            params=params or None,
        )
        rows = raw.get("content", raw) if isinstance(raw, dict) else raw
        return pd.DataFrame(rows if isinstance(rows, list) else [rows])
