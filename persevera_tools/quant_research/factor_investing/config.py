"""Configuration for the factor investing backtest engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Optional, Sequence, Union

import pandas as pd

Construction = Literal["long_only", "long_short"]
SelectionMode = Literal["quantile", "top_n"]
DateLike = Union[str, date, datetime, pd.Timestamp]


def _to_timestamp(value: DateLike) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


@dataclass
class BacktestConfig:
    """
    Stable interface between callers and ``run_backtest``.

    Provide either ``style`` (Fibery ``Estilo`` tag) or explicit ``components``
    (factor_zoo mnemonics / Fibery ``Name``). Explicit ``components`` win when both
    are set.

    Universe: candidate pool from Fibery ``Inv-Taxonomia/Ativos`` filtered by
    ``denomination`` (default ``"BRL"``). On each rebalance the investable set is
    ``adtv_field >= adtv_min`` (point-in-time). Pass ``codes`` to restrict further.

    Selection: ``selection_mode="quantile"`` uses ``quantile`` (fraction per tail);
    ``selection_mode="top_n"`` uses a fixed ``top_n`` names per tail.

    Rebalance schedule: pass ``rebalance_freq`` (pandas offset alias such as
    ``"BME"`` / ``"W-FRI"``) or an explicit ``rebalance_dates`` list.

    Costs (defaults 0 = cost-free NAV, backward compatible):
      - ``trading_cost_bps`` is one-way, charged on two-way traded notional
        ``sum(|Δw|)`` at the rebalance close, after the old book's daily P&L.
        ``10`` means 10 bps of every currency unit bought or sold.
      - ``borrow_rate`` is an annualized decimal (same convention as
        ``risk_free_rate``), accrued daily as
        ``short_gross * borrow_rate / 252``. Long-only is unaffected.
    """

    start_date: DateLike
    end_date: DateLike
    construction: Construction = "long_only"
    style: Optional[str] = None
    components: Optional[Sequence[str]] = None
    selection_mode: SelectionMode = "quantile"
    quantile: float = 0.2
    top_n: Optional[int] = None
    denomination: str = "BRL"
    adtv_field: str = "median_dollar_volume_traded_21d"
    adtv_min: float = 8_000_000.0
    rebalance_freq: Optional[str] = "BME"
    rebalance_dates: Optional[Sequence[DateLike]] = None
    price_field: str = "price_close"
    codes: Optional[Sequence[str]] = None
    price_ffill_limit: int = 5
    zR: float = 3.0
    zC: float = 3.0
    min_names: int = 10
    risk_free_rate: float = 0.0
    initial_nav: float = 1.0
    trading_cost_bps: float = 0.0
    borrow_rate: float = 0.0

    def __post_init__(self) -> None:
        self.start_date = _to_timestamp(self.start_date)
        self.end_date = _to_timestamp(self.end_date)
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        if self.construction not in ("long_only", "long_short"):
            raise ValueError("construction must be 'long_only' or 'long_short'")
        if self.selection_mode not in ("quantile", "top_n"):
            raise ValueError("selection_mode must be 'quantile' or 'top_n'")
        if self.selection_mode == "quantile":
            if not 0.0 < self.quantile <= 0.5:
                raise ValueError("quantile must be in (0, 0.5]")
        else:
            if self.top_n is None or int(self.top_n) < 1:
                raise ValueError("top_n must be an integer >= 1 when selection_mode='top_n'")
            self.top_n = int(self.top_n)
        if self.adtv_min < 0:
            raise ValueError("adtv_min must be >= 0")
        if self.min_names < 1:
            raise ValueError("min_names must be >= 1")
        if self.price_ffill_limit < 0:
            raise ValueError("price_ffill_limit must be >= 0")
        if not str(self.denomination).strip():
            raise ValueError("denomination cannot be empty")
        if self.zR <= 0 or self.zC <= 0:
            raise ValueError("zR and zC must be positive")
        if self.initial_nav <= 0:
            raise ValueError("initial_nav must be positive")
        if self.trading_cost_bps < 0:
            raise ValueError("trading_cost_bps must be >= 0")
        if self.borrow_rate < 0:
            raise ValueError("borrow_rate must be >= 0")
        if not self.style and not self.components:
            raise ValueError("Provide style and/or components")
        if self.rebalance_dates is None and not self.rebalance_freq:
            raise ValueError("Provide rebalance_freq or rebalance_dates")
        if self.components is not None:
            self.components = tuple(str(c) for c in self.components)
            if not self.components:
                raise ValueError("components cannot be empty when provided")
        if self.codes is not None:
            self.codes = tuple(str(c) for c in self.codes)
            if not self.codes:
                raise ValueError("codes cannot be empty when provided")
        if self.rebalance_dates is not None:
            self.rebalance_dates = tuple(_to_timestamp(d) for d in self.rebalance_dates)
