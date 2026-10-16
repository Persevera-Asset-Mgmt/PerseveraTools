from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import quote, urlencode

import requests
import pandas as pd

from .base import DataProvider, DataRetrievalError
from ...config import settings
from ...utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_MAKE_API_URL = "https://us1.make.com/api/v2"
# "XP - Data Access - 01. Access Token": on-demand, output `access_token`.
_DEFAULT_TOKEN_SCENARIO_ID = 4877209
# "XP - Data Access - 03. Proxy GET": inputs `path`, `access_token`; outputs `status_code`, `body`.
_DEFAULT_PROXY_SCENARIO_ID = 4980782
_DEFAULT_TIMEOUT = 120
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_BACKOFF = 2.0
_RETRY_STATUS_CODES = (429, 500, 502, 503, 504)
_TOKEN_DEFAULT_EXPIRES_SECONDS = 3599
_TOKEN_REFRESH_MARGIN_SECONDS = 120
_DEFAULT_PAGE_SIZE = 500


def _jwt_expiry_epoch(token: str) -> Optional[float]:
    """Lê o claim `exp` do JWT sem validar assinatura (só para saber quando renovar)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError, TypeError):
        return None


class XPWSProvider(DataProvider):
    """
    Provider for XP Data Access (APIs B2B para escritórios parceiros), routed through Make.

    The Data Access requires, on every call, a Bearer token, the office's client certificate (mTLS)
    and the ``Ocp-Apim-Subscription-Key`` header. The certificate and the subscription key live only
    in Make; Python never handles them:

      - token: scenario "XP - Data Access - 01. Access Token" (default id 4877209), output ``access_token``;
      - data: scenario "XP - Data Access - 03. Proxy GET" (default id 4980782), inputs ``path`` (path + query string,
        relative to https://matls-api.xpi.com.br/data-access/api) and ``access_token``;
        outputs ``status_code`` and ``body`` (raw JSON text).

    Both run synchronously through the Make API (POST /scenarios/{id}/run, ``responsive: true``).
    Make does not run the same scenario twice at once, so calls are sequential.

    Environment variables (usually in ~/.persevera/.env):
      - PERSEVERA_MAKE_API_TOKEN (required; scope ``scenarios:run``)
      - PERSEVERA_MAKE_API_URL (default https://us1.make.com/api/v2)
      - PERSEVERA_XPWS_MAKE_PROXY_SCENARIO_ID (default 4980782)
      - PERSEVERA_XPWS_MAKE_TOKEN_SCENARIO_ID (default 4877209)

    Supported categories:
      - raw: any GET endpoint, ``path`` like "/v1/account"
      - account: Dados da Conta, GET /v1/account (OData, validity history per account)
      - customers: Contas habilitadas, GET /v2/customers
      - consolidated_position_d0: Posição consolidada D0,
        GET /v1/consolidated-positions/customer/{customer_code}

    ``params`` go to the query string (OData options such as $filter, $select, $top). Collection
    endpoints answer ``{"data": [...], "count": N}`` with ``$count=true``; with ``paginate=True``
    the provider pages with $top/$skip until ``count`` rows are read (one Make run per page).
    """

    def __init__(
        self,
        start_date: str = "1980-01-01",
        *,
        make_api_token: Optional[str] = None,
        make_api_url: Optional[str] = None,
        proxy_scenario_id: Optional[Union[int, str]] = None,
        token_scenario_id: Optional[Union[int, str]] = None,
        access_token: Optional[str] = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
        timeout_seconds: int = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_backoff: float = _DEFAULT_RETRY_BACKOFF,
    ):
        super().__init__(start_date=start_date)

        self.make_api_token = make_api_token or getattr(settings, "MAKE_API_TOKEN", None)
        self.make_api_url = (make_api_url or getattr(settings, "MAKE_API_URL", None) or _DEFAULT_MAKE_API_URL).rstrip("/")
        self.proxy_scenario_id = int(
            proxy_scenario_id or getattr(settings, "XPWS_MAKE_PROXY_SCENARIO_ID", None) or _DEFAULT_PROXY_SCENARIO_ID
        )
        self.token_scenario_id = int(
            token_scenario_id or getattr(settings, "XPWS_MAKE_TOKEN_SCENARIO_ID", None) or _DEFAULT_TOKEN_SCENARIO_ID
        )

        self.page_size = int(page_size)
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, int(max_retries))
        self.retry_backoff = float(retry_backoff)

        self._session = requests.Session()
        self._static_token = access_token
        self._access_token: Optional[str] = access_token
        self._access_token_expiry_epoch: float = float("inf") if access_token else 0.0

        self._validate_credentials()

    # -------------------------
    # Public API
    # -------------------------
    def get_data(self, category: str, **kwargs) -> pd.DataFrame:
        """
        Retrieve data from XP Data Access.

          provider.get_data("raw", path="/v1/account", params={"$filter": "accountCode eq 123"})
          provider.get_data("account", params={"$filter": "currentRegisterIndicator eq 1"})
          provider.get_data("customers")
          provider.get_data("consolidated_position_d0", customer_code=64280)
        """
        self._log_processing(category)
        params = kwargs.get("params")
        paginate = kwargs.get("paginate", True)

        if category == "raw":
            if "path" not in kwargs:
                raise DataRetrievalError("raw requires 'path' (e.g. '/v1/account').")
            return self._to_dataframe(self._get_collection(kwargs["path"], params, paginate=paginate))
        if category == "account":
            return self.get_accounts(params=params, paginate=paginate)
        if category == "customers":
            return self.get_customers(params=params)
        if category == "consolidated_position_d0":
            cust = kwargs.get("customer_code") or kwargs.get("customerCode")
            if not cust:
                raise DataRetrievalError("consolidated_position_d0 requires 'customer_code'.")
            return self.get_consolidated_position_d0(cust, params=params)

        raise DataRetrievalError(f"Unsupported category '{category}' for XPWSProvider.")

    def get_accounts(self, params: Optional[Dict[str, Any]] = None, *, paginate: bool = True) -> pd.DataFrame:
        """Dados da Conta (D-1): cadastro e patrimônio declarado por conta, com vigência
        (startValidityDate/endValidityDate; currentRegisterIndicator=1 marca o registro atual)."""
        return self._to_dataframe(self._get_collection("/v1/account", params, paginate=paginate))

    def get_customers(self, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """Contas habilitadas no Data Access (customerCode, status, signatureDate, cancellationDate)."""
        return self._to_dataframe(self._get_collection("/v2/customers", params, paginate=False))

    def get_consolidated_position_d0(
        self, customer_code: Union[str, int], params: Optional[Dict[str, Any]] = None
    ) -> pd.DataFrame:
        """Posição consolidada D0 de um cliente."""
        return self._to_dataframe(self.get_json(f"/v1/consolidated-positions/customer/{customer_code}", params))

    def get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Resposta JSON crua de um endpoint (sem paginação nem conversão)."""
        return self._get(path, params)

    def get_access_token(self) -> str:
        """Token Bearer atual (renova pelo cenário Make se expirado)."""
        self._ensure_token()
        return self._access_token  # type: ignore[return-value]

    # -------------------------
    # Internals: Make
    # -------------------------
    def _validate_credentials(self) -> None:
        missing = []
        if not self.make_api_token:
            missing.append("PERSEVERA_MAKE_API_TOKEN")
        if missing:
            raise ValueError("Missing XPWS configuration variables: " + ", ".join(missing))

    def _run_scenario(self, scenario_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """Roda um cenário Make de forma síncrona e devolve `outputs`. Retry em 429/5xx do Make."""
        url = f"{self.make_api_url}/scenarios/{scenario_id}/run"
        attempt = 0
        while True:
            try:
                resp = self._session.post(
                    url,
                    headers={"Authorization": f"Token {self.make_api_token}", "Content-Type": "application/json"},
                    json={"responsive": True, "data": data},
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                raise DataRetrievalError(f"XPWS: erro ao chamar o Make (cenário {scenario_id}): {exc}") from exc

            if resp.status_code in _RETRY_STATUS_CODES and attempt < self.max_retries - 1:
                wait = self.retry_backoff * (2 ** attempt)
                logger.warning("Make HTTP %s no cenário %s; nova tentativa em %.0fs.", resp.status_code, scenario_id, wait)
                time.sleep(wait)
                attempt += 1
                continue

            if resp.status_code >= 400:
                raise DataRetrievalError(
                    f"XPWS: Make recusou rodar o cenário {scenario_id} (HTTP {resp.status_code}): {(resp.text or '')[:500]}"
                )
            try:
                payload = resp.json()
            except ValueError as exc:
                raise DataRetrievalError(f"XPWS: resposta não-JSON do Make: {resp.text[:200]!r}") from exc

            if isinstance(payload, dict) and "outputs" not in payload and isinstance(payload.get("result"), dict):
                payload = payload["result"]
            outputs = payload.get("outputs") if isinstance(payload, dict) else None
            if not isinstance(outputs, dict):
                raise DataRetrievalError(
                    f"XPWS: cenário {scenario_id} terminou sem outputs (executionId={(payload or {}).get('executionId')}). "
                    "Veja o histórico de execuções no Make."
                )
            return outputs

    def _ensure_token(self) -> None:
        if self._access_token and time.time() < self._access_token_expiry_epoch - _TOKEN_REFRESH_MARGIN_SECONDS:
            return
        if self._static_token:
            return
        logger.info("Requesting XPWS access token from Make scenario %s", self.token_scenario_id)
        token = self._run_scenario(self.token_scenario_id, {}).get("access_token")
        if not token:
            raise DataRetrievalError(f"XPWS: cenário Make {self.token_scenario_id} não devolveu access_token.")
        self._access_token = token
        self._access_token_expiry_epoch = _jwt_expiry_epoch(token) or (time.time() + _TOKEN_DEFAULT_EXPIRES_SECONDS)
        logger.info("XPWS access token acquired (expires in %ds).", int(self._access_token_expiry_epoch - time.time()))

    # -------------------------
    # Internals: Data Access via proxy
    # -------------------------
    @staticmethod
    def _build_path(path: str, params: Optional[Dict[str, Any]]) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        if not params:
            return path
        # Mantém $ e , legíveis para OData; espaços viram %20 (não +).
        query = urlencode(params, doseq=True, quote_via=quote, safe="$,'")
        return f"{path}{'&' if '?' in path else '?'}{query}"

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET via cenário proxy, com retry em 429/5xx da XP e um re-auth em 401. Corpo vazio → None."""
        full_path = self._build_path(path, params)
        logger.info("XPWS Request: GET %s", full_path)

        reauthenticated = False
        attempt = 0
        while True:
            self._ensure_token()
            outputs = self._run_scenario(
                self.proxy_scenario_id, {"path": full_path, "access_token": self._access_token}
            )
            try:
                status = int(outputs.get("status_code"))
            except (TypeError, ValueError):
                raise DataRetrievalError(f"XPWS: proxy sem status_code válido (outputs={list(outputs)}).")
            body = outputs.get("body")
            if not isinstance(body, str):
                body = "" if body is None else json.dumps(body)

            if status == 401 and not reauthenticated and not self._static_token:
                logger.info("XPWS: 401, renovando token e repetindo a chamada.")
                self._access_token = None
                reauthenticated = True
                continue

            if status in _RETRY_STATUS_CODES and attempt < self.max_retries - 1:
                wait = self.retry_backoff * (2 ** attempt)
                logger.warning("XPWS HTTP %s em %s (tentativa %d/%d); aguardando %.0fs.",
                               status, full_path, attempt + 1, self.max_retries, wait)
                time.sleep(wait)
                attempt += 1
                continue

            if status >= 400:
                detail = f"body: {body.strip()[:500]}" if body.strip() else "corpo vazio"
                raise DataRetrievalError(f"XPWS request failed [GET {full_path}] HTTP {status} ({detail})")

            if status == 204 or not body.strip():
                return None
            try:
                data = json.loads(body)
                # O Make devolve o corpo como texto JSON-encodado (string contendo o JSON da XP).
                while isinstance(data, str) and data.strip()[:1] in ("[", "{"):
                    data = json.loads(data)
                return data
            except ValueError as exc:
                raise DataRetrievalError(f"XPWS returned non-JSON body [GET {full_path}]: {body[:200]!r}") from exc

    def _get_collection(self, path: str, params: Optional[Dict[str, Any]], *, paginate: bool) -> Any:
        """Pagina com $top/$skip enquanto a resposta `{data, count}` tiver menos linhas que `count`."""
        query: Dict[str, Any] = dict(params or {})
        if paginate:
            query.setdefault("$count", "true")
            query.setdefault("$top", self.page_size)
        payload = self._get(path, query)
        if not (paginate and isinstance(payload, dict) and isinstance(payload.get("data"), list)):
            return payload
        count = payload.get("count")
        rows: List[Any] = list(payload["data"])
        if not isinstance(count, int):
            return payload

        skip0 = int(query.get("$skip", 0) or 0)
        while len(rows) < count:
            page = self._get(path, {**query, "$skip": skip0 + len(rows)})
            page_rows = page.get("data") if isinstance(page, dict) else None
            if not page_rows:
                break
            if page_rows[0] == rows[0]:
                raise DataRetrievalError(
                    f"XPWS: {path} ignorou $skip (mesma primeira linha); paginação interrompida em {len(rows)}/{count}."
                )
            rows.extend(page_rows)
            logger.info("XPWS %s: %d/%d linhas", path, len(rows), count)
        return {"data": rows, "count": count}

    @staticmethod
    def _to_dataframe(payload: Any) -> pd.DataFrame:
        """Lista → linhas; dict com data/items/results/value em lista → essas linhas; dict solto → 1 linha."""
        if payload is None:
            return pd.DataFrame()
        if isinstance(payload, list):
            return pd.json_normalize(payload)
        if isinstance(payload, dict):
            for key in ("data", "items", "results", "content", "value"):
                if isinstance(payload.get(key), list):
                    return pd.json_normalize(payload[key])
            return pd.json_normalize(payload)
        return pd.DataFrame()
