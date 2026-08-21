#!/usr/bin/env python3
"""Migration runner.

    python3 migrate.py            # apply everything pending
    python3 migrate.py --status   # show applied/pending without writing

The app also calls database.init_db() on boot, so a deploy migrates itself.
This CLI exists for running migrations deliberately — before a restore, or when
you want to see what a pending file will do before it touches real data.
"""

import argparse
import sys

import database as db


def status():
    with db.connect() as conn:
        applied = db.applied_versions(conn)
    rows = db.discover_migrations()
    print(f'Database: {db.DB_PATH}')
    if not rows:
        print('No migrations found.')
        return 0
    pending = 0
    for version, name, _ in rows:
        mark = '✓' if version in applied else ' '
        if version not in applied:
            pending += 1
        print(f'  [{mark}] {version}_{name}')
    print(f'\n{len(rows) - pending} applied, {pending} pending.')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='Apply pending SQL migrations.')
    ap.add_argument('--status', action='store_true',
                    help='list applied and pending migrations, apply nothing')
    ap.add_argument('--db', help='override the database path')
    args = ap.parse_args(argv)

    if args.db:
        db.DB_PATH = args.db
    if args.status:
        return status()

    print(f'Migrating {db.DB_PATH}')
    applied = db.migrate(verbose=True)
    if applied:
        print(f'Applied {len(applied)} migration(s): {", ".join(applied)}')
    else:
        print('Already up to date.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
