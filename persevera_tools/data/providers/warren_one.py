"""
WarrenOneProvider — provider (somente leitura) para a API One da Warren (consolidação de carteiras).

Autenticação: POST {auth_base}/user/integration/authorization com {clientId, secretKey}
→ idToken (JWT, ~3600s), enviado como Bearer. Todas as chamadas exigem também o header
x-tenant-id (sem ele a API responde 500 com corpo vazio).

Required environment variables (in ~/.persevera/.env):
  - PERSEVERA_WARRENONE_CLIENT_ID
  - PERSEVERA_WARRENONE_SECRET_KEY
  - PERSEVERA_WARRENONE_TENANT_ID

Optional environment variables:
  - PERSEVERA_WARRENONE_ENV            (default: prd — prd | stg; escolhe os hosts abaixo)
  - PERSEVERA_WARRENONE_AUTH_BASE_URL  (default: https://api-metis.meuportfol.io)
  - PERSEVERA_WARRENONE_API_BASE_URL   (default: https://api.prd.consolidacao.warren.com.br/one)
  - PERSEVERA_WARRENONE_TIMEOUT        (default: 60)
  - PERSEVERA_WARRENONE_VERIFY_SSL     (default: true)
  - PERSEVERA_WARRENONE_REQUEST_DELAY  (default: 0.3 — pausa entre carteiras em bulk)
  - PERSEVERA_WARRENONE_MAX_RETRIES    (default: 4)
  - PERSEVERA_WARRENONE_RETRY_BACKOFF  (default: 2.0 — base do backoff exponencial)

Comportamentos da API tratados aqui (observados em homologação, 2026-10):
  - ``includeHistoricalReturns=true`` ("incluir cota histórica") zera a rentabilidade de
    carteiras sem cota histórica importada → nunca é enviado.
  - Janelas maiores que o histórico da carteira vêm como 0 (returns v2, attribution por
    instituição) ou como a série desde o início (accumulated-returns, attribution por classe).
    Com ``mask_short_history=True`` (default) essas janelas viram NaN.
  - ``/v1/.../accumulated-returns/{initialDate}/{finalDate}`` responde 500 → intervalos
    arbitrários são obtidos rebaseando a série v2 (ver ``get_accumulated_returns(start=...)``).
  - O significado de ``periodType`` (EPeriodType 0–9) não está documentado; o mapeamento em
    ``_WINDOW_TO_PERIOD_TYPE`` foi INFERIDO comparando respostas (0 e 9 dão 500).

Nenhum endpoint de escrita é exposto. Em particular, ``POST /v1/portfolios/{id}/returns/...``
dispara o recálculo de retornos no One e não deve ser chamado por aqui.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

import numpy as np
import pandas as pd
import requests

from .base import DataProvider, DataRetrievalError
from ...config import settings
from ...utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_ENV_BASE_URLS = {
    "prd": ("https://api-metis.meuportfol.io", "https://api.prd.consolidacao.warren.com.br/one"),
    "stg": ("https://api-metis-staging.meuportfol.io", "https://api.stg.consolidacao.warren.com.br/one"),
}

_AUTH_PATH = "/user/integration/authorization"
_TOKEN_DEFAULT_EXPIRES_SECONDS = 3600
_TOKEN_REFRESH_MARGIN_SECONDS = 60

_RETRY_STATUS_CODES = frozenset({429, 502, 503, 504})
_DEFAULT_TIMEOUT = 60
_DEFAULT_REQUEST_DELAY = 0.3
_DEFAULT_MAX_RETRIES = 4
_DEFAULT_RETRY_BACKOFF = 2.0

# Janelas padronizadas → campo nas respostas de returns v2.
_RETURN_WINDOW_FIELDS = {
    "month": "monthly",
    "ytd": "annual",
    "inception": "inception",
    "3m": "past03Months",
    "6m": "past06Months",
    "12m": "past12Months",
    "24m": "past24Months",
    "36m": "past36Months",
}

# Janelas → campo na atribuição de performance por classe (sem periodType).
_ATTRIBUTION_WINDOW_FIELDS = {
    "month": "monthly",
    "ytd": "annual",
    "inception": "inception",
    "6m": "past06Months",
    "12m": "past12Months",
    "24m": "past24Months",
}

# INFERIDO (não documentado): EPeriodType usado nas rotas .../performance-attributions/{periodType}.
_WINDOW_TO_PERIOD_TYPE = {
    "month": 1,
    "ytd": 2,
    "inception": 3,
    "3m": 4,
    "6m": 5,
    "12m": 6,
    "24m": 7,
    "36m": 8,
}

# Períodos aceitos em /v2/.../accumulated-returns/{date}/{period}.
_ACCUMULATED_PERIODS = ("1m", "3m", "6m", "12m", "24m", "36m")

# Janelas de N meses móveis (sujeitas à máscara de histórico curto).
_TRAILING_WINDOW_MONTHS = {"1m": 1, "3m": 3, "6m": 6, "12m": 12, "24m": 24, "36m": 36}

_ATTRIBUTION_DIMENSIONS = {
    "asset_class": "asset-classes",
    "institution": "institutions",
    "vehicle": "vehicles",
}

_MONTH_FIELDS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)

# Carteiras auxiliares (só fluxos: /RENDIMENTOS, /AMORTIZACOES-..., /SWAPs, /TD, /AUXILIAR...).
_AUXILIARY_MARKER = "/"

_PORTFOLIO_COLUMNS = {
    "id": "portfolio_id",
    "name": "name",
    "portfolioType": "portfolio_type",
    "currencyName": "currency",
    "isActive": "is_active",
    "initialDate": "initial_date",
    "lastPositionDate": "last_position_date",
    "lastValidProcessedDate": "last_valid_processed_date",
    "totalNetValue": "total_net_value",
    "inflowValue": "inflow_value",
    "outflowValue": "outflow_value",
}

BenchmarkSpec = Union[int, str]


class WarrenOneProvider(DataProvider):
    """
    Provider (somente leitura) para a API One da Warren: carteiras, rentabilidade,
    séries acumuladas e atribuição de performance.

    Retornos são sempre decimais (0.0123 = 1,23%). Datas aceitam 'YYYY-MM-DD' ou Timestamp.
    Benchmarks podem ser passados por id (1) ou nome exato do índice ('CDI', 'IBOVESPA');
    os ids vêm de ``get_indexes()``.

    Supported categories in get_data():
      - raw                         : GET livre → path, [params]
      - portfolios                  : [active_only=True], [search]
      - class_allocations           : [active_only=True]
      - indexes                     : (none)
      - returns                     : portfolio_id, date, [benchmarks], [compare_to]
      - all_returns                 : date, [benchmarks], [compare_to], [portfolio_type],
                                      [exclude_auxiliary=True], [use_last_position=False], [on_error]
      - monthly_returns             : portfolio_id, date, [benchmarks]
      - accumulated_returns         : portfolio_id, date, [period | start], [benchmarks]
      - performance_attribution     : portfolio_id, date
      - performance_attribution_detail : portfolio_id, date, window, [by]

    Examples:
        >>> one = WarrenOneProvider()
        >>> carteiras = one.get_portfolios()
        >>> ret = one.get_returns(pid, "2026-06-30", benchmarks=["CDI", "IBOVESPA"], compare_to="CDI")
        >>> serie = one.get_accumulated_returns(pid, "2026-06-30", start="2026-03-31", benchmarks=["CDI"])
        >>> contrib = one.get_performance_attribution(pid, "2026-06-30")
        >>> pnl = one.get_performance_attribution_detail(pid, "2026-06-30", window="month")
    """

    def __init__(
        self,
        start_date: str = "1980-01-01",
        *,
        client_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
        env: Optional[str] = None,
        auth_base_url: Optional[str] = None,
        api_base_url: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        verify_ssl: Optional[bool] = None,
        request_delay: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_backoff: Optional[float] = None,
        mask_short_history: bool = True,
    ):
        super().__init__(start_date=start_date)

        self.client_id = client_id or getattr(settings, "WARRENONE_CLIENT_ID", None)
        self.secret_key = secret_key or getattr(settings, "WARRENONE_SECRET_KEY", None)
        self.tenant_id = tenant_id or getattr(settings, "WARRENONE_TENANT_ID", None)

        self.env = (env or getattr(settings, "WARRENONE_ENV", None) or "prd").lower()
        if self.env not in _ENV_BASE_URLS:
            raise ValueError(f"Invalid Warren One env '{self.env}'. Valid: {', '.join(_ENV_BASE_URLS)}.")
        default_auth, default_api = _ENV_BASE_URLS[self.env]

        self.auth_base_url = (
            auth_base_url or getattr(settings, "WARRENONE_AUTH_BASE_URL", None) or default_auth
        ).rstrip("/")
        self.api_base_url = (
            api_base_url or getattr(settings, "WARRENONE_API_BASE_URL", None) or default_api
        ).rstrip("/")

        self.timeout_seconds = int(
            timeout_seconds
            if timeout_seconds is not None
            else getattr(settings, "WARRENONE_TIMEOUT", None) or _DEFAULT_TIMEOUT
        )

        if verify_ssl is None:
            verify_env = getattr(settings, "WARRENONE_VERIFY_SSL", None) or "true"
            self.verify_ssl = str(verify_env).lower() in ("1", "true", "yes", "y")
        else:
            self.verify_ssl = verify_ssl

        self.request_delay = float(
            request_delay
            if request_delay is not None
            else getattr(settings, "WARRENONE_REQUEST_DELAY", None) or _DEFAULT_REQUEST_DELAY
        )
        self.max_retries = int(
            max_retries
            if max_retries is not None
            else getattr(settings, "WARRENONE_MAX_RETRIES", None) or _DEFAULT_MAX_RETRIES
        )
        self.retry_backoff = float(
            retry_backoff
            if retry_backoff is not None
            else getattr(settings, "WARRENONE_RETRY_BACKOFF", None) or _DEFAULT_RETRY_BACKOFF
        )

        self.mask_short_history = mask_short_history

        self._session = requests.Session()
        self._id_token: Optional[str] = None
        self._id_token_expiry_epoch: float = 0.0
        self._initial_dates: Dict[str, pd.Timestamp] = {}
        self._indexes: Optional[pd.DataFrame] = None

        self._validate_credentials()

    # ------------------------------------------------------------------
    # Public DataProvider interface
    # ------------------------------------------------------------------

    def get_data(self, category: str, **kwargs) -> pd.DataFrame:
        """Route to the appropriate handler based on category (see class docstring)."""
        self._log_processing(category)

        if category == "raw":
            payload = self.get_raw(self._require_kwarg(kwargs, "path", category), params=kwargs.get("params"))
            return self._to_dataframe(payload)

        if category == "portfolios":
            return self.get_portfolios(active_only=kwargs.get("active_only", True), search=kwargs.get("search"))

        if category == "class_allocations":
            return self.get_class_allocations(active_only=kwargs.get("active_only", True))

        if category == "indexes":
            return self.get_indexes()

        if category == "returns":
            return self.get_returns(
                self._require_kwarg(kwargs, "portfolio_id", category),
                self._require_kwarg(kwargs, "date", category),
                benchmarks=kwargs.get("benchmarks"),
                compare_to=kwargs.get("compare_to"),
            )

        if category == "all_returns":
            return self.get_all_returns(
                self._require_kwarg(kwargs, "date", category),
                benchmarks=kwargs.get("benchmarks"),
                compare_to=kwargs.get("compare_to"),
                portfolio_type=kwargs.get("portfolio_type"),
                active_only=kwargs.get("active_only", True),
                exclude_auxiliary=kwargs.get("exclude_auxiliary", True),
                use_last_position=kwargs.get("use_last_position", False),
                on_error=kwargs.get("on_error", "raise"),
            )

        if category == "monthly_returns":
            return self.get_monthly_returns(
                self._require_kwarg(kwargs, "portfolio_id", category),
                self._require_kwarg(kwargs, "date", category),
                benchmarks=kwargs.get("benchmarks"),
            )

        if category == "accumulated_returns":
            return self.get_accumulated_returns(
                self._require_kwarg(kwargs, "portfolio_id", category),
                self._require_kwarg(kwargs, "date", category),
                period=kwargs.get("period"),
                start=kwargs.get("start"),
                benchmarks=kwargs.get("benchmarks"),
            )

        if category == "performance_attribution":
            return self.get_performance_attribution(
                self._require_kwarg(kwargs, "portfolio_id", category),
                self._require_kwarg(kwargs, "date", category),
            )

        if category == "performance_attribution_detail":
            return self.get_performance_attribution_detail(
                self._require_kwarg(kwargs, "portfolio_id", category),
                self._require_kwarg(kwargs, "date", category),
                window=self._require_kwarg(kwargs, "window", category),
                by=kwargs.get("by", "asset_class"),
            )

        raise DataRetrievalError(f"Unsupported category '{category}' for WarrenOneProvider.")

    # ------------------------------------------------------------------
    # Public methods: cadastro
    # ------------------------------------------------------------------

    def get_raw(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET livre em qualquer rota da API One (ex.: '/v1/return-types'). Somente leitura."""
        return self._get(path, params=params)

    def get_portfolios(self, active_only: bool = True, search: Optional[str] = None) -> pd.DataFrame:
        """
        Lista as carteiras com patrimônio e rentabilidade resumida.

        GET /v1/portfolios/summaries

        Colunas: portfolio_id, name, portfolio_type, currency, is_active, initial_date,
        last_position_date, last_valid_processed_date, total_net_value, inflow_value,
        outflow_value, return_month, return_ytd, return_inception, is_auxiliary.

        ``is_auxiliary`` marca carteiras auxiliares (nome com '/', ex.: 'XXXX-BPCV-0000/RENDIMENTOS'),
        que recebem só fluxos e têm rentabilidade sem significado econômico (+39.000% visto em produção).

        Atenção: ``last_valid_processed_date`` não indica até quando há rentabilidade
        calculada (carteiras com 2025-12-31 ali tinham retornos até 2026-06-30); use
        ``last_position_date`` como data de referência.
        """
        items = self._get_summaries(active_only=active_only, search=search, include_class_allocations=False)
        rows = []
        for item in items:
            row = {dst: item.get(src) for src, dst in _PORTFOLIO_COLUMNS.items()}
            ret = item.get("return") or {}
            row["return_month"] = ret.get("monthlyReturn")
            row["return_ytd"] = ret.get("annualReturn")
            row["return_inception"] = ret.get("inceptionReturn")
            rows.append(row)

        df = pd.DataFrame(rows, columns=[*_PORTFOLIO_COLUMNS.values(), "return_month", "return_ytd", "return_inception"])
        for col in ("initial_date", "last_position_date", "last_valid_processed_date"):
            df[col] = pd.to_datetime(df[col], errors="coerce")
        df["is_auxiliary"] = df["name"].fillna("").str.contains(_AUXILIARY_MARKER, regex=False)
        self._initial_dates.update(
            {pid: ini for pid, ini in zip(df["portfolio_id"], df["initial_date"]) if pd.notna(ini)}
        )
        return df

    def get_class_allocations(self, active_only: bool = True) -> pd.DataFrame:
        """
        Alocação por classe de ativo de cada carteira (posição mais recente).

        GET /v1/portfolios/summaries?includeClassAllocations=true

        Colunas: portfolio_id, portfolio_name, class_id, class_name, total_value, weight.
        """
        items = self._get_summaries(active_only=active_only, include_class_allocations=True)
        rows = [
            {
                "portfolio_id": item.get("id"),
                "portfolio_name": item.get("name"),
                "class_id": alloc.get("classId"),
                "class_name": alloc.get("className"),
                "total_value": alloc.get("totalValue"),
                "weight": alloc.get("weight"),
            }
            for item in items
            for alloc in item.get("classAllocations") or []
        ]
        df = pd.DataFrame(rows, columns=["portfolio_id", "portfolio_name", "class_id", "class_name", "total_value", "weight"])
        df["weight"] = df["weight"].astype(float)
        return df

    def get_indexes(self) -> pd.DataFrame:
        """
        Índices disponíveis para benchmark.

        GET /v1/indexes

        Colunas: index_id, name, is_difference_comparison, currency_id.
        """
        if self._indexes is None:
            payload = self._get("/v1/indexes") or []
            df = pd.DataFrame(payload)
            df = df.rename(columns={
                "id": "index_id",
                "isDifferenceComparison": "is_difference_comparison",
                "benchmarkCurrencyId": "currency_id",
            })
            self._indexes = df
        return self._indexes.copy()

    # ------------------------------------------------------------------
    # Public methods: rentabilidade
    # ------------------------------------------------------------------

    def get_returns(
        self,
        portfolio_id: str,
        date: Union[str, pd.Timestamp],
        *,
        benchmarks: Optional[Sequence[BenchmarkSpec]] = None,
        compare_to: Optional[BenchmarkSpec] = None,
        initial_date: Optional[Union[str, pd.Timestamp]] = None,
    ) -> pd.DataFrame:
        """
        Rentabilidade da carteira (e benchmarks) nas janelas padrão até ``date``.

        GET /v2/management/portfolios/{id}/returns/{date}

        Args:
            benchmarks: ids ou nomes de índices a incluir.
            compare_to: índice para a linha "% de <índice>" (incluído em benchmarks automaticamente).
            initial_date: data inicial da carteira (evita uma chamada extra para mascarar janelas).

        Colunas (long): portfolio_id, date, series, series_type, window, value.
          series_type ∈ {portfolio, benchmark, relative}; window ∈ month, ytd, inception, 3m…36m.
        """
        ref = self._to_timestamp(date)
        benchmark_ids, compare_id = self._resolve_benchmarks(benchmarks, compare_to)
        params = self._benchmark_params(benchmark_ids)
        if compare_id is not None:
            params["comparisonBenchmarkId"] = compare_id

        payload = self._get(f"/v2/management/portfolios/{portfolio_id}/returns/{self._fmt(ref)}", params=params) or []

        rows = []
        for item in payload:
            description = item.get("description")
            series_type = self._classify_return_row(description)
            for window, field in _RETURN_WINDOW_FIELDS.items():
                rows.append({
                    "portfolio_id": portfolio_id,
                    "date": ref,
                    "series": description,
                    "series_type": series_type,
                    "window": window,
                    "value": item.get(field),
                })
        df = pd.DataFrame(rows, columns=["portfolio_id", "date", "series", "series_type", "window", "value"])
        df["value"] = df["value"].astype(float)
        return self._mask_windows(df, portfolio_id, ref, initial_date=initial_date)

    def get_all_returns(
        self,
        date: Union[str, pd.Timestamp],
        *,
        benchmarks: Optional[Sequence[BenchmarkSpec]] = None,
        compare_to: Optional[BenchmarkSpec] = None,
        portfolio_type: Optional[str] = None,
        active_only: bool = True,
        exclude_auxiliary: bool = True,
        use_last_position: bool = False,
        on_error: str = "raise",
    ) -> pd.DataFrame:
        """
        ``get_returns`` para todas as carteiras do summaries (uma chamada por carteira).

        Args:
            portfolio_type: filtra por tipo (ex.: 'Consolidado', 'Carteira').
            exclude_auxiliary: ignora carteiras auxiliares (nome com '/'; ver ``get_portfolios``).
            use_last_position: para carteiras cuja última posição é anterior a ``date``, consulta
                a rentabilidade na última posição (a API responde 204 para datas sem posição).
                A coluna ``date`` traz a data efetivamente usada e ``is_stale`` marca essas carteiras.
            on_error: 'raise' (default) interrompe na primeira falha; 'skip' registra e segue.

        Carteiras sem rentabilidade (204, ou nunca posicionadas com ``use_last_position``) ficam
        fora do resultado e são listadas em warning.
        Colunas: portfolio_id, portfolio_name, requested_date, is_stale + as de ``get_returns``.
        """
        if on_error not in ("raise", "skip"):
            raise ValueError("on_error must be 'raise' or 'skip'.")

        requested = self._to_timestamp(date)
        portfolios = self.get_portfolios(active_only=active_only)
        if portfolio_type is not None:
            portfolios = portfolios[portfolios["portfolio_type"] == portfolio_type]
        if exclude_auxiliary:
            portfolios = portfolios[~portfolios["is_auxiliary"]]

        frames: List[pd.DataFrame] = []
        failed: List[str] = []
        empty: List[str] = []
        stale: List[str] = []
        for i, row in enumerate(portfolios.itertuples(index=False)):
            ref = requested
            last_position = row.last_position_date
            if use_last_position and pd.notna(last_position) and last_position < requested:
                # Sem posição posterior ao início (ex.: 2000-01-03 = nunca posicionada): nada a consultar.
                if pd.notna(row.initial_date) and last_position <= row.initial_date:
                    empty.append(row.name)
                    continue
                ref = last_position

            if i and self.request_delay:
                time.sleep(self.request_delay)
            try:
                df = self.get_returns(
                    row.portfolio_id, ref,
                    benchmarks=benchmarks, compare_to=compare_to, initial_date=row.initial_date,
                )
            except DataRetrievalError:
                if on_error == "raise":
                    raise
                logger.warning("Warren One: falha nos retornos de %s (%s); seguindo.", row.name, row.portfolio_id, exc_info=True)
                failed.append(row.name)
                continue
            if df.empty:
                empty.append(row.name)
                continue
            if ref < requested:
                stale.append(f"{row.name} ({self._fmt(ref)})")
            df.insert(1, "portfolio_name", row.name)
            df.insert(2, "requested_date", requested)
            df.insert(3, "is_stale", ref < requested)
            frames.append(df)

        if failed:
            logger.warning("Warren One: %d carteira(s) com erro: %s", len(failed), ", ".join(failed))
        if stale:
            logger.warning("Warren One: %d carteira(s) com rentabilidade na última posição, anterior a %s: %s",
                           len(stale), self._fmt(requested), ", ".join(stale))
        if empty:
            # Em produção (2026-10), todos os 204 eram carteiras sem posição na data pedida.
            logger.warning("Warren One: %d carteira(s) sem rentabilidade em %s: %s",
                           len(empty), self._fmt(requested), ", ".join(empty))
        if not frames:
            return pd.DataFrame(columns=[
                "portfolio_id", "portfolio_name", "requested_date", "is_stale",
                "date", "series", "series_type", "window", "value",
            ])
        return pd.concat(frames, ignore_index=True)

    def get_monthly_returns(
        self,
        portfolio_id: str,
        date: Union[str, pd.Timestamp],
        *,
        benchmarks: Optional[Sequence[BenchmarkSpec]] = None,
        initial_date: Optional[Union[str, pd.Timestamp]] = None,
    ) -> pd.DataFrame:
        """
        Tabela de rentabilidade mês a mês da carteira (e benchmarks).

        GET /v2/management/{id}/returns/{date}/historical

        Colunas (long): portfolio_id, series, series_type, year, month, value.
        Meses posteriores a ``date`` ou anteriores ao início da carteira vêm como NaN
        (a API devolve 0 nesses casos).
        """
        ref = self._to_timestamp(date)
        benchmark_ids, _ = self._resolve_benchmarks(benchmarks, None)
        payload = self._get(
            f"/v2/management/{portfolio_id}/returns/{self._fmt(ref)}/historical",
            params=self._benchmark_params(benchmark_ids),
        ) or []

        ini = self._initial_date(portfolio_id, initial_date)
        rows = []
        for item in payload:
            year = int(item.get("year"))
            series_type = "portfolio" if item.get("featured") else self._classify_return_row(item.get("description"))
            for month, field in enumerate(_MONTH_FIELDS, start=1):
                value = item.get(field)
                month_end = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
                after_ref = (year, month) > (ref.year, ref.month)
                before_start = ini is not None and month_end <= ini
                rows.append({
                    "portfolio_id": portfolio_id,
                    "series": item.get("description"),
                    "series_type": series_type,
                    "year": year,
                    "month": month,
                    "value": np.nan if (after_ref or before_start) else value,
                })
        df = pd.DataFrame(rows, columns=["portfolio_id", "series", "series_type", "year", "month", "value"])
        df["value"] = df["value"].astype(float)
        return df

    def get_accumulated_returns(
        self,
        portfolio_id: str,
        date: Union[str, pd.Timestamp],
        *,
        period: Optional[str] = None,
        start: Optional[Union[str, pd.Timestamp]] = None,
        benchmarks: Optional[Sequence[BenchmarkSpec]] = None,
    ) -> pd.DataFrame:
        """
        Série diária de retorno acumulado da carteira (e benchmarks) até ``date``.

        GET /v2/management/portfolios/{id}/accumulated-returns/{date}[/{period}]

        Args:
            period: janela móvel até ``date`` ('1m', '3m', '6m', '12m', '24m', '36m').
                Se maior que o histórico, a API devolve a série desde o início (é emitido warning).
            start: rebaseia a série desde o início para começar em 0 nesta data (usa o último
                pregão <= start). Substitui a rota v1 de intervalo, que responde 500.
                Não pode ser combinado com ``period``.

        Colunas (long): portfolio_id, series, series_type, date, value.
        """
        if period is not None and start is not None:
            raise ValueError("Use 'period' or 'start', not both.")
        if period is not None and period not in _ACCUMULATED_PERIODS:
            raise ValueError(f"Invalid period '{period}'. Valid: {', '.join(_ACCUMULATED_PERIODS)}.")

        ref = self._to_timestamp(date)
        benchmark_ids, _ = self._resolve_benchmarks(benchmarks, None)
        path = f"/v2/management/portfolios/{portfolio_id}/accumulated-returns/{self._fmt(ref)}"
        if period is not None:
            path += f"/{period}"
            if self._window_exceeds_history(portfolio_id, ref, period):
                logger.warning(
                    "Warren One: janela %s maior que o histórico de %s; a API devolve a série desde o início.",
                    period, portfolio_id,
                )

        payload = self._get(path, params=self._benchmark_params(benchmark_ids)) or []
        rows = [
            {
                "portfolio_id": portfolio_id,
                "series": item.get("description"),
                "series_type": "portfolio" if item.get("id") == portfolio_id else "benchmark",
                "date": point.get("date"),
                "value": point.get("value"),
            }
            for item in payload
            for point in item.get("valuesByDates") or []
        ]
        df = pd.DataFrame(rows, columns=["portfolio_id", "series", "series_type", "date", "value"])
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = df["value"].astype(float)

        if start is not None:
            df = self._rebase_accumulated(df, self._to_timestamp(start))
        return df.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Public methods: atribuição de performance
    # ------------------------------------------------------------------

    def get_performance_attribution(
        self,
        portfolio_id: str,
        date: Union[str, pd.Timestamp],
        *,
        initial_date: Optional[Union[str, pd.Timestamp]] = None,
    ) -> pd.DataFrame:
        """
        Contribuição de cada classe de ativo para a rentabilidade, por janela.

        GET /v1/management/portfolios/{id}/asset-classes/{date}/performance-attributions

        As contribuições somam a rentabilidade da carteira (linha is_total=True).
        Colunas (long): portfolio_id, date, group, is_total, window, contribution.
          window ∈ month, ytd, inception, 6m, 12m, 24m.
        """
        ref = self._to_timestamp(date)
        payload = self._get(
            f"/v1/management/portfolios/{portfolio_id}/asset-classes/{self._fmt(ref)}/performance-attributions"
        ) or []
        rows = [
            {
                "portfolio_id": portfolio_id,
                "date": ref,
                "group": item.get("description"),
                "is_total": bool(item.get("featured")),
                "window": window,
                "contribution": item.get(field),
            }
            for item in payload
            for window, field in _ATTRIBUTION_WINDOW_FIELDS.items()
        ]
        df = pd.DataFrame(rows, columns=["portfolio_id", "date", "group", "is_total", "window", "contribution"])
        df["contribution"] = df["contribution"].astype(float)
        return self._mask_windows(df, portfolio_id, ref, initial_date=initial_date, value_cols=("contribution",))

    def get_performance_attribution_detail(
        self,
        portfolio_id: str,
        date: Union[str, pd.Timestamp],
        *,
        window: str,
        by: str = "asset_class",
        initial_date: Optional[Union[str, pd.Timestamp]] = None,
    ) -> pd.DataFrame:
        """
        Resultado financeiro (R$) e contribuição por classe, instituição ou veículo em uma janela.

        GET /v1/management/{id}/{asset-classes|institutions|vehicles}/{date}/performance-attributions/{periodType}

        Args:
            window: month, ytd, inception, 3m, 6m, 12m, 24m, 36m (mapeamento p/ periodType inferido).
            by: 'asset_class', 'institution' ou 'vehicle' (este último respondeu 204 em homologação).

        Colunas: portfolio_id, date, dimension, window, group_id, group, result_value,
        result_share, contribution.
          result_share = fatia do resultado em R$ (soma 1, pode ser negativa ou > 1);
          não é o peso da classe na carteira (para isso, ``get_class_allocations``).
        """
        if window not in _WINDOW_TO_PERIOD_TYPE:
            raise ValueError(f"Invalid window '{window}'. Valid: {', '.join(_WINDOW_TO_PERIOD_TYPE)}.")
        if by not in _ATTRIBUTION_DIMENSIONS:
            raise ValueError(f"Invalid by '{by}'. Valid: {', '.join(_ATTRIBUTION_DIMENSIONS)}.")

        ref = self._to_timestamp(date)
        payload = self._get(
            f"/v1/management/{portfolio_id}/{_ATTRIBUTION_DIMENSIONS[by]}/{self._fmt(ref)}"
            f"/performance-attributions/{_WINDOW_TO_PERIOD_TYPE[window]}"
        ) or []
        rows = [
            {
                "portfolio_id": portfolio_id,
                "date": ref,
                "dimension": by,
                "window": window,
                "group_id": item.get("classId") or item.get("id"),
                "group": item.get("name"),
                "result_value": item.get("resultValue"),
                "result_share": item.get("weightPercentage"),
                "contribution": item.get("earningsPercentage"),
            }
            for item in sorted(payload, key=lambda x: x.get("order", 0))
        ]
        df = pd.DataFrame(rows, columns=[
            "portfolio_id", "date", "dimension", "window", "group_id", "group",
            "result_value", "result_share", "contribution",
        ])
        for col in ("result_value", "result_share", "contribution"):
            df[col] = df[col].astype(float)
        return self._mask_windows(
            df, portfolio_id, ref, initial_date=initial_date,
            value_cols=("result_value", "result_share", "contribution"),
        )

    # ------------------------------------------------------------------
    # Internals: auth
    # ------------------------------------------------------------------

    def _ensure_token(self) -> None:
        if self._id_token and time.time() < self._id_token_expiry_epoch - _TOKEN_REFRESH_MARGIN_SECONDS:
            return
        self._refresh_token()

    def _refresh_token(self) -> None:
        """Autentica de novo com clientId/secretKey (o fluxo de refreshToken não é documentado)."""
        url = f"{self.auth_base_url}{_AUTH_PATH}"
        try:
            logger.info("Requesting Warren One token at %s", url)
            resp = self._session.post(
                url,
                json={"clientId": self.client_id, "secretKey": self.secret_key},
                timeout=self.timeout_seconds,
                verify=self.verify_ssl,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise DataRetrievalError(f"Warren One auth failed (status={status}): {exc}") from exc

        token = data.get("idToken") if isinstance(data, dict) else None
        if not token:
            raise DataRetrievalError(
                f"Warren One auth response missing idToken (status={resp.status_code}, keys={list(data or {})})"
            )
        try:
            expires_in = int(data.get("expiresIn", _TOKEN_DEFAULT_EXPIRES_SECONDS))
        except (TypeError, ValueError):
            expires_in = _TOKEN_DEFAULT_EXPIRES_SECONDS

        self._id_token = token
        self._id_token_expiry_epoch = time.time() + expires_in
        logger.info("Warren One token acquired (expires in %ds).", expires_in)

    def _build_headers(self) -> Dict[str, str]:
        self._ensure_token()
        return {
            "Authorization": f"Bearer {self._id_token}",
            "x-tenant-id": self.tenant_id,
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # Internals: HTTP
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET com retry em 429/5xx transitórios e um re-auth em 401. 204/corpo vazio → None."""
        url = self._build_url(path)
        logger.info("Warren One Request: GET %s", url)
        if params:
            logger.debug("Warren One Params: %s", params)

        reauthenticated = False
        attempt = 0
        while True:
            try:
                resp = self._session.get(
                    url,
                    headers=self._build_headers(),
                    params=params,
                    timeout=self.timeout_seconds,
                    verify=self.verify_ssl,
                )
            except requests.RequestException as exc:
                raise DataRetrievalError(f"Warren One request error [GET {url}]: {exc}") from exc

            if resp.status_code == 401 and not reauthenticated:
                logger.info("Warren One: 401, renovando token e repetindo a chamada.")
                self._id_token = None
                reauthenticated = True
                continue

            if resp.status_code in _RETRY_STATUS_CODES and attempt < self.max_retries - 1:
                wait = self._retry_wait_seconds(attempt, resp)
                logger.warning(
                    "Warren One HTTP %s em %s (tentativa %d/%d); aguardando %.0fs.",
                    resp.status_code, url, attempt + 1, self.max_retries, wait,
                )
                time.sleep(wait)
                attempt += 1
                continue

            if resp.status_code >= 400:
                body = (resp.text or "").strip()
                detail = f"body: {body[:500]}" if body else "corpo vazio"
                raise DataRetrievalError(
                    f"Warren One request failed [GET {url}] HTTP {resp.status_code} ({detail}); params={params}"
                )

            if resp.status_code == 204 or not resp.content:
                return None
            try:
                return resp.json()  # a API declara text/plain em parte das rotas, mas devolve JSON
            except ValueError as exc:
                raise DataRetrievalError(
                    f"Warren One returned non-JSON body [GET {url}]: {resp.text[:200]!r}"
                ) from exc

    def _retry_wait_seconds(self, attempt: int, resp: requests.Response) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), self.retry_backoff)
            except ValueError:
                pass
        return self.retry_backoff * (2 ** attempt)

    def _build_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.api_base_url}{path}"

    # ------------------------------------------------------------------
    # Internals: helpers
    # ------------------------------------------------------------------

    def _get_summaries(
        self,
        *,
        active_only: bool,
        include_class_allocations: bool,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "activesOnly": str(active_only).lower(),
            "includeClassAllocations": str(include_class_allocations).lower(),
            "includeAccumulatedReturns": "false",
        }
        if search:
            params["searchTerm"] = search
        return self._get("/v1/portfolios/summaries", params=params) or []

    def _initial_date(
        self,
        portfolio_id: str,
        initial_date: Optional[Union[str, pd.Timestamp]] = None,
    ) -> Optional[pd.Timestamp]:
        if initial_date is not None and not pd.isna(initial_date):
            return self._to_timestamp(initial_date)
        if portfolio_id not in self._initial_dates:
            summary = self._get(f"/v1/portfolios/summaries/{portfolio_id}") or {}
            ini = pd.to_datetime(summary.get("initialDate"), errors="coerce")
            if pd.isna(ini):
                return None
            self._initial_dates[portfolio_id] = ini
        return self._initial_dates[portfolio_id]

    def _window_exceeds_history(
        self,
        portfolio_id: str,
        ref: pd.Timestamp,
        window: str,
        initial_date: Optional[Union[str, pd.Timestamp]] = None,
    ) -> bool:
        months = _TRAILING_WINDOW_MONTHS.get(window)
        if months is None:
            return False
        ini = self._initial_date(portfolio_id, initial_date)
        if ini is None:
            return False
        # Meses-calendário completos entre o início e a referência (início em 31/12 e
        # referência em 30/06 = 6 meses → janela de 6m é válida, 12m não).
        history_months = (ref.year - ini.year) * 12 + (ref.month - ini.month)
        return history_months < months

    def _mask_windows(
        self,
        df: pd.DataFrame,
        portfolio_id: str,
        ref: pd.Timestamp,
        *,
        initial_date: Optional[Union[str, pd.Timestamp]] = None,
        value_cols: Iterable[str] = ("value",),
    ) -> pd.DataFrame:
        """Troca por NaN as janelas móveis mais longas que o histórico da carteira."""
        if not self.mask_short_history or df.empty:
            return df
        windows = [w for w in df["window"].unique() if self._window_exceeds_history(portfolio_id, ref, w, initial_date)]
        if windows:
            df.loc[df["window"].isin(windows), list(value_cols)] = np.nan
        return df

    @staticmethod
    def _rebase_accumulated(df: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
        if df.empty:
            return df
        frames = []
        for _, group in df.groupby(["series", "series_type"], sort=False):
            group = group.sort_values("date")
            base_rows = group[group["date"] <= start]
            if base_rows.empty:
                raise DataRetrievalError(
                    f"start={start.date()} é anterior ao início da série ({group['date'].min().date()})."
                )
            base_date = base_rows["date"].iloc[-1]
            base_value = base_rows["value"].iloc[-1]
            group = group[group["date"] >= base_date].copy()
            group["value"] = (1 + group["value"]) / (1 + base_value) - 1
            frames.append(group)
        return pd.concat(frames, ignore_index=True)

    def _resolve_benchmarks(
        self,
        benchmarks: Optional[Sequence[BenchmarkSpec]],
        compare_to: Optional[BenchmarkSpec],
    ) -> tuple[List[int], Optional[int]]:
        if isinstance(benchmarks, (str, int)):
            benchmarks = [benchmarks]
        ids = [self._resolve_benchmark(b) for b in benchmarks or []]
        compare_id = self._resolve_benchmark(compare_to) if compare_to is not None else None
        # A API só devolve a linha "% de X" se X também estiver em benchmarkIds.
        if compare_id is not None and compare_id not in ids:
            ids.append(compare_id)
        return ids, compare_id

    def _resolve_benchmark(self, benchmark: BenchmarkSpec) -> int:
        if isinstance(benchmark, (int, np.integer)):
            return int(benchmark)
        if isinstance(benchmark, str) and benchmark.strip().isdigit():
            return int(benchmark)
        indexes = self.get_indexes()
        match = indexes[indexes["name"].str.upper() == str(benchmark).strip().upper()]
        if match.empty:
            raise DataRetrievalError(f"Benchmark '{benchmark}' não encontrado em /v1/indexes.")
        return int(match["index_id"].iloc[0])

    @staticmethod
    def _benchmark_params(benchmark_ids: Sequence[int]) -> Dict[str, Any]:
        return {"benchmarkIds": list(benchmark_ids)} if benchmark_ids else {}

    @staticmethod
    def _classify_return_row(description: Optional[str]) -> str:
        if description == "Rentabilidade":
            return "portfolio"
        if description and description.startswith("% de "):
            return "relative"
        return "benchmark"

    @staticmethod
    def _to_timestamp(value: Union[str, pd.Timestamp]) -> pd.Timestamp:
        try:
            return pd.Timestamp(value).normalize()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid date {value!r}; expected 'YYYY-MM-DD'.") from exc

    @staticmethod
    def _fmt(ts: pd.Timestamp) -> str:
        return ts.strftime("%Y-%m-%d")

    @staticmethod
    def _require_kwarg(kwargs: Dict[str, Any], key: str, category: str) -> Any:
        value = kwargs.get(key)
        if value is None:
            raise DataRetrievalError(f"category='{category}' requires kwarg '{key}'.")
        return value

    def _validate_credentials(self) -> None:
        missing = []
        if not self.client_id:
            missing.append("PERSEVERA_WARRENONE_CLIENT_ID")
        if not self.secret_key:
            missing.append("PERSEVERA_WARRENONE_SECRET_KEY")
        if not self.tenant_id:
            missing.append("PERSEVERA_WARRENONE_TENANT_ID")
        if missing:
            raise ValueError("Missing Warren One configuration variables: " + ", ".join(missing))

    @staticmethod
    def _to_dataframe(payload: Any) -> pd.DataFrame:
        if payload is None:
            return pd.DataFrame()
        if isinstance(payload, list):
            return pd.json_normalize(payload) if payload else pd.DataFrame()
        if isinstance(payload, dict):
            for key in ("data", "items", "results", "content"):
                if isinstance(payload.get(key), list):
                    return pd.json_normalize(payload[key]) if payload[key] else pd.DataFrame()
            return pd.json_normalize([payload])
        return pd.DataFrame()
