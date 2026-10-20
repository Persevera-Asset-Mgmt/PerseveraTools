"""Testes offline de fixed_income.metrics (sem banco)."""

import numpy as np
import pandas as pd
import pytest

import persevera_tools.fixed_income.metrics as M


# --------------------------------------------------------------------------- #
# _macaulay_duration: taxas efetivas
# --------------------------------------------------------------------------- #

def test_zero_coupon_duration_is_time_to_maturity():
    assert M._macaulay_duration(0.12, 0.0, 3.4) == 3.4


def test_annual_par_bond_matches_closed_form():
    y, T = 0.10, 5
    expected = (1 + y) / y * (1 - (1 + y) ** -T)
    assert M._macaulay_duration(y, y, T, 1) == pytest.approx(expected)


def test_semiannual_coupon_is_effective_rate():
    # NTN-B: 6% a.a. efetivo, cupom semestral (1.06)^0.5 - 1; a 6% de taxa o título vale par
    y = c = 0.06
    T = 3.0
    times = T - np.arange(6) / 2
    cpn = 1.06 ** 0.5 - 1
    cf = np.full(6, cpn)
    cf[0] += 1
    pv = cf / (1 + y) ** times
    assert pv.sum() == pytest.approx(1.0)
    assert M._macaulay_duration(y, c, T, 2) == pytest.approx((times * pv).sum() / pv.sum())


# --------------------------------------------------------------------------- #
# _trim_mask
# --------------------------------------------------------------------------- #

def test_trim_mask_drops_floor_n_trim_each_tail():
    vals = pd.DataFrame([[1, 2, 3, 4, 5, 6, 7, 8, 9, 1000.0],
                         [1, 2, 3, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan]])
    kept = M._trim_mask(vals, 0.10)
    assert kept.iloc[0].tolist() == [False] + [True] * 8 + [False]
    assert kept.iloc[1, :3].tolist() == [True, True, True]  # floor(3 * 0.1) = 0
    assert vals.where(kept).mean(axis=1).iloc[0] == pytest.approx(5.5)


# --------------------------------------------------------------------------- #
# calculate_duration
# --------------------------------------------------------------------------- #

@pytest.fixture
def fake_db(monkeypatch):
    dates = pd.bdate_range("2026-09-28", "2026-10-09")

    def fake_fetch(codes, field, end_date_str):
        data = {
            "yield_to_maturity": {"DEB1": pd.Series(1.5, index=dates),
                                  "OLD1": pd.Series(7.0, index=dates[:1] - pd.Timedelta(days=400))},
            "duration": {"DEB1": pd.Series(504.0, index=dates),
                         "OLD1": pd.Series(252.0, index=dates[:1] - pd.Timedelta(days=400))},
        }[field]
        return {c: data[c] for c in codes if c in data}

    monkeypatch.setattr(M, "_fetch_anbima_field", fake_fetch)
    monkeypatch.setattr(M, "get_holidays", lambda: [])
    monkeypatch.setattr(M, "get_indicator_series",
                        lambda code, end_date=None: pd.Series(13.65, index=dates))
    return dates


def test_anbima_duration_is_converted_to_years_and_flagged_when_stale(fake_db):
    df = M.calculate_duration(["DEB1", "OLD1"], settlement_date="2026-10-09")
    assert df.loc["DEB1", "macaulay_duration"] == pytest.approx(2.0)
    assert df.loc["OLD1", "macaulay_duration"] == pytest.approx(1.0)
    assert df.loc["DEB1", "stale"] is False or not df.loc["DEB1", "stale"]
    assert bool(df.loc["OLD1", "stale"])


def test_di_fallback_discounts_at_cdi_plus_spread(fake_db):
    r = M.calculate_duration("DEB1", settlement_date="2026-10-09", use_anbima=False,
                             maturity_date="2031-10-09", indice="DI")
    full = 1.1365 * 1.015 - 1
    assert r["cdi"] == pytest.approx(0.1365)
    assert r["ytm"] == pytest.approx(0.015)
    assert r["coupon_rate"] == pytest.approx(full)
    assert r["macaulay_duration"] == pytest.approx(
        M._macaulay_duration(full, full, r["years_to_maturity"], 1))
    assert r["macaulay_duration"] < 4.5  # com 1.5% (só o spread) daria ~4.9


def test_non_di_uses_ytm_as_full_rate_and_du252(fake_db):
    r = M.calculate_duration("X", settlement_date="2026-10-09", ytm=0.07, coupon_rate=0.0,
                             maturity_date="2027-10-08", indice="IPCA")
    assert r["cdi"] is None
    days = np.busday_count(np.datetime64("2026-10-10"), np.datetime64("2027-10-09"))
    assert r["years_to_maturity"] == pytest.approx(days / 252)
    assert r["macaulay_duration"] == r["years_to_maturity"]


# --------------------------------------------------------------------------- #
# Blocos puros do spread (sem banco)
# --------------------------------------------------------------------------- #

DIAS = pd.bdate_range("2026-10-01", periods=8)


def test_spread_ipca_is_rate_minus_ntnb_at_reference_maturity():
    venc_a, venc_b = pd.Timestamp("2030-08-15"), pd.Timestamp("2035-05-15")
    ytm = pd.DataFrame({"DEB1": 8.0, "CRI1": 9.0}, index=DIAS)
    refs = pd.DataFrame({"date": [DIAS[0], DIAS[0]], "code": ["DEB1", "CRI1"],
                         "reference": [venc_a, venc_b]})
    curva = pd.DataFrame({venc_a: 7.0, venc_b: 7.5}, index=DIAS)
    s = M.spread_ipca_panel(ytm, refs, curva)
    # A referência do 1º dia vale por REFERENCE_FFILL_LIMIT datas, depois some.
    lim = M.REFERENCE_FFILL_LIMIT
    assert s["DEB1"].iloc[: lim + 1].tolist() == [1.0] * (lim + 1)
    assert s["CRI1"].iloc[: lim + 1].tolist() == [1.5] * (lim + 1)
    assert s["DEB1"].iloc[lim + 1 :].isna().all()


def test_spread_ipca_masks_zero_rates_and_never_backfills_reference():
    venc = pd.Timestamp("2030-08-15")
    ytm = pd.DataFrame({"DEB1": [8.0, 0.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0]}, index=DIAS)
    refs = pd.DataFrame({"date": [DIAS[2]], "code": ["DEB1"], "reference": [venc]})
    curva = pd.DataFrame({venc: 7.0}, index=DIAS)
    s = M.spread_ipca_panel(ytm, refs, curva)["DEB1"]
    assert s.iloc[:2].isna().all()  # antes da 1ª referência: sem spread (sem back-fill)
    assert s.iloc[2] == 1.0


def test_spread_di_only_fills_inside_gaps():
    ytm = pd.DataFrame({"DEB1": [1.0, np.nan, 3.0, np.nan]}, index=DIAS[:4])
    assert M.spread_di_panel(ytm)["DEB1"].tolist()[:3] == [1.0, 2.0, 3.0]
    assert np.isnan(M.spread_di_panel(ytm)["DEB1"].iloc[3])


def test_outstanding_volume_is_nan_for_codes_outside_emissions():
    like = pd.DataFrame({"DEB1": 1.0, "CRA1": 1.0}, index=DIAS[:2])
    emis = pd.DataFrame({"code": ["DEB1"], "valor_nominal_na_emissao": [1000.0],
                         "quantidade_emitida": [10.0]})
    par = pd.DataFrame({"DEB1": [900.0, np.nan]}, index=DIAS[:2])
    vol = M.outstanding_volume(par, emis, like)
    assert vol["DEB1"].tolist() == [9000.0, 9000.0]  # par ffill
    assert vol["CRA1"].isna().all()
    vol0 = M.outstanding_volume(pd.DataFrame(), emis, like)
    assert vol0["DEB1"].tolist() == [10000.0, 10000.0]  # sem par: valor nominal na emissão


def test_cross_section_stats_weights_only_assets_with_volume():
    serie = pd.DataFrame({"DEB1": [1.0], "DEB2": [3.0], "CRA1": [5.0]}, index=DIAS[:1])
    vol = pd.DataFrame({"DEB1": [100.0], "DEB2": [300.0], "CRA1": [np.nan]}, index=DIAS[:1])
    st = M.cross_section_stats(serie, vol, calculate_distribution=True)
    assert st["median"].iloc[0] == 3.0
    assert st["mean"].iloc[0] == 3.0  # 3 ativos: floor(3 * 10%) = 0, nada cortado
    assert st["weighted_mean"].iloc[0] == pytest.approx((1 * 100 + 3 * 300) / 400)
    assert st["count_above_mean"].iloc[0] == 1
    assert st["volume_above_mean"].iloc[0] == 0.0  # o acima da média (CRA1) não tem volume
    assert st["count_yield_above_250bp"].iloc[0] == 2
    sem_vol = M.cross_section_stats(serie)
    assert np.isnan(sem_vol["weighted_mean"].iloc[0]) and sem_vol["median"].iloc[0] == 3.0


def test_cross_section_stats_treats_zero_as_missing():
    serie = pd.DataFrame({"A": [0.0], "B": [2.0]}, index=DIAS[:1])
    assert M.cross_section_stats(serie)["median"].iloc[0] == 2.0


@pytest.fixture
def fake_spread_db(monkeypatch):
    emis = pd.DataFrame({
        "code": ["DEB1", "DEB2", "DEB3", "PCT1"],
        "valor_nominal_na_emissao": [1000.0] * 4,
        "quantidade_emitida": [10.0, 20.0, 30.0, 40.0],
        "percentual_multiplicador_rentabilidade": [100, 100, 100, 110],
    })
    taxas = pd.DataFrame({"DEB1": 1.0, "DEB2": 2.0, "DEB3": 4.0, "PCT1": 105.0}, index=DIAS)

    def fake_series(code=None, source=None, category=None, start_date=None, end_date=None, field=None):
        if field == "par_value":
            raise ValueError("sem par")
        return taxas[code]

    monkeypatch.setattr(M, "get_emissions", lambda index_code=None, deb_incent_lei_12431=None: emis)
    monkeypatch.setattr(M, "get_series", fake_series)
    return emis, taxas


def test_calculate_spread_equals_pure_stats_over_the_panel(fake_spread_db):
    emis, _ = fake_spread_db
    painel = M.calculate_spread_panel("DI")
    assert list(painel.columns) == ["DEB1", "DEB2", "DEB3"]  # DI: só 100% do CDI
    vol = M.outstanding_volume(pd.DataFrame(), emis, painel)
    esperado = M.cross_section_stats(painel, vol, calculate_distribution=True)
    pd.testing.assert_frame_equal(M.calculate_spread("DI", calculate_distribution=True), esperado)


def test_calculate_spread_restricted_to_codes(fake_spread_db):
    st = M.calculate_spread("DI", codes=["deb1", "DEB3", "PCT1", "NAO_EXISTE"])
    assert st["median"].iloc[0] == 2.5  # DEB1 e DEB3; PCT1 fora do universo DI
    assert st["weighted_mean"].iloc[0] == pytest.approx((1 * 10 + 4 * 30) / 40)
    assert list(M.calculate_spread_panel("DI", codes=["DEB2"]).columns) == ["DEB2"]
