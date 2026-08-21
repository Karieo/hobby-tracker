#!/usr/bin/env python3
"""Fetch the pinned BSData wh40k-11e catalogues into data/bsdata/.

Run once at setup, and again only when you deliberately bump the pin:

    python3 scripts/fetch_bsdata.py

The SHA below is the contract. Re-running is idempotent and offline once the
checkout exists — nothing here is ever called from a request path. See
data/SOURCES.md for why this repo fetches BSData rather than committing it.

To bump: change BSDATA_SHA, re-run with --force, re-run the importer, and read
the points diff before trusting it.
"""

import argparse
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(BASE_DIR, 'data', 'bsdata')

BSDATA_REPO = 'https://github.com/BSData/wh40k-11e'
BSDATA_SHA = '13f3c4e54d15f96baebdc48c3a8c10431db2990f'


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def current_sha(path):
    try:
        out = subprocess.check_output(['git', '-C', path, 'rev-parse', 'HEAD'],
                                      stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--force', action='store_true',
                    help='re-clone even if the checkout is already at the pin')
    args = ap.parse_args(argv)

    have = current_sha(DEST)
    if have == BSDATA_SHA and not args.force:
        n = len([f for f in os.listdir(DEST) if f.endswith('.json')])
        print(f'Already at pinned {BSDATA_SHA[:12]} ({n} catalogues) — nothing to do.')
        return 0

    if os.path.exists(DEST):
        if have is None and os.listdir(DEST):
            # Something that is not a git checkout is sitting in the way. Do not
            # delete data this script did not create.
            print(f'{DEST} exists but is not a BSData checkout. '
                  'Move it aside and re-run.', file=sys.stderr)
            return 1
        print(f'Removing previous checkout at {have[:12] if have else "?"}')
        shutil.rmtree(DEST)

    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    print(f'Cloning {BSDATA_REPO} @ {BSDATA_SHA[:12]}')
    # Fetch just the pinned commit rather than cloning full history: the repo
    # gets daily commits and none of that history is useful here.
    run(['git', 'init', '--quiet', DEST])
    run(['git', '-C', DEST, 'remote', 'add', 'origin', BSDATA_REPO])
    run(['git', '-C', DEST, 'fetch', '--quiet', '--depth', '1', 'origin', BSDATA_SHA])
    run(['git', '-C', DEST, 'checkout', '--quiet', 'FETCH_HEAD'])

    got = current_sha(DEST)
    if got != BSDATA_SHA:
        print(f'Checked out {got}, expected {BSDATA_SHA} — refusing to continue.',
              file=sys.stderr)
        return 1

    n = len([f for f in os.listdir(DEST) if f.endswith('.json')])
    print(f'Fetched {n} catalogues into {DEST}')
    print('Next: python3 scripts/import_bsdata.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
