from typing import Dict, Iterable, List, Optional, Union

import pandas as pd
import requests

from .base import DataProvider, DataRetrievalError


class OpeaProvider(DataProvider):
    """Provider for Opea cashflow/events, resolved from CETIP codes."""

    BASE_URL = "https://app.opea.com.br/bff/v1/api/emissao"
    DETALHE_PATH = "/passivosoperacoes/detalhe"
    FLUXO_PATH = "/precificacoes/detalhe/fluxofinanceiro/grafico"
    CATEGORY = "pagamentos"

    FLOW_FIELDS = {
        "amortizacao": "amortizacao",
        "jurosPago": "juros_pago",
        "saldoDevedor": "saldo_devedor",
        "amortizacaoExtraordinaria": "amortizacao_extraordinaria",
        "premio": "premio",
        "jurosRemuneracao": "juros_remuneracao",
    }

    def __init__(self, start_date: str = "1980-01-01", timeout_seconds: int = 30):
        super().__init__(start_date)
        self.timeout_seconds = timeout_seconds
        self._codigo_opea_cache: Dict[str, str] = {}

    def get_data(
        self,
        category: str,
        codes: Optional[Union[str, Iterable[str]]] = None,
        **kwargs,
    ) -> pd.DataFrame:
        """
        Retrieve Opea cashflow events for one or more CETIP codes.

        Args:
            category: Must be 'opea_fluxo'.
            codes: CETIP / IF code(s). Also accepted via kwargs as
                ``codigo_cetip`` or ``codigo_if``.

        Returns:
            DataFrame with columns: ['date', 'code', 'field', 'value']
        """
        self._log_processing(category)

        if category != self.CATEGORY:
            raise ValueError(f"Invalid category: {category}")

        cetip_codes = self._normalize_codes(
            codes if codes is not None else kwargs.get("codigo_cetip", kwargs.get("codigo_if"))
        )
        if not cetip_codes:
            raise ValueError("At least one CETIP code is required (codes / codigo_cetip / codigo_if)")

        frames: List[pd.DataFrame] = []
        for cetip in cetip_codes:
            try:
                codigo_opea = self.resolve_codigo_opea(cetip)
                flow_df = self._fetch_fluxo(codigo_opea)
                frames.append(self._parse_fluxo(flow_df, cetip))
            except Exception as exc:
                self.logger.warning("Failed to retrieve Opea fluxo for %s: %s", cetip, exc)

        if not frames:
            raise DataRetrievalError(
                f"No cashflow data retrieved from Opea for codes: {cetip_codes}"
            )

        df = pd.concat(frames, ignore_index=True)
        return self._validate_output(df)

    def resolve_codigo_opea(self, codigo_cetip: str) -> str:
        """Resolve CETIP / IF code to Opea operation code, with in-memory cache."""
        codigo_cetip = str(codigo_cetip).strip()
        if not codigo_cetip:
            raise ValueError("codigo_cetip cannot be empty")

        if codigo_cetip in self._codigo_opea_cache:
            return self._codigo_opea_cache[codigo_cetip]

        payload = self._get_json(
            f"{self.BASE_URL}{self.DETALHE_PATH}",
            params={"codigoIf": codigo_cetip},
        )
        content = payload.get("content") or {}
        operacao = content.get("operacao") or {}
        codigo_opea = operacao.get("CodigoOpea") or operacao.get("codigoOpea")

        if not codigo_opea:
            raise DataRetrievalError(
                f"Could not resolve CodigoOpea for CETIP code '{codigo_cetip}'"
            )

        codigo_opea = str(codigo_opea).strip()
        self._codigo_opea_cache[codigo_cetip] = codigo_opea
        return codigo_opea

    def _fetch_fluxo(self, codigo_opea: str) -> pd.DataFrame:
        payload = self._get_json(
            f"{self.BASE_URL}{self.FLUXO_PATH}",
            params={"codigoOpea": codigo_opea},
        )
        content = payload.get("content") or []
        if not content:
            raise DataRetrievalError(f"Empty cashflow for CodigoOpea '{codigo_opea}'")
        return pd.DataFrame(content)

    def _parse_fluxo(self, raw_df: pd.DataFrame, codigo_cetip: str) -> pd.DataFrame:
        if raw_df.empty or "dataPagamento" not in raw_df.columns:
            raise DataRetrievalError(
                f"Unexpected cashflow payload for CETIP code '{codigo_cetip}'"
            )

        available = [col for col in self.FLOW_FIELDS if col in raw_df.columns]
        if not available:
            raise DataRetrievalError(
                f"Cashflow payload has no numeric fields for CETIP code '{codigo_cetip}'"
            )

        df = raw_df[["dataPagamento"] + available].copy()
        df = df.rename(columns={"dataPagamento": "date", **self.FLOW_FIELDS})
        df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
        df["code"] = codigo_cetip

        value_cols = [self.FLOW_FIELDS[col] for col in available]
        long_df = df.melt(
            id_vars=["date", "code"],
            value_vars=value_cols,
            var_name="field",
            value_name="value",
        )
        return long_df.dropna(subset=["date", "value"])

    def _get_json(self, url: str, params: Dict[str, str]) -> dict:
        try:
            response = requests.get(url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json() or {}
        except Exception as exc:
            raise DataRetrievalError(f"Failed to retrieve data from Opea ({url}): {exc}") from exc

        messages = payload.get("messages") or {}
        if messages.get("hasError"):
            errors = messages.get("errors") or []
            raise DataRetrievalError(f"Opea API returned error for {params}: {errors}")

        return payload

    @staticmethod
    def _normalize_codes(codes: Optional[Union[str, Iterable[str]]]) -> List[str]:
        if codes is None:
            return []
        if isinstance(codes, str):
            return [codes.strip()] if codes.strip() else []
        return [str(code).strip() for code in codes if str(code).strip()]
