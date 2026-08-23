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
sys.path.insert(0, BASE_DIR)
DEST = os.path.join(BASE_DIR, 'data', 'bsdata')

# The pin lives in `rules_data`, which `/reference` and the weekly sweep
# both read too. Declared in one place because a pin recorded twice is a
# pin that will eventually disagree with itself.
from rules_data import BSDATA_REPO, BSDATA_SHA  # noqa: E402


def git(*args):
    """A git command line scoped with safe.directory.

    The checkout gets created by one user and read by another routinely: a
    host-side fetch leaves it owned by the login user, and the same directory
    is bind-mounted into the container where git runs as root. Git refuses to
    touch a repository owned by someone else ("detected dubious ownership"),
    which says nothing at all about whether the checkout is good.

    Scoped per invocation rather than written into global git config: the
    exemption covers this one directory, and nothing is left behind on the box.
    """
    return ['git', '-c', f'safe.directory={DEST}', *args]


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def current_sha(path):
    """(sha, error). sha is None when git could not read the directory.

    The error comes back rather than going to DEVNULL because the two reasons
    git fails here need completely different responses — "this is not a
    repository" means re-fetch, "dubious ownership" means the data is fine and
    git is the problem — and a caller that cannot tell them apart gives the
    wrong advice. This function used to swallow it, and the script told a user
    with a perfectly good checkout to move it aside.
    """
    proc = subprocess.run(git('-C', path, 'rev-parse', 'HEAD'),
                          capture_output=True, text=True)
    if proc.returncode == 0:
        return proc.stdout.strip(), None
    return None, proc.stderr.strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--force', action='store_true',
                    help='re-clone even if the checkout is already at the pin')
    args = ap.parse_args(argv)

    have, git_error = current_sha(DEST)
    if have == BSDATA_SHA and not args.force:
        n = len([f for f in os.listdir(DEST) if f.endswith('.json')])
        print(f'Already at pinned {BSDATA_SHA[:12]} ({n} catalogues) — nothing to do.')
        return 0

    if os.path.exists(DEST):
        if have is None and os.listdir(DEST):
            # Something this script did not create is in the way. Never delete
            # it — but do say which of the two cases it is, because the advice
            # is opposite. Catalogues present means the data is very likely
            # fine and git simply cannot read the directory as this user; the
            # importer reads the JSON directly and does not care.
            catalogues = len([f for f in os.listdir(DEST) if f.endswith('.json')])
            print(f'{DEST} exists and git cannot read it as a repository.',
                  file=sys.stderr)
            if git_error:
                print(f'  git said: {git_error}', file=sys.stderr)
            if catalogues:
                print(f'  It holds {catalogues} catalogues, so the data is '
                      'probably intact — do not move or delete it.\n'
                      '  The importer reads the JSON directly and needs no '
                      'git:\n'
                      '    python3 scripts/import_bsdata.py\n'
                      '  Re-fetch only if you want the pin verified, with '
                      '--force.', file=sys.stderr)
            else:
                print('  It holds no catalogues. Move it aside and re-run.',
                      file=sys.stderr)
            return 1
        print(f'Removing previous checkout at {have[:12] if have else "?"}')
        shutil.rmtree(DEST)

    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    print(f'Cloning {BSDATA_REPO} @ {BSDATA_SHA[:12]}')
    # Fetch just the pinned commit rather than cloning full history: the repo
    # gets daily commits and none of that history is useful here.
    run(git('init', '--quiet', DEST))
    run(git('-C', DEST, 'remote', 'add', 'origin', BSDATA_REPO))
    run(git('-C', DEST, 'fetch', '--quiet', '--depth', '1', 'origin', BSDATA_SHA))
    run(git('-C', DEST, 'checkout', '--quiet', 'FETCH_HEAD'))

    got, _ = current_sha(DEST)
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
