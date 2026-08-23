#!/usr/bin/env python3
"""Fetch the pinned BSData Kill Team catalogues into data/killteam/.

    python3 scripts/fetch_killteam.py

Same contract as fetch_bsdata.py — one pinned commit, verified after checkout,
idempotent and offline once the checkout exists. A separate repository because
BSData keeps Kill Team separate: wh40k-11e is Warhammer 40,000 datasheets and
carries no operatives at all.

To bump: change KILLTEAM_SHA, re-run with --force, re-run the importer, and
read the report before trusting it.
"""

import argparse
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
DEST = os.path.join(BASE_DIR, 'data', 'killteam')

# The pin lives in `rules_data`, which `/reference` and the weekly sweep
# both read too. Declared in one place because a pin recorded twice is a
# pin that will eventually disagree with itself.
from rules_data import KILLTEAM_REPO, KILLTEAM_SHA  # noqa: E402


def git(*args):
    """Scoped with safe.directory, for the reasons in fetch_bsdata.git()."""
    return ['git', '-c', f'safe.directory={DEST}', *args]


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def current_sha(path):
    """(sha, error) — see fetch_bsdata.current_sha for why the error comes back."""
    proc = subprocess.run(git('-C', path, 'rev-parse', 'HEAD'),
                          capture_output=True, text=True)
    if proc.returncode == 0:
        return proc.stdout.strip(), None
    return None, proc.stderr.strip()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--force', action='store_true',
                    help='re-clone even if the checkout is already at the pin')
    args = ap.parse_args(argv)

    have, git_error = current_sha(DEST)
    if have == KILLTEAM_SHA and not args.force:
        n = len([f for f in os.listdir(DEST) if f.endswith('.cat')])
        print(f'Already at pinned {KILLTEAM_SHA[:12]} ({n} catalogues) — '
              'nothing to do.')
        return 0

    if os.path.exists(DEST):
        if have is None and os.listdir(DEST):
            catalogues = len([f for f in os.listdir(DEST) if f.endswith('.cat')])
            print(f'{DEST} exists and git cannot read it as a repository.',
                  file=sys.stderr)
            if git_error:
                print(f'  git said: {git_error}', file=sys.stderr)
            if catalogues:
                print(f'  It holds {catalogues} catalogues, so the data is '
                      'probably intact — do not move or delete it.\n'
                      '  The importer reads the XML directly and needs no git:\n'
                      '    python3 scripts/import_killteam.py\n'
                      '  Re-fetch only if you want the pin verified, with '
                      '--force.', file=sys.stderr)
            else:
                print('  It holds no catalogues. Move it aside and re-run.',
                      file=sys.stderr)
            return 1
        print(f'Removing previous checkout at {have[:12] if have else "?"}')
        shutil.rmtree(DEST)

    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    print(f'Cloning {KILLTEAM_REPO} @ {KILLTEAM_SHA[:12]}')
    run(git('init', '--quiet', DEST))
    run(git('-C', DEST, 'remote', 'add', 'origin', KILLTEAM_REPO))
    run(git('-C', DEST, 'fetch', '--quiet', '--depth', '1', 'origin', KILLTEAM_SHA))
    run(git('-C', DEST, 'checkout', '--quiet', 'FETCH_HEAD'))

    got, _ = current_sha(DEST)
    if got != KILLTEAM_SHA:
        print(f'Checked out {got}, expected {KILLTEAM_SHA} — refusing to continue.',
              file=sys.stderr)
        return 1

    n = len([f for f in os.listdir(DEST) if f.endswith('.cat')])
    print(f'Fetched {n} catalogues into {DEST}')
    print('Next: python3 scripts/import_killteam.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
