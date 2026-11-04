"""Testes offline do universo padrão do factor_investing (índices B3, sem banco)."""

import pandas as pd

from persevera_tools.quant_research.factor_investing.config import BacktestConfig
from persevera_tools.quant_research.factor_investing.engine import _form_rebalance_books

ADTV_FIELD = "median_dollar_volume_traded_21d"
DATES = pd.bdate_range("2024-01-01", "2024-03-29")
TICKERS = [f"T{chr(65 + i)}AA3" for i in range(14)]


def _panel(records):
    frame = pd.DataFrame(
        {key: pd.Series({pd.Timestamp(d): v for d, v in obs.items()}) for key, obs in records.items()}
    ).sort_index()
    frame.columns = pd.MultiIndex.from_tuples(frame.columns, names=["ticker", "descriptor"])
    return frame


def _books(cfg, membership, adtv_value=1e8):
    prices = pd.DataFrame(100.0, index=DATES, columns=TICKERS)
    adtv = pd.DataFrame(
        adtv_value, index=DATES,
        columns=pd.MultiIndex.from_product([TICKERS, [ADTV_FIELD]], names=["ticker", "descriptor"]),
    )
    comp = _panel({(t, "momentum_6m"): {"2024-03-28": float(i)} for i, t in enumerate(TICKERS)})
    return _form_rebalance_books(
        cfg,
        rebalance_dates=pd.DatetimeIndex([pd.Timestamp("2024-03-29")]),
        adtv_panel=adtv,
        component_panel=comp,
        prices=prices,
        components=("momentum_6m",),
        higher_is_better={},
        membership=membership,
    )


def test_default_universe_is_ibx100_and_smll_without_adtv_floor():
    cfg = BacktestConfig(start_date="2024-01-01", end_date="2024-12-31", components=["roe"])
    assert cfg.universe_index == ("IBX100", "SMLL")
    assert cfg.adtv_min == 0.0


def test_default_universe_keeps_only_index_members():
    # Os dois maiores scores (TMAA3, TNAA3) não são membros do índice.
    members = TICKERS[:-2]
    membership = pd.DataFrame({"date": pd.Timestamp("2024-03-28"), "code": members})
    cfg = BacktestConfig(start_date="2024-01-01", end_date="2024-03-29", components=["momentum_6m"])
    scores, weights, rebals, _ = _books(cfg, membership, adtv_value=1.0)  # ADTV baixo não filtra
    assert set(scores.columns) == set(members)
    assert set(weights.columns[weights.iloc[0] > 0]) == {members[-2], members[-1]}
    assert rebals["n_index_members"].iloc[0] == len(members)


def test_universe_index_none_uses_adtv_floor():
    cfg = BacktestConfig(
        start_date="2024-01-01", end_date="2024-03-29", components=["momentum_6m"],
        universe_index=None, adtv_min=8_000_000,
    )
    scores, *_ = _books(cfg, membership=None, adtv_value=1e8)
    assert set(scores.columns) == set(TICKERS)
    cfg_low = BacktestConfig(
        start_date="2024-01-01", end_date="2024-03-29", components=["momentum_6m"],
        universe_index=None, adtv_min=8_000_000, min_names=1,
    )
    try:
        _books(cfg_low, membership=None, adtv_value=1.0)
    except ValueError as exc:
        assert "No valid rebalance portfolios" in str(exc)
    else:
        raise AssertionError("ADTV abaixo do mínimo deveria deixar o universo vazio")
