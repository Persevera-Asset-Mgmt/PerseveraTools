"""Testes offline da simulação de P&L do factor_investing (drift dos pesos, sem banco)."""

from typing import Any, Optional

import numpy as np
import pandas as pd
import pytest

from persevera_tools.quant_research.factor_investing.config import BacktestConfig
from persevera_tools.quant_research.factor_investing.engine import (
    _apply_realized_trades,
    _daily_portfolio_return,
    _simulate_holdings_pnl,
    _traded_notional,
)

DATES = pd.bdate_range("2024-01-01", periods=6)


def _sim(prices, weights_df, *, drift, borrow_rate=0.0, cost_bps=0.0):
    nav, rets, costs, events, _, _, traded = _simulate_holdings_pnl(
        prices=prices,
        weights_df=weights_df,
        start_date=prices.index[0],
        end_date=prices.index[-1],
        initial_nav=1.0,
        borrow_rate=borrow_rate,
        trading_cost_bps=cost_bps,
        weight_drift=drift,
    )
    return nav, rets, costs, events, traded


def _legacy_sim(prices, weights_df, *, borrow_rate, cost_bps):
    """Simulação anterior (pesos voltam ao alvo todo dia), para checar regressão."""
    idx = prices.index
    daily = weights_df.reindex(idx).ffill()
    asset_returns = prices.pct_change(fill_method=None)
    costs: dict[Any, float] = {}
    prev: Optional[pd.Series] = None
    for dt, row in weights_df.iterrows():
        row = row[row.notna() & (row != 0.0)]
        costs[dt] = _traded_notional(prev, row) * cost_bps / 1e4
        prev = row
    out = pd.Series(index=idx, dtype=float)
    for i, dt in enumerate(idx):
        w = pd.Series(dtype=float)
        if i > 0:
            raw = daily.loc[idx[i - 1]]
            w = raw[raw.notna() & (raw != 0.0)]
        cost = costs.get(dt, 0.0)
        borrow = 0.0
        ret = float("nan")
        if not w.empty:
            ret, _ = _daily_portfolio_return(w, asset_returns.loc[dt], as_of=dt)
            borrow = float((-w[w < 0]).sum()) * borrow_rate / 252.0
        if pd.isna(ret):
            if w.empty and cost == 0.0:
                continue
            ret = 0.0
        out.loc[dt] = ret - cost - borrow
    first = out.first_valid_index()
    return out.loc[first:].fillna(0.0)


# --------------------------------------------------------------------------- #
# Drift
# --------------------------------------------------------------------------- #

def test_long_only_drift_is_buy_and_hold():
    prices = pd.DataFrame(
        {"AAAA3": [10, 11, 12.1, 9.0, 9.9, 10.5], "BBBB3": [20, 19, 18, 21, 22, 20.0]},
        index=DATES,
    )
    weights = pd.DataFrame({"AAAA3": [0.5], "BBBB3": [0.5]}, index=[DATES[0]])
    nav, *_ = _sim(prices, weights, drift=True)
    expected = 0.5 * prices["AAAA3"] / 10 + 0.5 * prices["BBBB3"] / 20
    np.testing.assert_allclose(nav.loc[DATES[1]:].to_numpy(), expected.loc[DATES[1]:].to_numpy())


def test_short_leg_grows_when_shorted_name_rallies():
    # +1 AAAA3 / −1 BBBB3; BBBB3 sobe 50% e depois mais 10%.
    prices = pd.DataFrame(
        {"AAAA3": [10.0] * 6, "BBBB3": [10, 15, 16.5, 16.5, 16.5, 16.5]}, index=DATES
    )
    weights = pd.DataFrame({"AAAA3": [1.0], "BBBB3": [-1.0]}, index=[DATES[0]])
    _, drift_rets, *_ = _sim(prices, weights, drift=True)
    _, const_rets, *_ = _sim(prices, weights, drift=False)
    assert drift_rets.loc[DATES[1]] == pytest.approx(-0.5)
    assert const_rets.loc[DATES[1]] == pytest.approx(-0.5)
    # Depois do dia 1 a carteira vale 0,5: comprado 2,0, vendido −3,0 (em % do NAV).
    assert drift_rets.loc[DATES[2]] == pytest.approx(-3.0 * 0.1)
    assert const_rets.loc[DATES[2]] == pytest.approx(-1.0 * 0.1)


def test_rebalance_trades_from_drifted_weights():
    prices = pd.DataFrame(
        {"AAAA3": [10, 20, 20, 20, 20, 20.0], "BBBB3": [10.0] * 6}, index=DATES
    )
    weights = pd.DataFrame(
        {"AAAA3": [0.5, 0.5], "BBBB3": [0.5, 0.5]}, index=[DATES[0], DATES[1]]
    )
    _, rets, costs, _, traded = _sim(prices, weights, drift=True, cost_bps=10)
    # AAAA3 dobra: pesos flutuados 2/3 e 1/3; voltar a 50/50 negocia 1/3 do NAV.
    assert traded[DATES[0]] == pytest.approx(1.0)  # primeira carteira, vinda do caixa
    assert traded[DATES[1]] == pytest.approx(1 / 3)
    assert costs.loc[DATES[1], "trading_cost"] == pytest.approx(1 / 3 * 10 / 1e4)
    assert rets.loc[DATES[1]] == pytest.approx(0.5 - 1 / 3 * 10 / 1e4)
    _, _, _, _, traded_const = _sim(prices, weights, drift=False, cost_bps=10)
    assert traded_const[DATES[1]] == pytest.approx(0.0)


def test_borrow_accrues_on_drifted_short_leg():
    prices = pd.DataFrame(
        {"AAAA3": [10.0] * 6, "BBBB3": [10, 15, 15, 15, 15, 15.0]}, index=DATES
    )
    weights = pd.DataFrame({"AAAA3": [1.0], "BBBB3": [-1.0]}, index=[DATES[0]])
    rate = 0.0252  # 0,01% ao dia
    _, _, costs, _, _ = _sim(prices, weights, drift=True, borrow_rate=rate)
    day = rate / 252
    assert costs.loc[DATES[1], "borrow_cost"] == pytest.approx(day)
    nav_after = 1 - 0.5 - day
    assert costs.loc[DATES[2], "borrow_cost"] == pytest.approx(1.5 / nav_after * day)


def test_name_without_price_is_closed_once_and_weight_goes_to_its_leg():
    prices = pd.DataFrame(
        {
            "AAAA3": [10, 10, 11, 11, 11, 11.0],
            "BBBB3": [10, 10, 12, 12, 12, 12.0],
            "CCCC3": [10, 10, np.nan, np.nan, np.nan, np.nan],
        },
        index=DATES,
    )
    weights = pd.DataFrame({"AAAA3": [1 / 3], "BBBB3": [1 / 3], "CCCC3": [1 / 3]}, index=[DATES[0]])
    _, rets, _, events, _ = _sim(prices, weights, drift=True)
    # CCCC3 sai no dia 2 e o peso vai para AAAA3 e BBBB3 (50/50).
    assert rets.loc[DATES[2]] == pytest.approx(0.5 * 0.1 + 0.5 * 0.2)
    ev = pd.DataFrame(events)
    assert (ev["event"] == "renorm").sum() == 1
    assert ev.loc[ev["event"] == "price_gap", "code"].tolist() == ["CCCC3"]


# --------------------------------------------------------------------------- #
# Sem drift: igual à simulação anterior
# --------------------------------------------------------------------------- #

def test_without_drift_matches_legacy_simulation():
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2024-01-01", periods=80)
    tickers = [f"T{i:02d}AA3" for i in range(10)]
    prices = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, 0.02, (len(dates), len(tickers))), axis=0),
        index=dates, columns=tickers,
    )
    prices.iloc[40:, 3] = np.nan  # um papel sai do pregão
    rebal = dates[[0, 20, 40, 60]]
    rows = []
    for d in rebal:
        w = pd.Series(0.0, index=tickers)
        picks = rng.choice(len(tickers), 6, replace=False)
        w.iloc[picks[:3]] = 1 / 3
        w.iloc[picks[3:]] = -1 / 3
        rows.append(w.rename(d))
    weights = pd.DataFrame(rows)
    _, rets, *_ = _sim(prices, weights, drift=False, borrow_rate=0.05, cost_bps=10)
    legacy = _legacy_sim(prices, weights, borrow_rate=0.05, cost_bps=10)
    pd.testing.assert_series_equal(
        rets.dropna(), legacy, check_names=False, check_freq=False
    )


# --------------------------------------------------------------------------- #
# Diagnóstico e config
# --------------------------------------------------------------------------- #

def test_apply_realized_trades_overwrites_and_keeps_first_turnover_nan():
    rebals = pd.DataFrame(
        {"turnover": [np.nan, 0.1], "traded_notional": [1.0, 0.2], "trading_cost": [1e-3, 2e-4]},
        index=pd.DatetimeIndex([DATES[0], DATES[3]]),
    )
    out = _apply_realized_trades(rebals, {DATES[0]: 1.0, DATES[3]: 0.5}, 10)
    assert np.isnan(out["turnover"].iloc[0])
    assert out["turnover"].iloc[1] == pytest.approx(0.25)
    assert out["traded_notional"].iloc[1] == pytest.approx(0.5)
    assert out["trading_cost"].iloc[1] == pytest.approx(0.5 * 10 / 1e4)


def test_weight_drift_is_on_by_default():
    cfg = BacktestConfig(start_date="2024-01-01", end_date="2024-12-31", components=["roe"])
    assert cfg.weight_drift is True
