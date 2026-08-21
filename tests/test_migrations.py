"""The migration runner and the shape the schema promises."""

import sqlite3

import pytest

import database as db


def test_migrate_applies_every_file_once(tmp_path):
    path = str(tmp_path / 'm.db')
    first = db.migrate(path)
    assert first, 'expected at least one migration to run'
    # Re-running is a no-op, which is what makes init_db() safe on every boot.
    assert db.migrate(path) == []


def test_migrations_are_recorded(tmp_path):
    path = str(tmp_path / 'm.db')
    db.migrate(path)
    with db.connect(path) as conn:
        rows = conn.execute(
            'SELECT version, name FROM schema_migrations ORDER BY version').fetchall()
    assert [r['version'] for r in rows] == [v for v, _n, _p in db.discover_migrations()]


def test_migrations_are_numbered_and_unique():
    versions = [v for v, _n, _p in db.discover_migrations()]
    assert versions == sorted(versions, key=int)
    assert len(set(versions)) == len(versions)


def test_a_failing_migration_leaves_the_last_good_version(tmp_path):
    """A half-applied migration would be worse than a failed one."""
    mig_dir = tmp_path / 'migrations'
    mig_dir.mkdir()
    (mig_dir / '001_ok.sql').write_text('CREATE TABLE a (id INTEGER PRIMARY KEY);')
    (mig_dir / '002_bad.sql').write_text(
        'CREATE TABLE b (id INTEGER PRIMARY KEY);\nTHIS IS NOT SQL;')
    path = str(tmp_path / 'm.db')

    with pytest.raises(sqlite3.Error):
        db.migrate(path, directory=str(mig_dir))

    with db.connect(path) as conn:
        applied = db.applied_versions(conn)
        tables = {r['name'] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert applied == {'001'}
    assert 'a' in tables
    assert 'b' not in tables, 'the failed migration left a table behind'


def test_stage_pipeline_seeded_in_spec_order(conn):
    stages = db.get_stages(conn)
    assert [s['name'] for s in stages] == [
        'Wishlist', 'On sprue', 'Assembled', 'Base prepared',
        'Primed', 'Painted', 'Based', 'Battle ready']
    assert [s['position'] for s in stages] == list(range(8))


def test_wishlist_is_the_only_unowned_stage(conn):
    """Wishlist is a stage, not a table — but it must not count as owned."""
    unowned = [s['name'] for s in db.get_stages(conn) if not s['is_owned']]
    assert unowned == ['Wishlist']


def test_exactly_one_terminal_stage(conn):
    terminal = [s['name'] for s in db.get_stages(conn) if s['is_terminal']]
    assert terminal == ['Battle ready']
    assert db.terminal_stage(conn)['name'] == 'Battle ready'
    assert db.first_owned_stage(conn)['name'] == 'On sprue'


def test_foreign_keys_are_enforced(conn):
    """Without this, a bad unit_id silently orphans models."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            'INSERT INTO models (unit_id, stage_id, stage_changed_at, created_at) '
            'VALUES (9999, 1, ?, ?)', (db.now(), db.now()))


def test_box_state_and_stage_stay_separate(conn):
    """A sealed box and an opened one both hold models "On sprue"."""
    cols = {r['name'] for r in conn.execute('PRAGMA table_info(kits)')}
    assert 'box_state' in cols
    model_cols = {r['name'] for r in conn.execute('PRAGMA table_info(models)')}
    assert 'box_state' not in model_cols
    assert 'stage_id' in model_cols


def test_kit_status_rejects_an_unknown_value(conn):
    """Disposals are status changes; a typo must not invent a fourth state."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO kits (name, status, created_at, updated_at) "
            "VALUES ('x', 'incinerated', ?, ?)", (db.now(), db.now()))


def test_units_may_have_no_army(conn):
    """A sealed box Clay has not committed to an army must not be forced."""
    conn.execute("INSERT INTO factions (name, slug) VALUES ('Orks', 'orks')")
    conn.execute(
        "INSERT INTO datasheets (bsdata_id, name, effort, created_at, updated_at) "
        "VALUES ('x', 'Boyz', 1, ?, ?)", (db.now(), db.now()))
    conn.execute(
        'INSERT INTO units (datasheet_id, created_at, updated_at) VALUES (1, ?, ?)',
        (db.now(), db.now()))
    assert conn.execute(
        'SELECT army_id FROM units WHERE id = 1').fetchone()['army_id'] is None
