"""SQLite connection handling, the migration runner, and shared queries.

Remndrs creates its schema idempotently from a SCHEMA constant and patches it
with an ad-hoc ``_migrate()``. This app uses numbered SQL files in
``migrations/`` instead, because the spec calls for it and because the data
here cannot be reconstructed from anywhere — "which of my Boyz are primed" has
no upstream source. An explicit, ordered, recorded migration history is what
makes a restore trustworthy.

Migrations are additive and never rewritten once applied.
"""

import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'hobby_tracker.db')
MIGRATIONS_DIR = os.path.join(BASE_DIR, 'migrations')


def now():
    """ISO-8601 local wall-clock stamp. TIMEZONE pins what "local" means."""
    return datetime.now().isoformat(timespec='seconds')


def connect(db_path=None):
    """Fresh connection per call — safe to use from background threads.

    Matches Remndrs: sqlite3.Row factory, foreign keys ON. WAL is set here too
    because the importer writes thousands of rows while the web app may be
    reading, and the default rollback journal blocks readers for the duration.
    """
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


# ── Migrations ───────────────────────────────────────────

def _ensure_migrations_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version    TEXT PRIMARY KEY,
          name       TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )
    """)


def discover_migrations(directory=None):
    """Return [(version, name, path)] sorted by version.

    Filenames are ``NNN_description.sql``; the numeric prefix is the version and
    orders the run. Anything not matching that shape is ignored rather than
    guessed at.
    """
    directory = directory or MIGRATIONS_DIR
    out = []
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith('.sql'):
            continue
        version, _, rest = fn[:-4].partition('_')
        if not version.isdigit():
            continue
        out.append((version, rest or fn[:-4], os.path.join(directory, fn)))
    return sorted(out, key=lambda r: int(r[0]))


def applied_versions(conn):
    _ensure_migrations_table(conn)
    return {r['version'] for r in conn.execute('SELECT version FROM schema_migrations')}


def migrate(db_path=None, directory=None, verbose=False):
    """Apply every pending migration in order. Returns the versions applied.

    Each migration runs inside its own transaction together with the bookkeeping
    row, so a failure leaves the database at the last fully-applied version
    rather than half-way through a file.
    """
    applied = []
    with connect(db_path) as conn:
        done = applied_versions(conn)
        for version, name, path in discover_migrations(directory):
            if version in done:
                continue
            with open(path, encoding='utf-8') as fh:
                sql = fh.read()
            # executescript() COMMITs any open transaction before running, so
            # BEGIN is issued inside it to keep the DDL and the bookkeeping row
            # atomic.
            conn.executescript('BEGIN;\n' + sql)
            conn.execute(
                'INSERT INTO schema_migrations (version, name, applied_at) '
                'VALUES (?, ?, ?)', (version, name, now()))
            conn.commit()
            applied.append(version)
            if verbose:
                print(f'  applied {version}_{name}')
    return applied


def init_db(db_path=None):
    """Bring the database up to the latest schema. Safe to call on every boot."""
    return migrate(db_path)


# ── Reference-data helpers ───────────────────────────────

def get_stages(conn):
    return conn.execute('SELECT * FROM stages ORDER BY position').fetchall()


def first_owned_stage(conn):
    """"On sprue" — where scanned and instantiated models start."""
    return conn.execute(
        'SELECT * FROM stages WHERE is_owned = 1 ORDER BY position LIMIT 1'
    ).fetchone()


def wishlist_stage(conn):
    """The one stage with is_owned = 0 — things Clay wants but does not have."""
    return conn.execute(
        'SELECT * FROM stages WHERE is_owned = 0 ORDER BY position LIMIT 1'
    ).fetchone()


def terminal_stage(conn):
    return conn.execute(
        'SELECT * FROM stages WHERE is_terminal = 1 ORDER BY position LIMIT 1'
    ).fetchone()


def upsert_faction(conn, name, slug):
    """Insert or update a faction by slug; returns its id."""
    conn.execute(
        'INSERT INTO factions (name, slug) VALUES (?, ?) '
        'ON CONFLICT(slug) DO UPDATE SET name = excluded.name', (name, slug))
    return conn.execute('SELECT id FROM factions WHERE slug = ?', (slug,)).fetchone()['id']


def get_faction_by_slug(conn, slug):
    return conn.execute('SELECT * FROM factions WHERE slug = ?', (slug,)).fetchone()


def record_unresolved(conn, importer, kind, raw_name, detail,
                      source_ref=None, payload=None):
    """Log something an importer could not resolve.

    Never guess and never drop silently: an unresolved row is visible and
    fixable, a dropped one is a shortfall Clay finds out about months later at
    the till.
    """
    import json as _json
    conn.execute(
        'INSERT INTO unresolved_imports '
        '(importer, kind, source_ref, raw_name, detail, payload, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (importer, kind, source_ref, raw_name, detail,
         _json.dumps(payload) if payload is not None else None, now()))


def open_unresolved(conn, importer=None):
    sql = 'SELECT * FROM unresolved_imports WHERE resolved_at IS NULL'
    args = []
    if importer:
        sql += ' AND importer = ?'
        args.append(importer)
    return conn.execute(sql + ' ORDER BY id', args).fetchall()


def clear_unresolved(conn, importer):
    """Drop a previous run's open rows so a re-import does not double-report."""
    conn.execute(
        'DELETE FROM unresolved_imports WHERE importer = ? AND resolved_at IS NULL',
        (importer,))


# ── Users (Remndrs auth posture) ─────────────────────────

def count_users(conn=None):
    own = conn is None
    conn = conn or connect()
    try:
        return conn.execute('SELECT COUNT(*) c FROM users').fetchone()['c']
    finally:
        if own:
            conn.close()


def create_user(name, password_hash, role='owner'):
    import uuid
    uid = str(uuid.uuid4())
    with connect() as conn:
        conn.execute(
            'INSERT INTO users (id, name, password_hash, role, created_at) '
            'VALUES (?, ?, ?, ?, ?)', (uid, name, password_hash, role, now()))
    return uid


def get_user(user_id):
    if not user_id:
        return None
    with connect() as conn:
        return conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()


def get_user_by_login(login):
    if not login:
        return None
    with connect() as conn:
        return conn.execute('SELECT * FROM users WHERE name = ?',
                            (login.strip(),)).fetchone()


def get_user_by_token_hash(token_hash):
    with connect() as conn:
        row = conn.execute(
            'SELECT u.* FROM users u JOIN api_tokens t ON t.user_id = u.id '
            'WHERE t.token_hash = ?', (token_hash,)).fetchone()
        if row:
            conn.execute('UPDATE api_tokens SET last_used_at = ? WHERE token_hash = ?',
                         (now(), token_hash))
        return row


def create_api_token(user_id, token_hash, device_name=None):
    import uuid
    tid = str(uuid.uuid4())
    with connect() as conn:
        conn.execute(
            'INSERT INTO api_tokens (id, user_id, token_hash, device_name, created_at) '
            'VALUES (?, ?, ?, ?, ?)', (tid, user_id, token_hash, device_name, now()))
    return tid


def delete_api_token_by_hash(token_hash):
    with connect() as conn:
        conn.execute('DELETE FROM api_tokens WHERE token_hash = ?', (token_hash,))


# ── Import status (surfaced on the skeleton index page) ──

def import_summary():
    """Counts the index page and `migrate.py --status` both report."""
    with connect() as conn:
        def one(sql):
            return conn.execute(sql).fetchone()[0]
        return {
            'factions': one('SELECT COUNT(*) FROM factions'),
            'datasheets': one('SELECT COUNT(*) FROM datasheets'),
            'datasheet_points': one('SELECT COUNT(*) FROM datasheet_points'),
            'stages': one('SELECT COUNT(*) FROM stages'),
            'armies': one('SELECT COUNT(*) FROM armies'),
            'models': one('SELECT COUNT(*) FROM models'),
            'unresolved': one(
                'SELECT COUNT(*) FROM unresolved_imports WHERE resolved_at IS NULL'),
        }
