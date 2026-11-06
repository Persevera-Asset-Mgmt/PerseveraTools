"""Validação diária das rotinas agendadas, pelo que chegou no banco.

Os jobs rodam no PC Bloomberg e o log fica lá; o banco mostra o que de fato
foi gravado. Cada checagem lê uma tabela alimentada por uma rotina e devolve
``ok``, ``warn`` (aviso: algo a olhar) ou ``fail`` (a rotina não entregou).
Nada é escrito.

Pregão esperado: o dia útil ANBIMA anterior à data da checagem, menos os dias
sem fechamento do Ibovespa (fechamentos só da B3, como 24/12 e 31/12).

Exemplos::

    python -m persevera_tools.monitoring.daily_checks
    python -m persevera_tools.monitoring.daily_checks --date 2026-11-06 --json

Sai com código 1 quando alguma checagem falha.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Callable, Iterable, Mapping, Optional, Sequence

import pandas as pd

OK, WARN, FAIL = "ok", "warn", "fail"
_SEVERITY = {OK: 0, WARN: 1, FAIL: 2}
_LABEL = {OK: "OK   ", WARN: "AVISO", FAIL: "FALHA"}

# Índices carregados por index_composition_update (passo 4 do .bat diário).
INDICES = ("IBOV", "IBRA", "IBX100", "IBX50", "MLCX", "SMLL")

# Séries em ``indicadores`` -> pregões de defasagem aceitos além do esperado.
# IHFA: o dia mais recente fica fora até metade do peso ter cota publicada.
INDICADORES = {
    "persevera_anbima_debentures_spread_di": 0,
    "persevera_anbima_debentures_spread_ipca_incent": 0,
    "persevera_anbima_debentures_spread_ipca_nao_incent": 0,
    "persevera_anbima_debentures_duration_di": 0,
    "persevera_anbima_debentures_duration_ipca_incent": 0,
    "persevera_anbima_ihfa_long_short": 1,
}

# Campo derivado diário usado como prova de que o passo 3 (fatores) rodou.
DERIVED_DAILY_FIELD = "median_dollar_volume_traded_21d"

PRICE_JUMP = 0.5          # retorno diário acima de ±50%
COUNT_DROP_WARN = 0.9     # códigos no último pregão < 90% da mediana
BDI_PARTIAL = 0.25        # dia da B3 com < 25% das linhas da mediana
BDI_WINDOW = 30           # pregões olhados na carga da B3

Reader = Callable[[str, Optional[Mapping]], pd.DataFrame]


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    items: list = field(default_factory=list)


@dataclass
class Context:
    """Data da checagem, calendário e acesso ao banco (injetáveis nos testes)."""

    today: date
    holidays: frozenset
    read: Reader
    ibov_closes: frozenset = frozenset()
    active_codes: Callable[[], set] = field(default=lambda: set())

    def sessions(self, start: date, end: date) -> list:
        return sessions_between(start, end, self.holidays, self.ibov_closes)

    @property
    def expected(self) -> date:
        return previous_session(self.today, self.holidays, self.ibov_closes)

    def lag(self, last: Optional[date]) -> Optional[int]:
        """Pregões entre ``last`` (exclusive) e o pregão esperado (inclusive)."""
        if last is None:
            return None
        return len([d for d in self.sessions(last + timedelta(days=1), self.expected)])

    def window_start(self, n_sessions: int) -> date:
        """Data inicial que cobre os últimos ``n_sessions`` pregões até o esperado."""
        days = self.sessions(self.expected - timedelta(days=3 * n_sessions + 10), self.expected)
        return days[-n_sessions] if len(days) >= n_sessions else days[0]


# --------------------------------------------------------------------- calendário

def _as_date(value) -> date:
    return pd.Timestamp(value).date()


def sessions_between(start: date, end: date, holidays: Iterable, ibov_closes: Iterable = ()) -> list:
    """Dias úteis ANBIMA em ``[start, end]``.

    Com fechamentos do Ibovespa, tira os dias úteis sem fechamento, mas só até
    o último fechamento conhecido (o pregão mais recente pode ainda não ter
    sido carregado).
    """
    hol = {_as_date(h) for h in holidays}
    days = [d.date() for d in pd.bdate_range(start, end) if d.date() not in hol]
    closes = {_as_date(c) for c in ibov_closes}
    if closes:
        last_close = max(closes)
        days = [d for d in days if d > last_close or d in closes]
    return days


def previous_session(today: date, holidays: Iterable, ibov_closes: Iterable = ()) -> date:
    days = sessions_between(today - timedelta(days=15), today - timedelta(days=1), holidays, ibov_closes)
    return days[-1]


# ------------------------------------------------------------------- avaliações

def freshness(name: str, last: Optional[date], lag: Optional[int], max_lag: int = 0,
              what: str = "") -> CheckResult:
    if last is None:
        return CheckResult(name, FAIL, f"sem dados recentes{what}")
    msg = f"último dado em {last:%d/%m}{what}"
    if lag > max_lag:
        return CheckResult(name, FAIL, f"{msg}: {lag} pregão(ões) atrasado")
    return CheckResult(name, OK, msg)


def counts_by_session(df: pd.DataFrame, sessions: Sequence[date]) -> pd.Series:
    """``date, n`` -> série indexada pelos pregões (0 onde não há linhas)."""
    s = pd.Series(dict(zip(df["date"].map(_as_date), df["n"])), dtype="float64") if len(df) else pd.Series(dtype="float64")
    return s.reindex(list(sessions)).fillna(0)


def price_jumps(prices: pd.DataFrame, on: date, threshold: float = PRICE_JUMP) -> pd.DataFrame:
    """Retornos de ``price_close`` acima de ``threshold`` na data ``on``."""
    if prices.empty:
        return pd.DataFrame(columns=["code", "date", "prev", "value", "ret"])
    p = prices.assign(date=prices["date"].map(_as_date)).sort_values(["code", "date"])
    p["prev"] = p.groupby("code")["value"].shift()
    p = p[(p["date"] == on) & (p["prev"] > 0)]
    p = p.assign(ret=p["value"] / p["prev"] - 1)
    return p.loc[p["ret"].abs() > threshold, ["code", "date", "prev", "value", "ret"]].reset_index(drop=True)


# -------------------------------------------------------------------- checagens

def _daily_field(ctx: Context, name: str, field_name: str, what: str = "") -> CheckResult:
    """Último pregão de um campo diário e quantos códigos vieram nele."""
    sessions = ctx.sessions(ctx.window_start(10), ctx.expected)
    df = ctx.read(
        "SELECT date, count(*) AS n FROM factor_zoo "
        "WHERE field = :field AND date >= :since GROUP BY date",
        {"field": field_name, "since": sessions[0]},
    )
    counts = counts_by_session(df, sessions)
    filled = counts[counts > 0]
    last = filled.index[-1] if len(filled) else None
    res = freshness(name, last, ctx.lag(last), what=what)
    if res.status != OK:
        return res
    n_last = int(counts[last])
    previous = filled.iloc[:-1]
    median = float(previous.median()) if len(previous) else float(n_last)
    res.detail += f", {n_last} códigos (mediana {median:.0f})"
    if n_last < COUNT_DROP_WARN * median:
        res.status = WARN
        res.detail += ": menos códigos que o normal"
    return res


def check_factor_zoo_prices(ctx: Context) -> CheckResult:
    return _daily_field(ctx, "factor_zoo: preços (passo 1, company_data)", "price_close")


def check_factor_zoo_derived(ctx: Context) -> CheckResult:
    return _daily_field(ctx, "factor_zoo: fatores derivados (passo 3)", DERIVED_DAILY_FIELD,
                        what=f" ({DERIVED_DAILY_FIELD})")


def check_new_codes(ctx: Context) -> CheckResult:
    name = "factor_zoo: códigos de Ações Ativas sem histórico"
    codes = sorted(ctx.active_codes())
    if not codes:
        return CheckResult(name, WARN, "lista de Ações Ativas vazia (Fibery)")
    # NOT EXISTS por código usa o índice (code, field); um DISTINCT com ANY
    # varria o price_close de todas as partições (~100 s).
    df = ctx.read(
        "SELECT c AS code FROM unnest(CAST(:codes AS text[])) AS c "
        "WHERE NOT EXISTS (SELECT 1 FROM factor_zoo z WHERE z.code = c AND z.field = 'price_close')",
        {"codes": codes},
    )
    missing = sorted(df["code"])
    if missing:
        return CheckResult(name, WARN, f"{len(missing)} sem price_close: {', '.join(missing)}", missing)
    return CheckResult(name, OK, f"todos os {len(codes)} códigos têm histórico")


def check_pending_aliases(ctx: Context) -> CheckResult:
    name = "factor_zoo: aliases pendentes (passo 2)"
    # EXISTS em vez de count(*): contar as linhas levava ~3 min.
    df = ctx.read(
        """SELECT a.old_code, a.new_code,
               EXISTS (SELECT 1 FROM factor_zoo z WHERE z.code = a.old_code
                       AND z.date >= COALESCE(a.effective_date, DATE '1900-01-01')) AS factor_zoo,
               EXISTS (SELECT 1 FROM b3_index_composition c WHERE c.code = a.old_code
                       AND c.date >= COALESCE(a.effective_date, DATE '1900-01-01')) AS composicao
           FROM factor_zoo_alias a""",
        None,
    )
    todo = df[df["factor_zoo"].astype(bool) | df["composicao"].astype(bool)]
    if len(todo):
        items = [f"{r.old_code}->{r.new_code} ("
                 + ", ".join(t for t, flag in (("factor_zoo", r.factor_zoo), ("composição", r.composicao)) if flag)
                 + ")" for r in todo.itertuples()]
        return CheckResult(name, WARN, f"{len(todo)} alias(es) com linhas do código antigo: "
                           + "; ".join(items), items)
    return CheckResult(name, OK, f"{len(df)} aliases, nenhum pendente")


def check_price_jumps(ctx: Context) -> CheckResult:
    name = "factor_zoo: saltos de preço no último pregão"
    since = ctx.window_start(6)
    prices = ctx.read(
        "SELECT code, date, value FROM factor_zoo WHERE field = 'price_close' AND date >= :since",
        {"since": since},
    )
    if prices.empty:
        return CheckResult(name, WARN, "sem preços na janela")
    on = max(_as_date(d) for d in prices["date"])
    jumps = price_jumps(prices, on)
    if len(jumps):
        items = [f"{r.code} {r.prev:.2f}->{r.value:.2f} ({r.ret:+.0%})" for r in jumps.itertuples()]
        return CheckResult(name, WARN, f"{len(jumps)} retorno(s) acima de ±{PRICE_JUMP:.0%} em "
                           f"{on:%d/%m}: " + "; ".join(items), items)
    return CheckResult(name, OK, f"nenhum retorno acima de ±{PRICE_JUMP:.0%} em {on:%d/%m}")


def check_index_composition(ctx: Context) -> CheckResult:
    name = "b3_index_composition (passo 4)"
    df = ctx.read(
        "SELECT field AS indice, max(date) AS last FROM b3_index_composition "
        "WHERE date >= :since GROUP BY field",
        {"since": ctx.expected - timedelta(days=60)},
    )
    last = {r.indice: _as_date(r.last) for r in df.itertuples()}
    late = []
    for idx in INDICES:
        d = last.get(idx)
        lag = ctx.lag(d)
        if d is None:
            late.append(f"{idx} sem dados em 60 dias")
        elif lag > 0:
            late.append(f"{idx} em {d:%d/%m} ({lag} pregão(ões))")
    if late:
        return CheckResult(name, FAIL, "atrasado: " + "; ".join(late), late)
    return CheckResult(name, OK, f"{len(INDICES)} índices em {ctx.expected:%d/%m}")


def check_indicadores(ctx: Context) -> CheckResult:
    name = "indicadores: spreads, duration e IHFA"
    values = ", ".join(f"(:c{i})" for i in range(len(INDICADORES)))
    params = {f"c{i}": code for i, code in enumerate(INDICADORES)}
    df = ctx.read(
        f"SELECT v.code, (SELECT max(date) FROM indicadores i WHERE i.code = v.code) AS last "
        f"FROM (VALUES {values}) AS v(code)",
        params,
    )
    last = {r.code: (None if pd.isna(r.last) else _as_date(r.last)) for r in df.itertuples()}
    late = []
    for code, max_lag in INDICADORES.items():
        d = last.get(code)
        lag = ctx.lag(d)
        if d is None:
            late.append(f"{code} sem dados")
        elif lag > max_lag:
            late.append(f"{code} em {d:%d/%m} ({lag} pregão(ões))")
    if late:
        return CheckResult(name, FAIL, "atrasado: " + "; ".join(late), late)
    return CheckResult(name, OK, f"{len(INDICADORES)} séries em dia")


def check_b3_bdi(ctx: Context) -> CheckResult:
    name = "renda_fixa_historico: B3 BDI (source='b3')"
    sessions = ctx.sessions(ctx.window_start(BDI_WINDOW), ctx.expected)
    df = ctx.read(
        "SELECT date, count(*) AS n FROM renda_fixa_historico "
        "WHERE source = 'b3' AND date >= :since GROUP BY date",
        {"since": sessions[0]},
    )
    counts = counts_by_session(df, sessions)
    filled = counts[counts > 0]
    median = float(filled.median()) if len(filled) else 0.0
    missing = [d for d, n in counts.items() if n == 0]
    partial = [d for d, n in filled.items() if n < BDI_PARTIAL * median]
    items = [f"{d:%d/%m} sem dados" for d in missing] + \
            [f"{d:%d/%m} parcial ({int(counts[d])} linhas)" for d in partial]
    head = f"últimos {len(sessions)} pregões, mediana {median:.0f} linhas/dia"
    if ctx.expected in missing:
        return CheckResult(name, FAIL, f"{ctx.expected:%d/%m} sem dados; {head}: " + "; ".join(items), items)
    if items:
        return CheckResult(name, WARN, f"{head}: " + "; ".join(items), items)
    return CheckResult(name, OK, head)


CHECKS = (
    check_factor_zoo_prices,
    check_factor_zoo_derived,
    check_new_codes,
    check_pending_aliases,
    check_price_jumps,
    check_index_composition,
    check_indicadores,
    check_b3_bdi,
)


def run_checks(ctx: Context, checks: Sequence = CHECKS) -> list:
    """Roda cada checagem; um erro de consulta vira falha daquela checagem."""
    results = []
    for check in checks:
        try:
            results.append(check(ctx))
        except Exception as exc:  # noqa: BLE001 - uma checagem não derruba as outras
            results.append(CheckResult(check.__name__, FAIL, f"erro ao checar: {exc!r}"))
    return results


def overall(results: Sequence[CheckResult]) -> str:
    return max((r.status for r in results), key=_SEVERITY.__getitem__, default=OK)


def format_report(results: Sequence[CheckResult], ctx: Context) -> str:
    lines = [f"Validação das rotinas diárias em {ctx.today:%d/%m/%Y} "
             f"(pregão esperado {ctx.expected:%d/%m/%Y})", ""]
    lines += [f"[{_LABEL[r.status]}] {r.name}: {r.detail}" for r in results]
    n = {s: sum(r.status == s for r in results) for s in _SEVERITY}
    lines += ["", f"Resumo: {n[OK]} ok, {n[WARN]} aviso(s), {n[FAIL]} falha(s)"]
    return "\n".join(lines)


# ------------------------------------------------------------------------- CLI

def _db_context(today: date) -> Context:
    from persevera_tools.data.lookups import get_securities_by_exchange
    from persevera_tools.db.operations import read_sql
    from persevera_tools.utils.dates import get_holidays

    def read(sql: str, params: Optional[Mapping] = None) -> pd.DataFrame:
        return read_sql(sql, params=dict(params) if params else None, raise_errors=True)

    ibov = read(
        "SELECT date FROM indicadores WHERE code = 'br_ibovespa' AND field = 'close' AND date >= :since",
        {"since": today - timedelta(days=120)},
    )
    return Context(
        today=today,
        holidays=frozenset(_as_date(h) for h in get_holidays()),
        read=read,
        ibov_closes=frozenset(_as_date(d) for d in ibov["date"]),
        active_codes=lambda: set(get_securities_by_exchange().values()),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--date", help="Data da checagem (AAAA-MM-DD); padrão: hoje.")
    p.add_argument("--json", action="store_true", help="Saída em JSON.")
    args = p.parse_args(argv)

    # Console do Windows em cp1252: sem isso os acentos saem trocados num pipe.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.getLogger("persevera_tools").setLevel(logging.WARNING)
    today = date.fromisoformat(args.date) if args.date else date.today()
    ctx = _db_context(today)
    results = run_checks(ctx)
    status = overall(results)
    if args.json:
        print(json.dumps({"date": str(today), "expected_session": str(ctx.expected), "status": status,
                          "checks": [asdict(r) for r in results]}, ensure_ascii=False, indent=2))
    else:
        print(format_report(results, ctx))
    return 1 if status == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
