#!/usr/bin/env python3
"""Has the rules data aged? Compare each pin against its upstream.

    python3 scripts/check_rules_pins.py

Exits 1 when there is something to **take** — not merely when a repository has
moved. Those are different, and conflating them made this cry wolf. Measured
2026-08-26: the MFM pin had moved on a `chore(deps)` CI bump with the points
files byte-identical, and BSData had moved by 35 genuine data commits that
changed exactly two rows of what this app imports, both keyword-only. A weekly
alarm for either is a nag, and a nag becomes wallpaper.

Nothing here bumps a pin. Points changing under a list is exactly the kind of
thing Clay should decide to accept rather than wake up to: a balance dataslate
can move a unit twenty points, and a list that was legal on Saturday quietly
becoming illegal on Monday is worse than being told and choosing when.

Bumping one is: edit the SHA in `rules_data.py`, re-fetch, re-import, and for
the MFM also replace the vendored files in `data/mfm/` and update
`data/SOURCES.md`. The importer reports `manual_override` rows and leaves them
alone, so corrections survive a re-sync.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db      # noqa: E402
import rules_data          # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--timeout', type=int, default=60,
                    help='seconds to wait on each ls-remote')
    ap.add_argument('--db', help='override the database path')
    args = ap.parse_args(argv)
    if args.db:
        db.DB_PATH = args.db

    with db.connect() as conn:
        state = rules_data.provenance(conn)

    print('Imported:')
    print(f'  {state["datasheets"]:>5} datasheets   '
          f'(last written {state["datasheets_updated_at"] or "never"})')
    print(f'  {state["killteam"]:>5} Kill Team operatives')
    print(f'  {state["points"]:>5} points rows  '
          f'({state["points_source"] or "no source recorded"})')
    if state['overrides']:
        print(f'  {state["overrides"]:>5} points rows are manual overrides — '
              'a re-import reports and keeps them')
    if state['import_pending']:
        print(f'\n  ** data/mfm/ holds {state["mfm_on_disk"]} but the database '
              f'was priced from {state["points_effective_from"]}.')
        print('     The files were updated and the importer never re-run:')
        print('     python3 scripts/import_bsdata.py')

    print('\nPins:')
    stale, moved = [], []
    for row in rules_data.check_pins(timeout=args.timeout):
        if not row['reachable']:
            print(f'  {row["label"]:<22} could not reach {row["repo"]}')
            continue
        if row['stale']:
            stale.append(row)
            print(f'  {row["label"]:<22} NEW DATA  v{row["dataset"]["version"]} '
                  f'({row["dataset"]["date"]}) is out — take it')
        elif row['dataset']:
            # The one source that publishes a dated dataset, so this is a real
            # answer rather than an inference from a commit id.
            print(f'  {row["label"]:<22} current — v{row["dataset"]["version"]} '
                  f'({row["dataset"]["date"]}) is the newest published')
        elif row['moved']:
            moved.append(row)
            print(f'  {row["label"]:<22} {row["sha"][:12]} → {row["head"][:12]}, '
                  'commits ahead')
        else:
            print(f'  {row["label"]:<22} current at {row["sha"][:12]}')

    if moved:
        # Reported, never exit-worthy on its own. Measured 2026-08-26: BSData
        # was 35 commits ahead, every one a real data fix, and re-importing
        # changed two rows — both keyword-only. Its JSON carries the whole
        # BattleScribe model and this app reads a narrow slice, so most fixes
        # upstream are invisible here. Exiting 1 on that turns the weekly sweep
        # into a warning nobody reads.
        print('\n  Commits ahead is not the same as out of date: this app '
              'imports a narrow\n  slice of BSData, so most upstream fixes '
              'change nothing here. Re-import\n  to find out for certain.')

    if not stale:
        print('\nNothing to take. Points are current.')
        return 0

    print(f'\n{len(stale)} source(s) have published new data. '
          'Nothing has been changed.')
    print('To take an update, edit the SHA in rules_data.py, then:')
    for row in stale:
        if row['key'] == 'mfm':
            print('  MFM     — replace data/mfm/ from the new commit, update '
                  'data/SOURCES.md,\n            then python3 '
                  'scripts/import_bsdata.py')
        elif row['key'] == 'bsdata':
            print('  BSData  — python3 scripts/fetch_bsdata.py --force && '
                  'python3 scripts/import_bsdata.py')
        else:
            print('  Kill Team — python3 scripts/fetch_killteam.py --force && '
                  'python3 scripts/import_killteam.py')
    return 1


if __name__ == '__main__':
    sys.exit(main())
