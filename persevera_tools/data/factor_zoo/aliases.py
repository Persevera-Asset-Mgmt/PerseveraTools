"""Ticker successions in ``factor_zoo``: one security, one code.

When a company changes ticker (CCRO3 → MOTV3, TRPL4 → ISAE4, …) Bloomberg
chains the whole history into the new ticker. The old code keeps its own
copy, so backtests count the company twice. The successor is the canonical
code: old-code rows (from ``effective_date``) are archived and removed, and
company-level raw fields the successor lacks (e.g. pre-2020 fundamentals of a
recently renamed ticker) are first copied to the successor code (successor
wins on clashes). Listing-level fields (price, volume, market cap, …) are not
copied — the successor carries Bloomberg's chained series, possibly in another
currency — and derived fields are recomputed from the successor's inputs.

Tables
------
``factor_zoo_alias``
    ``old_code → new_code``. ``effective_date`` NULL means the successor
    covers the old code's whole life (all old rows are merged); otherwise
    only rows on/after that date are merged and the earlier stretch stays
    under the old code (avoids splicing two differently adjusted price series).
    Also used to translate historical references (portfolios, index
    composition) to the canonical code.
``factor_zoo_removed``
    Every row deleted from ``factor_zoo`` / ``factor_zoo_latest`` by this
    module, with the reason, so removals are auditable and reversible.

Sources: the seed below, and the Fibery relation ``Códigos Anteriores`` on
``Ações Ativas`` (successor → its previous tickers), synced on every run.

Examples::

    python -m persevera_tools.data.factor_zoo.aliases            # dry run
    python -m persevera_tools.data.factor_zoo.aliases --apply
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Iterable, Sequence

import pandas as pd
import sqlalchemy

from ...db.connection import get_db_engine
from ...db.fibery import read_fibery

logger = logging.getLogger(__name__)

_FIBERY_TABLE = "Inv-Rsrch-Quant/Ações Ativas"
_FIBERY_FIELD = "Códigos Anteriores"
_DEFINITIONS_TABLE = "Inv-Rsrch-Quant/Definições dos Fatores"
# Bloomberg categories describing the company rather than the listing.
_COMPANY_LEVEL_FREQUENCIES = frozenset({"Trimestral", "Consenso"})
_COMPANY_LEVEL_CATEGORIES = frozenset({"analyst_sentiment"})

# Successions found on 2026-10-06 (price series with a constant ratio and the
# same start date). Same-radical class conversions (WEGE4, LEVE4, ALLL11) are
# not here: they are distinct share classes, handled by one_class_per_issuer.
SEED: tuple[tuple[str, str], ...] = (
    ("BRHA4", "ABEV3"), ("VCPA4", "FIBR3"), ("MARI3", "AMAR3"), ("ACGU3", "TERI3"),
    ("UGPA4", "UGPA3"), ("KROT11", "COGN3"), ("GETI4", "TIET11"), ("RUMO3", "RAIL3"),
    ("SMLE3", "SMLS3"), ("TIET3", "AESB3"), ("WSON33", "PORT3"), ("OMGE3", "SRNA3"),
    ("BIDI11", "INBR32"), ("RRRP3", "BRAV3"), ("TRPL3", "ISAE3"), ("BAHI3", "BIED3"),
    ("TRPL4", "ISAE4"), ("NINJ3", "ARND3"), ("CCRO3", "MOTV3"), ("ATMP3", "CTAX3"),
    ("GOLL4", "GOLL54"), ("MBLY3", "TOKY3"), ("NTCO3", "NATU3"), ("MRFG3", "MBRF3"),
    ("ELET5", "AXIA5"), ("LVTC3", "WDCN3"), ("ERJ", "EMBJ"), ("EMBR3", "EMBJ3"),
    ("ELET3", "AXIA3"), ("ELET6", "AXIA6"), ("AZUL4", "AZUL54"), ("GUAR3", "RIAA3"),
    ("ODPV3", "SAUD3"), ("TRAD3", "ECOM3"),
    # Aura Minerals: Toronto line (CAD) superseded by the US line (USD).
    ("ORA", "AUGO"),
)

_DDL = """
CREATE TABLE IF NOT EXISTS factor_zoo_alias (
    old_code       TEXT PRIMARY KEY,
    new_code       TEXT NOT NULL,
    effective_date DATE,
    source         TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (old_code <> new_code)
);
CREATE TABLE IF NOT EXISTS factor_zoo_removed (
    code        TEXT NOT NULL,
    date        DATE NOT NULL,
    field       TEXT NOT NULL,
    value       DOUBLE PRECISION,
    from_table  TEXT NOT NULL,
    reason      TEXT NOT NULL,
    removed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# effective_date: NULL when the successor's history starts no later than the
# old code's (full chain), else the successor's first date.
_UPSERT_ALIAS = """
WITH bounds AS (
    SELECT
        (SELECT min(date) FROM factor_zoo WHERE code = :old AND field = 'price_close') AS old_first,
        (SELECT min(date) FROM factor_zoo WHERE code = :new AND field = 'price_close') AS new_first
)
INSERT INTO factor_zoo_alias (old_code, new_code, effective_date, source)
SELECT :old, :new,
       CASE WHEN new_first IS NULL OR old_first IS NULL OR new_first <= old_first
            THEN NULL ELSE new_first END,
       :source
FROM bounds
ON CONFLICT (old_code) DO NOTHING
"""

_PENDING = """
SELECT a.old_code, a.new_code, a.effective_date,
       z.field_rows AS rows_total,
       z.to_move    AS rows_copied_to_successor
FROM factor_zoo_alias a
CROSS JOIN LATERAL (
    SELECT count(*) AS field_rows,
           count(*) FILTER (WHERE z.field = ANY(:fields) AND NOT EXISTS (
               SELECT 1 FROM factor_zoo s
               WHERE s.code = a.new_code AND s.date = z.date AND s.field = z.field
           )) AS to_move
    FROM factor_zoo z
    WHERE z.code = a.old_code
      AND (a.effective_date IS NULL OR z.date >= a.effective_date)
) z
WHERE z.field_rows > 0
ORDER BY a.old_code
"""

_SCOPE = """
    FROM {table} z JOIN factor_zoo_alias a ON a.old_code = z.code
    WHERE a.old_code = :old
      AND (a.effective_date IS NULL OR z.date >= a.effective_date)
"""

_ARCHIVE = """
INSERT INTO factor_zoo_removed (code, date, field, value, from_table, reason)
SELECT z.code, z.date, z.field, z.value, '{table}', :reason
""" + _SCOPE

# Successor wins on (date, field) clashes.
_MOVE = """
INSERT INTO factor_zoo (code, date, field, value)
SELECT a.new_code, z.date, z.field, z.value
""" + _SCOPE.format(table="factor_zoo") + """
  AND z.field = ANY(:fields)
ON CONFLICT (code, date, field) DO NOTHING
"""

_DELETE = """
DELETE FROM {table} z
USING factor_zoo_alias a
WHERE a.old_code = :old
  AND z.code = a.old_code
  AND (a.effective_date IS NULL OR z.date >= a.effective_date)
"""


def company_level_fields() -> list[str]:
    """Raw mnemonics that describe the company (copied to the successor)."""
    df = read_fibery(table_name=_DEFINITIONS_TABLE)
    df = df[(df["state"] == "Ativo") & df["Código Bloomberg"].notna()]
    keep = df["Frequência"].isin(_COMPANY_LEVEL_FREQUENCIES) | df[
        "Categoria Independente"
    ].isin(_COMPANY_LEVEL_CATEGORIES)
    return sorted(df.loc[keep, "Name"].astype(str).unique())


def fibery_successions() -> list[tuple[str, str]]:
    """(old_code, new_code) pairs from ``Ações Ativas`` → ``Códigos Anteriores``."""
    df = read_fibery(table_name=_FIBERY_TABLE)
    if _FIBERY_FIELD not in df.columns:
        logger.warning("Fibery field %r not found; skipping sync", _FIBERY_FIELD)
        return []
    pairs = []
    for new_code, olds in zip(df["Name"], df[_FIBERY_FIELD]):
        if isinstance(olds, (list, tuple)):
            pairs += [(str(o).strip(), str(new_code)) for o in olds if str(o).strip()]
    return pairs


def register(conn, pairs: Iterable[tuple[str, str]], source: str) -> int:
    """Insert aliases not yet known (existing ones are never overwritten)."""
    n = 0
    for old, new in pairs:
        if old == new:
            continue
        n += conn.execute(
            sqlalchemy.text(_UPSERT_ALIAS), {"old": old, "new": new, "source": source}
        ).rowcount
    return n


def pending(conn, fields: Sequence[str]) -> pd.DataFrame:
    """Aliases that still have old-code rows in ``factor_zoo``."""
    return pd.read_sql_query(sqlalchemy.text(_PENDING), conn, params={"fields": list(fields)})


def apply(conn, todo: pd.DataFrame, fields: Sequence[str]) -> pd.DataFrame:
    """Archive old-code rows, copy company-level gaps to the successor, delete.

    ``factor_zoo_latest`` only loses the old code (it is rebuilt from
    ``factor_zoo``).
    """
    out = []
    for r in todo.itertuples(index=False):
        params = {
            "old": r.old_code,
            "reason": f"alias {r.old_code}->{r.new_code}",
            "fields": list(fields),
        }
        counts = {}
        for table in ("factor_zoo", "factor_zoo_latest"):
            conn.execute(sqlalchemy.text(_ARCHIVE.format(table=table)), params)
            if table == "factor_zoo":
                counts["copied"] = conn.execute(sqlalchemy.text(_MOVE), params).rowcount
            counts[f"deleted_{table}"] = conn.execute(
                sqlalchemy.text(_DELETE.format(table=table)), params
            ).rowcount
        logger.info("%s: %s", params["reason"], counts)
        out.append({"old_code": r.old_code, "new_code": r.new_code, **counts})
    return pd.DataFrame(out)


def run(*, do_apply: bool, use_seed: bool = True, use_fibery: bool = True) -> pd.DataFrame:
    """Sync aliases and (optionally) remove superseded rows, in one transaction."""
    fibery_pairs = fibery_successions() if use_fibery else []
    fields = company_level_fields()
    engine = get_db_engine()
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                for stmt in filter(str.strip, _DDL.split(";")):
                    conn.execute(sqlalchemy.text(stmt))
                added = 0
                if use_seed:
                    added += register(conn, SEED, "seed 2026-10-06")
                added += register(conn, fibery_pairs, "fibery")
                todo = pending(conn, fields)
                print(f"New aliases registered: {added}")
                print(f"Aliases with rows to merge: {len(todo)}")
                if len(todo):
                    print(todo[["rows_total", "rows_copied_to_successor"]]
                          .sum().map("{:,}".format).to_string())
                if len(todo):
                    print(todo.to_string(index=False))
                if not do_apply:
                    # Dry run: the alias registration is rolled back too.
                    trans.rollback()
                    print("Dry run: nothing written (use --apply).")
                    return todo
                done = apply(conn, todo, fields)
                trans.commit()
            except BaseException:
                if trans.is_active:
                    trans.rollback()
                raise
        print(done.to_string(index=False) if len(done) else "Nothing to remove.")
        return done
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    from persevera_tools.utils.logging import initialize as _initialize_logging

    _initialize_logging()
    p = argparse.ArgumentParser(description="Apply ticker successions to factor_zoo.")
    p.add_argument("--apply", action="store_true", help="Delete (after archiving). Default: dry run.")
    p.add_argument("--no-fibery", action="store_true", help="Skip the Fibery sync.")
    args = p.parse_args(argv)
    run(do_apply=args.apply, use_fibery=not args.no_fibery)
    return 0


if __name__ == "__main__":
    sys.exit(main())
