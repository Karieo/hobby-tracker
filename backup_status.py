"""How long since a backup actually finished.

Clay put `./backup.sh` on a nightly cron. That turns its failure mode into
silence: it reports loudly, but at 3am "loudly" is one line in a file nobody
opens. `CLAUDE.md` already records what that costs — a non-matching `grep` once
took the whole backup down with exit 1 and no output, which under cron is
backups silently never happening, strictly worse than having none.

So the home screen says when the last one was.

Why a marker file and not the snapshots themselves
--------------------------------------------------
The obvious implementation is to stat the newest file in `BACKUP_DIR`. The app
cannot: it runs in a container with `./data` and `./.env` mounted and nothing
else, so `/mnt/t7` does not exist from in there. Reading the backup directory
would work perfectly in development and report "no backups, ever" on the only
machine that matters.

So `backup.sh` writes `data/.last-backup` as its final act. Final is the point:
the script runs under `set -euo pipefail`, so reaching the last line means
every step before it succeeded. A run that died halfway leaves the marker at
its previous value and the home screen keeps saying the backup is overdue,
which is exactly right.

Three states, not two
---------------------
`unknown` is not `never`. A missing marker means *this app has no record* —
which is also what a database restored from before this shipped looks like,
and what Clay's box looked like the day it landed despite having real backups
on the T7. Saying "never backed up" there would be false and, worse, would
train him to ignore the line.
"""

import os
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MARKER = os.path.join(BASE_DIR, 'data', '.last-backup')

#: Cron runs nightly, so one missed night is noise and two is a signal. Being
#: shouted at for a laptop that was closed overnight is how a warning becomes
#: wallpaper.
STALE_AFTER_DAYS = 2


def last_backup(path=MARKER, now=None):
    """``{'at', 'days', 'state'}`` — when, how long ago, and whether to worry.

    Never raises. This is decoration on the home screen: a truncated marker
    from a full disk, a clock that went backwards, or a file someone edited by
    hand must not be able to take the whole page down with it.
    """
    now = now or datetime.now(timezone.utc)
    stamp = _read(path)
    if stamp is None:
        return {'at': None, 'days': None, 'state': 'unknown'}

    days = (now - stamp).days
    if days < 0:
        # The marker is in the future: a clock skew, or a snapshot restored
        # from a box set differently. Not something to reassure anyone with.
        return {'at': stamp, 'days': None, 'state': 'unknown'}
    return {'at': stamp, 'days': days,
            'state': 'stale' if days >= STALE_AFTER_DAYS else 'ok'}


def describe(status):
    """The words for it. Kept here so the template holds no logic and the
    phrasing can be tested."""
    if status['state'] == 'unknown':
        return 'No backup recorded yet'
    if status['days'] == 0:
        return 'Backed up today'
    if status['days'] == 1:
        return 'Backed up yesterday'
    return f"Last backup {status['days']} days ago"


def _read(path):
    try:
        with open(path, encoding='utf-8') as fh:
            raw = fh.read().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None
    # `backup.sh` writes UTC with a Z. A marker without an offset is read as
    # UTC rather than guessed at — assuming local time would make the age wrong
    # by hours in one direction and right by accident in the other.
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
