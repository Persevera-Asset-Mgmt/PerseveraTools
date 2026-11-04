"""Testes offline do limite de idade dos componentes (factor_investing, sem banco)."""

import logging

import numpy as np
import pandas as pd
import pytest

from persevera_tools.quant_research.factor_investing.config import (
    COMPONENT_LOOKBACK_DAYS,
    DEFAULT_MAX_AGE_BY_FREQUENCY,
    BacktestConfig,
)
from persevera_tools.quant_research.factor_investing.definitions import (
    get_component_max_age,
    infer_frequency,
)
from persevera_tools.quant_research.factor_investing.engine import _form_rebalance_books
from persevera_tools.quant_research.factor_investing.result import (
    BacktestDiagnostics,
    summarize_diagnostics,
)
from persevera_tools.quant_research.factor_investing.scoring import (
    last_values_with_age,
    snapshot_components,
)


def _panel(records):
    """records: {(ticker, field): {date: value}} → date × (ticker, descriptor) panel."""
    frame = pd.DataFrame(
        {key: pd.Series({pd.Timestamp(d): v for d, v in obs.items()}) for key, obs in records.items()}
    ).sort_index()
    frame.columns = pd.MultiIndex.from_tuples(frame.columns, names=["ticker", "descriptor"])
    return frame


def _legacy_snapshot(panel, as_of, components):
    """Implementação anterior (ffill sem limite) para checar regressão."""
    snap = panel.loc[:as_of].ffill().iloc[-1]
    return pd.DataFrame({c: snap.xs(c, level=-1) for c in components})


# --------------------------------------------------------------------------- #
# snapshot_components / last_values_with_age
# --------------------------------------------------------------------------- #

def test_value_at_limit_is_kept_and_one_day_older_is_dropped():
    panel = _panel({("AAAA3", "roe"): {"2024-01-01": 10.0}, ("BBBB3", "roe"): {"2024-01-02": 20.0}})
    as_of = pd.Timestamp("2024-01-31")  # AAAA3 com 30 dias, BBBB3 com 29
    out = snapshot_components(panel, as_of, ["roe"], max_age_days={"roe": 29})
    assert np.isnan(out.loc["AAAA3", "roe"])
    assert out.loc["BBBB3", "roe"] == 20.0
    out = snapshot_components(panel, as_of, ["roe"], max_age_days={"roe": 30})
    assert out.loc["AAAA3", "roe"] == 10.0


def test_age_is_counted_in_calendar_days_from_last_valid_print():
    panel = _panel(
        {
            ("AAAA3", "roe"): {"2024-01-01": 10.0, "2024-03-01": np.nan},
            ("AAAA3", "momentum_6m"): {"2024-03-01": 0.5},
        }
    )
    values, ages = last_values_with_age(panel, pd.Timestamp("2024-03-11"), ["roe", "momentum_6m"])
    assert values.loc["AAAA3", "roe"] == 10.0  # NaN posterior não apaga o último valor
    assert ages.loc["AAAA3", "roe"] == 70
    assert ages.loc["AAAA3", "momentum_6m"] == 10


def test_limits_apply_per_component():
    panel = _panel(
        {
            ("AAAA3", "roe"): {"2024-01-01": 10.0},
            ("AAAA3", "fcf_yield_fwd"): {"2024-01-01": 5.0},
        }
    )
    out = snapshot_components(
        panel, pd.Timestamp("2024-03-01"), ["roe", "fcf_yield_fwd"],
        max_age_days={"roe": 200, "fcf_yield_fwd": 30},
    )
    assert out.loc["AAAA3", "roe"] == 10.0
    assert np.isnan(out.loc["AAAA3", "fcf_yield_fwd"])


def test_component_without_limit_is_kept():
    panel = _panel({("AAAA3", "roe"): {"2020-01-01": 10.0}})
    out = snapshot_components(panel, pd.Timestamp("2024-01-01"), ["roe"], max_age_days={"other": 1})
    assert out.loc["AAAA3", "roe"] == 10.0


def test_ticker_that_never_printed_stays_nan():
    panel = _panel(
        {
            ("AAAA3", "roe"): {"2024-01-01": 10.0},
            ("BBBB3", "roe"): {"2024-01-01": np.nan},
        }
    )
    values, ages = last_values_with_age(panel, pd.Timestamp("2024-01-05"), ["roe"])
    assert np.isnan(values.loc["BBBB3", "roe"])
    assert np.isnan(ages.loc["BBBB3", "roe"])


def test_ignores_values_after_as_of():
    panel = _panel({("AAAA3", "roe"): {"2024-01-01": 10.0, "2024-02-01": 99.0}})
    out = snapshot_components(panel, pd.Timestamp("2024-01-15"), ["roe"])
    assert out.loc["AAAA3", "roe"] == 10.0


def test_without_limits_matches_legacy_ffill_snapshot():
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2023-01-02", periods=120)
    tickers = [f"T{i:03d}3" for i in range(8)]
    comps = ["roe", "momentum_6m", "fcf_yield_fwd"]
    cols = pd.MultiIndex.from_product([tickers, comps], names=["ticker", "descriptor"])
    data = rng.normal(size=(len(dates), len(cols)))
    data[rng.random(data.shape) < 0.7] = np.nan
    panel = pd.DataFrame(data, index=dates, columns=cols)
    for as_of in dates[[10, 60, 119]]:
        new = snapshot_components(panel, as_of, comps)
        old = _legacy_snapshot(panel, as_of, comps)
        pd.testing.assert_frame_equal(
            new.reindex(index=old.index), old, check_names=False, check_index_type=False
        )


def test_missing_component_is_omitted_and_empty_panel_is_empty():
    panel = _panel({("AAAA3", "roe"): {"2024-01-01": 1.0}})
    out = snapshot_components(panel, pd.Timestamp("2024-01-02"), ["roe", "absent"])
    assert list(out.columns) == ["roe"]
    assert snapshot_components(pd.DataFrame(), pd.Timestamp("2024-01-02"), ["roe"]).empty


def test_flat_columns_raise():
    panel = pd.DataFrame({"roe": [1.0]}, index=[pd.Timestamp("2024-01-01")])
    with pytest.raises(TypeError):
        snapshot_components(panel, pd.Timestamp("2024-01-02"), ["roe"])


def test_brap4_consensus_from_2021_is_dropped_in_2025():
    # Caso real: último fcf_yield_fwd da BRAP4 em 20/10/2021, ainda usado em jun/2025.
    panel = _panel(
        {
            ("BRAP4", "fcf_yield_fwd"): {"2021-10-20": -25.29},
            ("VALE3", "fcf_yield_fwd"): {"2025-06-27": 9.1},
        }
    )
    limits = get_component_max_age(["fcf_yield_fwd"], pd.DataFrame(columns=["Name", "Frequência"]))
    out = snapshot_components(panel, pd.Timestamp("2025-06-30"), ["fcf_yield_fwd"], limits)
    assert np.isnan(out.loc["BRAP4", "fcf_yield_fwd"])
    assert out.loc["VALE3", "fcf_yield_fwd"] == 9.1


# --------------------------------------------------------------------------- #
# Resolução da frequência
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "name, expected",
    [
        ("fcf_yield_fwd", "Consenso"),
        ("momentum_12m", "Diário"),
        ("delta_dollar_volume_21d_63d", "Diário"),
        ("median_dollar_volume_traded_21d", "Diário"),
        ("roe_trend_deviation", "Trimestral"),
        ("ebitda_margin_growth_1y", "Trimestral"),
        ("accruals_ratio_cf", "Trimestral"),
        ("book_yield_percentile_10y", None),
    ],
)
def test_infer_frequency(name, expected):
    assert infer_frequency(name) == expected


def test_fibery_frequency_wins_over_name_rule():
    defs = pd.DataFrame({"Name": ["momentum_6m"], "Frequência": ["Trimestral"]})
    assert get_component_max_age(["momentum_6m"], defs) == {"momentum_6m": 200}


def test_empty_fibery_frequency_falls_back_to_name_rule():
    defs = pd.DataFrame({"Name": ["fcf_yield_fwd", "beta"], "Frequência": [pd.NA, "Diário"]})
    assert get_component_max_age(["fcf_yield_fwd", "beta"], defs) == {
        "fcf_yield_fwd": 30,
        "beta": 10,
    }


def test_unresolved_component_uses_quarterly_and_warns(caplog):
    defs = pd.DataFrame({"Name": ["capex_to_sales"], "Frequência": [None]})
    with caplog.at_level(logging.WARNING):
        out = get_component_max_age(["capex_to_sales"], defs)
    assert out == {"capex_to_sales": DEFAULT_MAX_AGE_BY_FREQUENCY["Trimestral"]}
    assert "capex_to_sales" in caplog.text


def test_override_and_custom_frequency_limits():
    defs = pd.DataFrame({"Name": ["roe", "beta"], "Frequência": ["Trimestral", "Diário"]})
    out = get_component_max_age(
        ["roe", "beta"], defs,
        max_age_by_frequency={"Trimestral": 120},
        overrides={"beta": 3},
    )
    assert out == {"roe": 120, "beta": 3}


def test_definitions_without_frequency_column_use_name_rule():
    defs = pd.DataFrame({"Name": ["roe"]})
    assert get_component_max_age(["roe"], defs) == {"roe": 200}


# --------------------------------------------------------------------------- #
# BacktestConfig
# --------------------------------------------------------------------------- #

def test_config_merges_frequency_limits_with_defaults():
    cfg = BacktestConfig(
        start_date="2024-01-01", end_date="2024-12-31", components=["roe"],
        max_age_by_frequency={"Consenso": 45},
    )
    assert cfg.max_age_by_frequency == {**DEFAULT_MAX_AGE_BY_FREQUENCY, "Consenso": 45}
    assert cfg.component_max_age_days is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_age_by_frequency": {"Diário": -1}},
        {"max_age_by_frequency": {"Trimestral": COMPONENT_LOOKBACK_DAYS + 1}},
        {"component_max_age_days": {"roe": COMPONENT_LOOKBACK_DAYS + 1}},
    ],
)
def test_config_rejects_limits_outside_lookback(kwargs):
    with pytest.raises(ValueError):
        BacktestConfig(start_date="2024-01-01", end_date="2024-12-31", components=["roe"], **kwargs)


# --------------------------------------------------------------------------- #
# Engine: valores velhos viram NaN e entram no diagnóstico
# --------------------------------------------------------------------------- #

def test_rebalance_blanks_stale_values_and_reports_them():
    dates = pd.bdate_range("2024-01-01", "2024-03-29")
    tickers = [f"T{chr(65 + i)}AA3" for i in range(12)]
    prices = pd.DataFrame(100.0, index=dates, columns=tickers)
    adtv_field = "median_dollar_volume_traded_21d"
    adtv = pd.DataFrame(
        1e8, index=dates,
        columns=pd.MultiIndex.from_product([tickers, [adtv_field]], names=["ticker", "descriptor"]),
    )
    comp = {(t, "fcf_yield_fwd"): {"2024-03-28": float(i)} for i, t in enumerate(tickers)}
    comp[(tickers[0], "fcf_yield_fwd")] = {"2024-01-02": 99.0}  # 87 dias no rebalance
    comp_panel = _panel(comp)

    cfg = BacktestConfig(start_date="2024-01-01", end_date="2024-03-29", components=["fcf_yield_fwd"])
    scores, _, rebals, _ = _form_rebalance_books(
        cfg,
        rebalance_dates=pd.DatetimeIndex([pd.Timestamp("2024-03-29")]),
        adtv_panel=adtv,
        component_panel=comp_panel,
        prices=prices,
        components=("fcf_yield_fwd",),
        higher_is_better={},
        max_age_days={"fcf_yield_fwd": 30},
    )
    assert tickers[0] not in scores.columns
    assert rebals["stale__fcf_yield_fwd"].iloc[0] == 1
    summary = summarize_diagnostics(BacktestDiagnostics(rebals=rebals))
    assert summary["n_stale_values"] == 1


def _beta_neutral_books(max_age_days):
    dates = pd.bdate_range("2024-01-01", "2024-03-29")
    tickers = [f"T{chr(65 + i)}AA3" for i in range(12)]
    prices = pd.DataFrame(100.0, index=dates, columns=tickers)
    adtv_field = "median_dollar_volume_traded_21d"
    adtv = pd.DataFrame(
        1e8, index=dates,
        columns=pd.MultiIndex.from_product([tickers, [adtv_field]], names=["ticker", "descriptor"]),
    )
    comp_panel = _panel({(t, "momentum_6m"): {"2024-03-28": float(i)} for i, t in enumerate(tickers)})
    betas = {(t, "beta"): {"2024-03-28": 1.0} for t in tickers}
    betas[(tickers[-1], "beta")] = {"2024-01-02": 5.0}  # maior score (perna comprada), beta de 87 dias
    cfg = BacktestConfig(
        start_date="2024-01-01", end_date="2024-03-29", components=["momentum_6m"],
        construction="long_short", beta_neutral=True,
    )
    _, weights, rebals, _ = _form_rebalance_books(
        cfg,
        rebalance_dates=pd.DatetimeIndex([pd.Timestamp("2024-03-29")]),
        adtv_panel=adtv,
        component_panel=comp_panel,
        prices=prices,
        components=("momentum_6m",),
        higher_is_better={},
        max_age_days=max_age_days,
        beta_panel=_panel(betas),
    )
    return weights, rebals


def test_beta_neutral_ignores_stale_beta_and_reports_it():
    _, rebals = _beta_neutral_books({"momentum_6m": 10, "beta": 10})
    row = rebals.iloc[0]
    assert row["beta_long"] == pytest.approx(1.0)  # beta velho vira a mediana (1,0)
    assert row["gross_long"] == pytest.approx(1.0)
    assert row["n_stale_beta"] == 1


def test_beta_neutral_without_limit_keeps_old_beta():
    _, rebals = _beta_neutral_books(None)
    row = rebals.iloc[0]
    assert row["beta_long"] == pytest.approx(3.0)  # (5 + 1) / 2
    assert row["gross_long"] == pytest.approx(2 * 1.0 / (3.0 + 1.0))
    assert row["n_stale_beta"] == 0
