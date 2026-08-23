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


# ── 007: one faction per name ────────────────────────────
#
# Clay's faction picker offered "Adepta Sororitas" twice with no way to tell
# which was which. Two rows, one meaning.

def _apply_007(path):
    """Run the merge migration's own SQL against a database that already holds
    the duplicates it is meant to fix."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sql = open(os.path.join(root, 'migrations',
                            '007_merge_duplicate_factions.sql'),
               encoding='utf-8').read()
    conn = db.connect(path)
    conn.executescript(sql)
    conn.commit()
    conn.close()


def _faction(conn, name, slug):
    return db.upsert_faction(conn, name, slug)


def test_a_kill_team_faction_merges_into_its_40k_namesake(tmp_path):
    path = str(tmp_path / 'merge.db')
    db.migrate(path)
    conn = db.connect(path)
    keep = _faction(conn, 'Adepta Sororitas', 'adepta-sororitas')
    dupe = _faction(conn, 'Adepta Sororitas', 'kt-adepta-sororitas')
    sheet = conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
        "game_system, created_at, updated_at) VALUES ('kt:x', 'Sister', ?, 1, "
        "'killteam', ?, ?)", (dupe, db.now(), db.now())).lastrowid
    army = conn.execute(
        'INSERT INTO armies (name, primary_faction_id, created_at) '
        'VALUES (?, ?, ?)', ('Sisters', dupe, db.now())).lastrowid
    conn.commit()
    conn.close()

    _apply_007(path)

    conn = db.connect(path)
    assert [r['name'] for r in conn.execute('SELECT name FROM factions')] == \
        ['Adepta Sororitas'], 'one row survives'
    assert conn.execute('SELECT faction_id FROM datasheets WHERE id = ?',
                        (sheet,)).fetchone()['faction_id'] == keep
    assert conn.execute('SELECT primary_faction_id FROM armies WHERE id = ?',
                        (army,)).fetchone()['primary_faction_id'] == keep
    conn.close()


def test_a_kill_team_only_faction_is_left_alone(tmp_path):
    """Wrecka Krew is not a duplicate of anything."""
    path = str(tmp_path / 'keep.db')
    db.migrate(path)
    conn = db.connect(path)
    _faction(conn, 'Wrecka Krew', 'kt-wrecka-krew')
    _faction(conn, 'Orks', 'orks')
    conn.commit()
    conn.close()

    _apply_007(path)

    conn = db.connect(path)
    assert sorted(r['slug'] for r in conn.execute('SELECT slug FROM factions')) \
        == ['kt-wrecka-krew', 'orks']
    conn.close()


def test_a_kit_tagged_with_the_duplicate_follows_the_merge(tmp_path):
    """Anything Clay already tagged has to come with it, or it points at a row
    that no longer exists."""
    path = str(tmp_path / 'kits.db')
    db.migrate(path)
    conn = db.connect(path)
    keep = _faction(conn, 'Orks', 'orks')
    dupe = _faction(conn, 'Orks', 'kt-orks')
    kit = conn.execute(
        'INSERT INTO kits (name, faction_id, status, created_at, updated_at) '
        "VALUES ('Wrecka Krew', ?, 'owned', ?, ?)",
        (dupe, db.now(), db.now())).lastrowid
    lst = conn.execute(
        'INSERT INTO army_lists (name, faction_id, created_at) '
        'VALUES (?, ?, ?)', ('Saturday', dupe, db.now())).lastrowid
    conn.commit()
    conn.close()

    _apply_007(path)

    conn = db.connect(path)
    assert conn.execute('SELECT faction_id FROM kits WHERE id = ?',
                        (kit,)).fetchone()['faction_id'] == keep
    assert conn.execute('SELECT faction_id FROM army_lists WHERE id = ?',
                        (lst,)).fetchone()['faction_id'] == keep
    assert not conn.execute('SELECT 1 FROM factions WHERE id = ?',
                            (dupe,)).fetchone(), 'the duplicate is gone'
    conn.close()


def test_the_merge_survives_a_re_import(tmp_path):
    """A merge the next Kill Team import undoes is worth nothing."""
    path = str(tmp_path / 'reimport.db')
    db.migrate(path)
    conn = db.connect(path)
    keep = _faction(conn, 'Orks', 'orks')

    # Exactly the lookup the importer now performs for a team name.
    existing = conn.execute(
        "SELECT id, slug FROM factions WHERE name = ? AND slug NOT LIKE 'kt-%'",
        ('Orks',)).fetchone()

    assert existing is not None and existing['id'] == keep
    conn.close()
