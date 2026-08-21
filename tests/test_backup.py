"""backup.sh and restore.sh.

These are shell scripts, so they get shell-level tests: the whole point of a
backup is that it works unattended, and the failure this suite exists to catch
is the silent one — a cron job that exits non-zero every night and tells nobody.

Each test copies the scripts into a temp directory and runs them there, because
both derive their APP_DIR from their own location. Nothing here touches the
real database.
"""

import csv
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(shutil.which('bash') is None,
                                reason='bash not available')


def _install(tmp_path, env_lines):
    """A self-contained copy of the app's backup surface in a temp dir."""
    app = tmp_path / 'app'
    (app / 'data').mkdir(parents=True)
    for script in ('backup.sh', 'restore.sh'):
        shutil.copy(os.path.join(REPO, script), app / script)
        os.chmod(app / script, 0o755)
    shutil.copytree(os.path.join(REPO, 'migrations'), app / 'migrations')
    (app / '.env').write_text('\n'.join(env_lines) + '\n')

    sys.path.insert(0, REPO)
    import database as db
    db_path = str(app / 'data' / 'hobby_tracker.db')
    db.migrate(db_path)
    # Closed explicitly, not just committed: `with conn` ends the transaction
    # but leaves the connection open, and an open connection keeps the WAL from
    # checkpointing — which would leave the fixture's own schema sitting in the
    # -wal file and make every test here measure the wrong thing.
    conn = db.connect(db_path)
    try:
        faction = db.upsert_faction(conn, 'Orks', 'orks')
        conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            "created_at, updated_at) VALUES ('boyz', 'Boyz', ?, 1, ?, ?)",
            (faction, db.now(), db.now()))
        conn.execute("INSERT INTO armies (name, sort_order, created_at) "
                     "VALUES ('Da Boyz', 1, ?)", (db.now(),))
        conn.execute('INSERT INTO units (army_id, datasheet_id, created_at, '
                     'updated_at) VALUES (1, 1, ?, ?)', (db.now(), db.now()))
        for _ in range(10):
            conn.execute(
                'INSERT INTO models (unit_id, stage_id, stage_changed_at, '
                'created_at) VALUES (1, 2, ?, ?)', (db.now(), db.now()))
        conn.execute(
            'INSERT INTO users (id, name, password_hash, role, created_at) '
            "VALUES ('u1', 'Clay', '$2b$12$notarealhash', 'owner', ?)", (db.now(),))
        conn.commit()
    finally:
        conn.close()
    return app


def _run(app, script, *args):
    return subprocess.run(['bash', str(app / script), *args],
                          capture_output=True, text=True, cwd=str(app), timeout=120)


def _snapshots(backup_dir):
    db_dir = os.path.join(backup_dir, 'db')
    if not os.path.isdir(db_dir):
        return []
    return sorted(os.path.join(db_dir, f) for f in os.listdir(db_dir)
                  if f.endswith('.db'))


# ── The silent-failure bug ───────────────────────────────

def test_backup_succeeds_with_optional_settings_absent(tmp_path):
    """The regression that mattered.

    BACKUP_SSH_KEY ships commented out in .env.example, so following the
    documented setup left it unset. Under `set -euo pipefail` the non-matching
    grep in env_value took the whole script down — exit 1, no output, and under
    cron that means backups silently never happen.
    """
    backup_dir = tmp_path / 'backups'
    app = _install(tmp_path, [f'BACKUP_DIR={backup_dir}'])   # nothing else set
    result = _run(app, 'backup.sh')
    assert result.returncode == 0, (
        f'backup.sh failed with only BACKUP_DIR set\n'
        f'stdout: {result.stdout}\nstderr: {result.stderr}')
    assert _snapshots(str(backup_dir)), 'no snapshot was written'


def test_backup_fails_loudly_when_it_fails(tmp_path):
    """A backup that dies must say so. Cron only sees output."""
    app = _install(tmp_path, ['BACKUP_DIR='])               # required, empty
    result = _run(app, 'backup.sh')
    assert result.returncode != 0
    assert 'BACKUP_DIR' in (result.stdout + result.stderr), \
        'the failure must name what is wrong, not just exit non-zero'


def test_backup_reports_a_missing_database_rather_than_writing_an_empty_one(tmp_path):
    backup_dir = tmp_path / 'backups'
    app = _install(tmp_path, [f'BACKUP_DIR={backup_dir}'])
    os.remove(app / 'data' / 'hobby_tracker.db')
    result = _run(app, 'backup.sh')
    assert result.returncode != 0
    assert 'No database' in result.stdout + result.stderr


# ── What the backup contains ─────────────────────────────

def test_backup_writes_a_snapshot_and_a_csv_export(tmp_path):
    backup_dir = tmp_path / 'backups'
    app = _install(tmp_path, [f'BACKUP_DIR={backup_dir}'])
    assert _run(app, 'backup.sh').returncode == 0

    snaps = _snapshots(str(backup_dir))
    assert len(snaps) == 1
    conn = sqlite3.connect(f'file:{snaps[0]}?mode=ro', uri=True)
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 10

    csv_root = backup_dir / 'csv'
    export = next(csv_root.iterdir())
    names = {f.name for f in export.iterdir()}
    assert 'models.csv' in names and 'stage_events.csv' in names
    with open(export / 'models.csv', encoding='utf-8') as fh:
        assert len(list(csv.DictReader(fh))) == 10


def test_csv_export_redacts_credentials(tmp_path):
    """The CSV travels off-box in plain text; the .db snapshot keeps the real
    values. A password hash does nothing for reading a collection."""
    backup_dir = tmp_path / 'backups'
    app = _install(tmp_path, [f'BACKUP_DIR={backup_dir}'])
    assert _run(app, 'backup.sh').returncode == 0
    export = next((backup_dir / 'csv').iterdir())
    with open(export / 'users.csv', encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]['password_hash'] == '[redacted]'
    assert rows[0]['name'] == 'Clay', 'only the credential is hidden'

    snaps = _snapshots(str(backup_dir))
    conn = sqlite3.connect(f'file:{snaps[0]}?mode=ro', uri=True)
    kept = conn.execute('SELECT password_hash FROM users').fetchone()[0]
    assert kept == '$2b$12$notarealhash', 'the .db snapshot must stay complete'


def test_rotation_keeps_the_newest(tmp_path):
    backup_dir = tmp_path / 'backups'
    app = _install(tmp_path, [f'BACKUP_DIR={backup_dir}', 'BACKUP_KEEP=2'])
    for _ in range(3):
        # Snapshots are stamped to the second, so they need distinct names.
        assert _run(app, 'backup.sh').returncode == 0
        for old in _snapshots(str(backup_dir)):
            pass
        subprocess.run(['sleep', '1.1'], check=True)
    assert len(_snapshots(str(backup_dir))) <= 2


# ── Restore ──────────────────────────────────────────────

def test_check_verifies_the_newest_snapshot_without_writing(tmp_path):
    backup_dir = tmp_path / 'backups'
    app = _install(tmp_path, [f'BACKUP_DIR={backup_dir}'])
    assert _run(app, 'backup.sh').returncode == 0
    snap = _snapshots(str(backup_dir))[0]
    before = os.stat(snap).st_mtime

    result = _run(app, 'restore.sh', '--check')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'intact and restorable' in result.stdout
    assert os.stat(snap).st_mtime == before, '--check must not touch the snapshot'


def test_check_rejects_a_corrupt_snapshot(tmp_path):
    """A backup that cannot be restored must not report success."""
    backup_dir = tmp_path / 'backups'
    app = _install(tmp_path, [f'BACKUP_DIR={backup_dir}'])
    assert _run(app, 'backup.sh').returncode == 0
    snap = _snapshots(str(backup_dir))[0]
    with open(snap, 'r+b') as fh:
        fh.seek(1024)
        fh.write(b'\xff' * 4096)          # corrupt the middle of the file
    result = _run(app, 'restore.sh', '--check', snap)
    assert result.returncode != 0
    assert 'intact and restorable' not in result.stdout


def test_check_rejects_a_snapshot_with_no_collection_data(tmp_path):
    """Rules data can be re-imported; the collection cannot. An empty
    collection means the backup captured nothing worth keeping."""
    backup_dir = tmp_path / 'backups'
    app = _install(tmp_path, [f'BACKUP_DIR={backup_dir}'])
    with sqlite3.connect(app / 'data' / 'hobby_tracker.db') as conn:
        conn.execute('PRAGMA foreign_keys = OFF')
        for table in ('stage_events', 'models', 'units', 'kits', 'armies'):
            conn.execute(f'DELETE FROM {table}')
    assert _run(app, 'backup.sh').returncode == 0
    result = _run(app, 'restore.sh', '--check')
    assert result.returncode != 0
    assert 'no collection data' in result.stdout


def test_restore_brings_back_a_destroyed_database(tmp_path):
    backup_dir = tmp_path / 'backups'
    app = _install(tmp_path, [f'BACKUP_DIR={backup_dir}'])
    assert _run(app, 'backup.sh').returncode == 0
    live = app / 'data' / 'hobby_tracker.db'
    os.remove(live)

    result = _run(app, 'restore.sh', _snapshots(str(backup_dir))[0])
    assert result.returncode == 0, result.stdout + result.stderr
    conn = sqlite3.connect(f'file:{live}?mode=ro', uri=True)
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 10
    assert conn.execute('SELECT COUNT(*) FROM armies').fetchone()[0] == 1


def test_restore_sets_the_current_database_aside_first(tmp_path):
    """Restoring the wrong snapshot is an easier mistake than losing the DB."""
    backup_dir = tmp_path / 'backups'
    app = _install(tmp_path, [f'BACKUP_DIR={backup_dir}'])
    assert _run(app, 'backup.sh').returncode == 0
    with sqlite3.connect(app / 'data' / 'hobby_tracker.db') as conn:
        conn.execute("INSERT INTO armies (name, sort_order, created_at) "
                     "VALUES ('Added after the backup', 2, '2026-01-01')")

    assert _run(app, 'restore.sh', _snapshots(str(backup_dir))[0]).returncode == 0
    aside = [f for f in os.listdir(app / 'data') if '.replaced-' in f]
    assert aside, 'the replaced database must be kept'
    conn = sqlite3.connect(f'file:{app / "data" / aside[0]}?mode=ro', uri=True)
    assert conn.execute(
        "SELECT COUNT(*) FROM armies WHERE name = 'Added after the backup'"
    ).fetchone()[0] == 1, 'the set-aside copy must hold what the restore discarded'


def test_a_wal_only_write_survives_the_backup(tmp_path):
    """The reason this uses sqlite3 .backup and not cp.

    A live app keeps a connection open, so committed data can sit in the -wal
    file with the .db not yet containing it. Copying the .db alone loses it
    silently, and the backup looks fine until the day it matters.
    """
    backup_dir = tmp_path / 'backups'
    app = _install(tmp_path, [f'BACKUP_DIR={backup_dir}'])
    live = str(app / 'data' / 'hobby_tracker.db')

    holder = sqlite3.connect(live)
    holder.execute('PRAGMA journal_mode = WAL')
    holder.execute("INSERT INTO armies (name, sort_order, created_at) "
                   "VALUES ('WAL only', 9, '2026-01-01')")
    holder.commit()                       # committed, but not checkpointed
    try:
        naive = str(tmp_path / 'naive.db')
        shutil.copy(live, naive)          # the mistake
        assert _run(app, 'backup.sh').returncode == 0
        snap = _snapshots(str(backup_dir))[0]
    finally:
        holder.close()

    def has_canary(path):
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            return bool(conn.execute(
                "SELECT 1 FROM armies WHERE name = 'WAL only'").fetchone())
        except sqlite3.OperationalError:
            # A copy so incomplete the table isn't even there has certainly
            # lost the row, which is the point being made.
            return False
        finally:
            conn.close()

    assert not has_canary(naive), 'a plain copy is expected to lose it'
    assert has_canary(snap), 'the .backup snapshot must not'
