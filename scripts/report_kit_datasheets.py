#!/usr/bin/env python3
"""What migration 008 could and could not work out on its own.

    python3 scripts/report_kit_datasheets.py

Reads only. The migration seeds `kit_datasheets` from two sources that are both
recorded fact — a template's researched contents, and the units actually in the
box — and backfills `models.datasheet_id` from each model's unit. Neither step
guesses, so anything it could not answer is left blank rather than filled in
wrongly, and this is the list of blanks.

Four things worth looking at, in the order they cost you something:

  Kits with no datasheets at all   Nothing known about what is in the box.
                                   Allocation can never offer these as
                                   buildable, so a real box sits invisible.
  Models with no datasheet         Should be none. Every model has a unit and
                                   every unit has a datasheet, so a row here
                                   means something bypassed that.
  Kits that build more than one    Where the assembly picker will appear.
                                   Not a problem — just where the decisions are.
  Magnetised models                What you have told the app is swappable.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402


def unmapped_kits(conn):
    """Owned kits with nothing in `kit_datasheets`.

    A disposed kit is excluded: it is out of the collection, so allocation will
    never look at it and mapping it now would be busywork.
    """
    return conn.execute("""
        SELECT k.id, k.name, k.status, k.kit_template_id,
               (SELECT COUNT(*) FROM units u WHERE u.kit_id = k.id) AS unit_count
          FROM kits k
         WHERE k.status IN ('owned', 'listed')
           AND NOT EXISTS (SELECT 1 FROM kit_datasheets kd WHERE kd.kit_id = k.id)
         ORDER BY k.name
    """).fetchall()


def multi_datasheet_kits(conn):
    """Kits that can become more than one thing — where the picker fires."""
    return conn.execute("""
        SELECT k.id, k.name, COUNT(*) AS n,
               GROUP_CONCAT(d.name, ', ') AS sheets
          FROM kit_datasheets kd
          JOIN kits k      ON k.id = kd.kit_id
          JOIN datasheets d ON d.id = kd.datasheet_id
         WHERE k.status IN ('owned', 'listed')
         GROUP BY kd.kit_id
        HAVING COUNT(*) > 1
         ORDER BY n DESC, k.name
    """).fetchall()


def uncommitted_models(conn):
    """Models with no datasheet, grouped by the unit they sit in."""
    return conn.execute("""
        SELECT u.id AS unit_id, u.datasheet_id AS unit_datasheet_id,
               d.name AS unit_datasheet, COUNT(m.id) AS n
          FROM models m
          JOIN units u       ON u.id = m.unit_id
          LEFT JOIN datasheets d ON d.id = u.datasheet_id
         WHERE m.datasheet_id IS NULL
         GROUP BY m.unit_id
         ORDER BY n DESC
    """).fetchall()


def flexible_models(conn):
    return conn.execute("""
        SELECT d.name, COUNT(*) AS n
          FROM models m
          LEFT JOIN datasheets d ON d.id = m.datasheet_id
         WHERE m.is_flexible = 1
         GROUP BY m.datasheet_id
         ORDER BY n DESC, d.name
    """).fetchall()


def report(conn):
    """Print the four sections. Returns 1 if anything needs Clay, else 0."""
    needs_attention = 0

    total_models = conn.execute('SELECT COUNT(*) FROM models').fetchone()[0]
    committed = conn.execute(
        'SELECT COUNT(*) FROM models WHERE datasheet_id IS NOT NULL').fetchone()[0]
    print(f'{committed} of {total_models} models carry a datasheet.')
    print(f'{conn.execute("SELECT COUNT(*) FROM kit_datasheets").fetchone()[0]} '
          'kit → datasheet links.\n')

    rows = unmapped_kits(conn)
    if rows:
        needs_attention = 1
        print(f'Kits with no datasheets ({len(rows)}) — nothing known about '
              'what is inside:')
        for r in rows:
            why = ('no template and no units' if not r['kit_template_id']
                   else 'template has no contents defined')
            print(f'  [{r["id"]:>4}] {r["name"]} — {why}')
        print('  Fix by defining the template contents, or by adding the units '
              'the box holds.\n')

    rows = uncommitted_models(conn)
    if rows:
        needs_attention = 1
        total = sum(r['n'] for r in rows)
        print(f'Models with no datasheet ({total}) — the backfill should have '
              'left none:')
        for r in rows:
            print(f'  unit {r["unit_id"]}: {r["n"]} × '
                  f'{r["unit_datasheet"] or "unknown datasheet"}')
        print()

    rows = multi_datasheet_kits(conn)
    if rows:
        print(f'Kits that build more than one datasheet ({len(rows)}) — this is '
              'where the assembly picker appears:')
        for r in rows:
            print(f'  [{r["id"]:>4}] {r["name"]} → {r["n"]}: {r["sheets"]}')
        print()

    rows = flexible_models(conn)
    if rows:
        print(f'Magnetised models ({sum(r["n"] for r in rows)}):')
        for r in rows:
            print(f'  {r["n"]:>3} × {r["name"] or "no datasheet"}')
        print()

    if not needs_attention:
        print('Nothing needs filling in.')
    return needs_attention


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Report what migration 008 could not map on its own.')
    ap.add_argument('--db', help='override the database path')
    args = ap.parse_args(argv)
    if args.db:
        db.DB_PATH = args.db
    with db.connect() as conn:
        return report(conn)


if __name__ == '__main__':
    sys.exit(main())
