"""Undo a wrong ticker alias: drop it and restore the rows it archived.

``factor_zoo.aliases --apply`` archives the old code's rows to
``factor_zoo_removed`` (reason ``alias OLD->NEW``) and deletes them. This
script, in one transaction:

* deletes the ``factor_zoo_alias`` row for ``OLD``;
* re-inserts the archived ``factor_zoo`` / ``factor_zoo_latest`` rows under
  ``OLD`` (existing rows win) and removes them from ``factor_zoo_removed``.

Not undone: company-level fields copied to ``NEW`` where it had none, and
``b3_index_composition`` rows (reload the affected index windows with
``index_composition_update --start`` instead; the loader re-derives them
from Bloomberg).

Remove the pair from Fibery (``Códigos Anteriores`` of ``NEW`` in Ações
Ativas) first, or the next ``aliases`` run registers it again; the script
refuses to run while it is still there.

Usage::

    python scripts/factor_zoo_revert_alias.py AMBV3 ABEV3          # dry run: counts only
    python scripts/factor_zoo_revert_alias.py AMBV3 ABEV3 --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import sqlalchemy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from persevera_tools.data.factor_zoo.aliases import SEED, fibery_successions  # noqa: E402
from persevera_tools.db.connection import get_db_engine  # noqa: E402

_COUNTS = """
SELECT from_table, count(*) AS n, min(date) AS d0, max(date) AS d1
FROM factor_zoo_removed WHERE code = :old AND reason = :reason
GROUP BY from_table ORDER BY from_table
"""

_RESTORE = """
INSERT INTO {table} (code, date, field, value)
SELECT code, date, field, value FROM factor_zoo_removed
WHERE code = :old AND reason = :reason AND from_table = '{table}'
ON CONFLICT (code, date, field) DO NOTHING
"""

_UNARCHIVE = """
DELETE FROM factor_zoo_removed
WHERE code = :old AND reason = :reason AND from_table IN ('factor_zoo', 'factor_zoo_latest')
"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Undo a factor_zoo ticker alias.")
    p.add_argument("old_code")
    p.add_argument("new_code")
    p.add_argument("--apply", action="store_true", help="Write (default: dry run).")
    args = p.parse_args(argv)
    old, new = args.old_code, args.new_code
    params = {"old": old, "reason": f"alias {old}->{new}"}

    if (old, new) in SEED:
        print(f"{old}->{new} is in aliases.SEED; remove it from the code first.")
        return 1
    if (old, new) in fibery_successions():
        print(f"{old}->{new} is still in Fibery (Códigos Anteriores of {new}); remove it first.")
        return 1

    engine = get_db_engine()
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                alias = pd.read_sql_query(
                    sqlalchemy.text("SELECT * FROM factor_zoo_alias WHERE old_code = :old"), conn, params=params)
                print(alias.to_string(index=False) if len(alias) else f"No alias row for {old}.")
                print(pd.read_sql_query(sqlalchemy.text(_COUNTS), conn, params=params).to_string(index=False))
                if not args.apply:
                    trans.rollback()
                    print("Dry run: nothing written (use --apply).")
                    return 0
                dropped = conn.execute(
                    sqlalchemy.text("DELETE FROM factor_zoo_alias WHERE old_code = :old AND new_code = :new"),
                    {**params, "new": new}).rowcount
                restored = {t: conn.execute(sqlalchemy.text(_RESTORE.format(table=t)), params).rowcount
                            for t in ("factor_zoo", "factor_zoo_latest")}
                unarchived = conn.execute(sqlalchemy.text(_UNARCHIVE), params).rowcount
                trans.commit()
            except BaseException:
                if trans.is_active:
                    trans.rollback()
                raise
    finally:
        engine.dispose()
    print(f"alias rows deleted: {dropped}; restored: {restored}; removed from archive: {unarchived}")
    print("b3_index_composition is not restored: reload the affected index windows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
