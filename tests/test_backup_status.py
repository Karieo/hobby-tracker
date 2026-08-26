"""When the last backup was, and whether to worry about it.

Clay put `./backup.sh` on a nightly cron, which turns its failure mode into
silence. This is the line on the home screen that breaks the silence, so what
matters most here is that it cannot lie in the reassuring direction: a missing
marker, a corrupt one, or a clock that went backwards must never read as "fine".
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backup_status as bs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def marker(tmp_path, text):
    path = tmp_path / '.last-backup'
    path.write_text(text)
    return str(path)


def test_a_backup_from_this_morning_is_fine(tmp_path):
    got = bs.last_backup(marker(tmp_path, '2026-08-26T03:00:00Z'), now=NOW)

    assert got['state'] == 'ok'
    assert got['days'] == 0
    assert bs.describe(got) == 'Backed up today'


def test_yesterday_is_still_fine(tmp_path):
    """One missed night is a closed laptop. Shouting about it is how a warning
    becomes wallpaper."""
    got = bs.last_backup(marker(tmp_path, '2026-08-25T03:00:00Z'), now=NOW)

    assert got['state'] == 'ok'
    assert bs.describe(got) == 'Backed up yesterday'


def test_two_nights_missed_is_a_warning(tmp_path):
    """Cron runs nightly, so two is no longer explicable by one bad evening."""
    got = bs.last_backup(marker(tmp_path, '2026-08-24T03:00:00Z'), now=NOW)

    assert got['state'] == 'stale'
    assert bs.describe(got) == 'Last backup 2 days ago'


def test_no_marker_is_unknown_and_never_says_never(tmp_path):
    """A missing marker means *this app* has no record — which is also what a
    box with real backups on the T7 looked like the day this shipped. Calling
    that "never backed up" would be false and would teach Clay to ignore the
    line."""
    got = bs.last_backup(str(tmp_path / 'absent'), now=NOW)

    assert got['state'] == 'unknown'
    assert bs.describe(got) == 'No backup recorded yet'


def test_a_corrupt_marker_does_not_read_as_fine(tmp_path):
    """A full disk truncates the write. Failing open here would be the one
    failure mode that matters: a home screen reassuring Clay about a backup
    that did not happen."""
    for junk in ('', '   ', 'yesterday', '2026-13-45T99:99:99Z', '\x00\x00'):
        got = bs.last_backup(marker(tmp_path, junk), now=NOW)
        assert got['state'] == 'unknown', junk


def test_a_marker_in_the_future_is_not_reassuring(tmp_path):
    """Clock skew, or a snapshot restored from a box set differently. Whatever
    it is, it is not evidence a backup ran."""
    got = bs.last_backup(marker(tmp_path, '2026-09-01T03:00:00Z'), now=NOW)

    assert got['state'] == 'unknown'


def test_a_marker_with_no_timezone_is_read_as_utc(tmp_path):
    """`backup.sh` writes a Z. Guessing local time for one without would make
    the age wrong by hours in one direction and right by luck in the other."""
    got = bs.last_backup(marker(tmp_path, '2026-08-26T03:00:00'), now=NOW)

    assert got['state'] == 'ok'
    assert got['days'] == 0


def test_an_unreadable_marker_never_raises(tmp_path):
    """This is decoration on the home screen. It must not be able to take the
    page down — a directory where a file should be is enough to try it."""
    d = tmp_path / '.last-backup'
    d.mkdir()

    assert bs.last_backup(str(d), now=NOW)['state'] == 'unknown'


# ── The other half: the script actually writes it ────────

def test_the_backup_script_writes_the_marker_last():
    """The marker is only meaningful because it is written after everything
    else: under `set -euo pipefail`, reaching that line means every step
    succeeded. Move it earlier and a half-failed run starts reassuring Clay.
    """
    script = open(os.path.join(ROOT, 'backup.sh'), encoding='utf-8').read()

    assert '.last-backup' in script, 'nothing would ever set the marker'
    written = script.index('.last-backup')
    for earlier in ('sqlite3 .backup', 'CSV export', 'rsync -a "$PHOTO_DIR/'):
        assert script.index(earlier) < written, \
            f'{earlier!r} must happen before the marker is written'


def test_the_marker_the_script_writes_is_one_this_module_can_read(tmp_path):
    """The two halves are a shell script and a Python module, so nothing but a
    test makes them agree on a format. `date -u +%Y-%m-%dT%H:%M:%SZ` is what
    `backup.sh` runs; this is that exact command."""
    stamp = subprocess.run(['date', '-u', '+%Y-%m-%dT%H:%M:%SZ'],
                           capture_output=True, text=True, check=True).stdout
    path = tmp_path / '.last-backup'
    path.write_text(stamp)

    got = bs.last_backup(str(path))

    assert got['state'] == 'ok', f'could not read {stamp!r}'
    assert got['days'] == 0
