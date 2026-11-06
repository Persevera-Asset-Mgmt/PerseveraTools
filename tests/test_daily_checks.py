"""Testes offline da validação diária (monitoring.daily_checks, sem banco)."""

from datetime import date, timedelta

import pandas as pd
import pytest

from persevera_tools.monitoring import daily_checks as dc

HOLIDAYS = frozenset({date(2026, 11, 2), date(2026, 11, 20)})
TODAY = date(2026, 11, 6)  # sexta; pregão esperado: quinta 05/11


def _sessions(start, end):
    return dc.sessions_between(start, end, HOLIDAYS)


def _counts(days, n):
    return pd.DataFrame({"date": list(days), "n": n if isinstance(n, list) else [n] * len(days)})


class FakeDB:
    """Responde cada consulta pela tabela/forma do SQL."""

    def __init__(self, **tables):
        self.tables = tables
        self.calls = []

    def __call__(self, sql, params=None):
        self.calls.append((sql, params))
        for key in ("factor_zoo_alias", "unnest", "renda_fixa_historico", "b3_index_composition",
                    "indicadores", "SELECT code, date, value", "field = :field"):
            if key in sql:
                value = self.tables[key]
                return value(params) if callable(value) else value
        raise AssertionError(f"consulta inesperada: {sql}")


def _ctx(read, today=TODAY, **kw):
    return dc.Context(today=today, holidays=HOLIDAYS, read=read, **kw)


# ----------------------------------------------------------------- calendário

def test_previous_session_skips_weekend_and_holiday():
    assert dc.previous_session(date(2026, 11, 3), HOLIDAYS) == date(2026, 10, 30)
    assert dc.previous_session(date(2026, 11, 9), HOLIDAYS) == date(2026, 11, 6)


def test_sessions_drop_days_without_ibov_close_only_up_to_last_close():
    closes = {date(2026, 12, 22), date(2026, 12, 23)}  # 24/12 sem pregão na B3
    days = dc.sessions_between(date(2026, 12, 22), date(2026, 12, 28), HOLIDAYS, closes | {date(2026, 12, 28)})
    assert date(2026, 12, 24) not in days
    # Depois do último fechamento conhecido o dia útil continua esperado.
    days = dc.sessions_between(date(2026, 12, 22), date(2026, 12, 28), HOLIDAYS, closes)
    assert date(2026, 12, 24) in days and days[-1] == date(2026, 12, 28)


def test_lag_counts_sessions_after_last_date():
    ctx = _ctx(FakeDB())
    assert ctx.expected == date(2026, 11, 5)
    assert ctx.lag(date(2026, 11, 5)) == 0
    assert ctx.lag(date(2026, 10, 30)) == 3  # 03, 04 e 05/11 (02/11 é feriado)
    assert ctx.lag(None) is None


# ------------------------------------------------------------- factor_zoo

def test_prices_ok_with_usual_count():
    days = _sessions(date(2026, 10, 20), date(2026, 11, 5))
    ctx = _ctx(FakeDB(**{"field = :field": _counts(days, 350)}))
    res = dc.check_factor_zoo_prices(ctx)
    assert res.status == dc.OK and "05/11" in res.detail


def test_prices_fail_when_last_session_missing():
    days = _sessions(date(2026, 10, 20), date(2026, 11, 3))
    res = dc.check_factor_zoo_prices(_ctx(FakeDB(**{"field = :field": _counts(days, 350)})))
    assert res.status == dc.FAIL and "2 pregão" in res.detail


def test_prices_warn_on_count_drop():
    days = _sessions(date(2026, 10, 20), date(2026, 11, 5))
    n = [350] * (len(days) - 1) + [200]
    res = dc.check_factor_zoo_prices(_ctx(FakeDB(**{"field = :field": _counts(days, n)})))
    assert res.status == dc.WARN


def test_derived_reads_daily_field():
    days = _sessions(date(2026, 10, 20), date(2026, 11, 5))
    db = FakeDB(**{"field = :field": _counts(days, 390)})
    assert dc.check_factor_zoo_derived(_ctx(db)).status == dc.OK
    assert db.calls[0][1]["field"] == dc.DERIVED_DAILY_FIELD


def test_new_codes_warns_with_missing_list():
    db = FakeDB(unnest=pd.DataFrame({"code": ["RAIZ55"]}))
    res = dc.check_new_codes(_ctx(db, active_codes=lambda: {"PETR4", "RAIZ55"}))
    assert res.status == dc.WARN and res.items == ["RAIZ55"]
    assert sorted(db.calls[0][1]["codes"]) == ["PETR4", "RAIZ55"]


def test_new_codes_ok():
    db = FakeDB(unnest=pd.DataFrame({"code": []}))
    assert dc.check_new_codes(_ctx(db, active_codes=lambda: {"PETR4"})).status == dc.OK


def test_pending_aliases():
    df = pd.DataFrame({"old_code": ["RAIZ4", "GOLL4"], "new_code": ["RAIZ55", "GOLL54"],
                       "factor_zoo": [True, False], "composicao": [False, False]})
    res = dc.check_pending_aliases(_ctx(FakeDB(factor_zoo_alias=df)))
    assert res.status == dc.WARN and res.items == ["RAIZ4->RAIZ55 (factor_zoo)"]


def test_price_jumps_only_on_given_date():
    prices = pd.DataFrame({
        "code": ["A", "A", "A", "B", "B"],
        "date": [date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 4), date(2026, 11, 5)],
        "value": [10.0, 30.0, 31.0, 10.0, 4.0],
    })
    jumps = dc.price_jumps(prices, date(2026, 11, 5))
    assert jumps["code"].tolist() == ["B"]
    assert jumps["ret"].iloc[0] == pytest.approx(-0.6)


def test_check_price_jumps_warns():
    prices = pd.DataFrame({"code": ["B", "B"], "date": [date(2026, 11, 4), date(2026, 11, 5)],
                           "value": [10.0, 25.0]})
    res = dc.check_price_jumps(_ctx(FakeDB(**{"SELECT code, date, value": prices})))
    assert res.status == dc.WARN and res.items[0].startswith("B 10.00->25.00")


# ------------------------------------------------- composição e indicadores

def test_index_composition_fail_lists_late_indices():
    last = {i: date(2026, 11, 5) for i in dc.INDICES}
    last["SMLL"] = date(2026, 10, 30)
    df = pd.DataFrame({"indice": list(last), "last": list(last.values())})
    res = dc.check_index_composition(_ctx(FakeDB(b3_index_composition=df)))
    assert res.status == dc.FAIL and res.items == ["SMLL em 30/10 (3 pregão(ões))"]


def test_index_composition_missing_index_fails():
    df = pd.DataFrame({"indice": ["IBOV"], "last": [date(2026, 11, 5)]})
    res = dc.check_index_composition(_ctx(FakeDB(b3_index_composition=df)))
    assert res.status == dc.FAIL and "SMLL sem dados em 60 dias" in res.items


def test_indicadores_allow_ihfa_one_session_late():
    last = {code: date(2026, 11, 5) for code in dc.INDICADORES}
    last["persevera_anbima_ihfa_long_short"] = date(2026, 11, 4)
    df = pd.DataFrame({"code": list(last), "last": list(last.values())})
    assert dc.check_indicadores(_ctx(FakeDB(indicadores=df))).status == dc.OK

    last["persevera_anbima_ihfa_long_short"] = date(2026, 11, 3)
    last["persevera_anbima_debentures_spread_di"] = None
    df = pd.DataFrame({"code": list(last), "last": list(last.values())})
    res = dc.check_indicadores(_ctx(FakeDB(indicadores=df)))
    assert res.status == dc.FAIL and len(res.items) == 2


# ---------------------------------------------------------------- B3 BDI

def _bdi(days, n):
    return FakeDB(renda_fixa_historico=_counts(days, n))


def test_bdi_fail_when_expected_session_missing():
    days = _sessions(date(2026, 9, 1), date(2026, 11, 4))
    res = dc.check_b3_bdi(_ctx(_bdi(days, 100_000)))
    assert res.status == dc.FAIL and res.items == ["05/11 sem dados"]


def test_bdi_warn_on_old_gap_and_partial_day():
    days = [d for d in _sessions(date(2026, 9, 1), date(2026, 11, 5)) if d != date(2026, 10, 15)]
    n = [100_000] * len(days)
    n[days.index(date(2026, 10, 30))] = 5_000
    res = dc.check_b3_bdi(_ctx(_bdi(days, n)))
    assert res.status == dc.WARN
    assert res.items == ["15/10 sem dados", "30/10 parcial (5000 linhas)"]


def test_bdi_ok_and_window_has_30_sessions():
    days = _sessions(date(2026, 9, 1), date(2026, 11, 5))
    db = _bdi(days, 100_000)
    res = dc.check_b3_bdi(_ctx(db))
    assert res.status == dc.OK
    since = db.calls[0][1]["since"]
    assert len(_sessions(since, date(2026, 11, 5))) == dc.BDI_WINDOW


# ------------------------------------------------------------- execução

def test_run_checks_turns_errors_into_failures():
    def broken(ctx):
        raise RuntimeError("sem conexão")

    def fine(ctx):
        return dc.CheckResult("fine", dc.OK, "ok")

    results = dc.run_checks(_ctx(FakeDB()), checks=(fine, broken))
    assert [r.status for r in results] == [dc.OK, dc.FAIL]
    assert "sem conexão" in results[1].detail
    assert dc.overall(results) == dc.FAIL


def test_report_summary_line():
    results = [dc.CheckResult("a", dc.OK, "x"), dc.CheckResult("b", dc.WARN, "y")]
    text = dc.format_report(results, _ctx(FakeDB()))
    assert "pregão esperado 05/11/2026" in text
    assert text.splitlines()[-1] == "Resumo: 1 ok, 1 aviso(s), 0 falha(s)"
    assert dc.overall(results) == dc.WARN
