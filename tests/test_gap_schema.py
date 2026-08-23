"""Migration 008 — the gap checker's prerequisite schema.

Section 7 needs three things this database could not say: what a model *is*
independently of its unit, whether that is reversible (magnetised), and what a
box is *capable* of becoming. This is the migration that adds them, and these
are the tests that it does so without touching what is already there.

The most important ones here are the boring ones. Clay has a live database on
bastion with real kits, real units and a pasted list in it, and 008 rebuilds
`list_entries` to make `datasheet_id` nullable. A rebuild that quietly dropped a
row, renumbered an id or lost an order would be discovered weeks later with no
way back.

NOTE ON "ROLLS BACK CLEAN". The kickoff asks for it. There is no rollback in
this repo — migrations are forward-only numbered files recorded in
`schema_migrations`, and CLAUDE.md forbids rewriting one that has been applied.
What is actually guaranteed is atomicity: each file runs inside its own
transaction with its bookkeeping row, so a failure leaves the database at the
last fully-applied version rather than half-way through. That is covered by
`test_a_failing_migration_leaves_the_last_good_version` in test_migrations.py.
"""

import os
import sqlite3

import pytest

import database as db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS = os.path.join(ROOT, 'migrations')
EIGHT = os.path.join(MIGRATIONS, '008_gap_checker_schema.sql')


def _migrate_to_007(tmp_path):
    """A database at the version Clay's actually running, not a fresh one.

    Testing 008 against a freshly-migrated database would prove nothing about
    the case that matters — an existing database with rows in the tables it
    rebuilds.
    """
    staged = tmp_path / 'staged'
    staged.mkdir()
    for name in sorted(os.listdir(MIGRATIONS)):
        if name.endswith('.sql') and name[:3] < '008':
            (staged / name).write_text(
                open(os.path.join(MIGRATIONS, name), encoding='utf-8').read(),
                encoding='utf-8')
    path = str(tmp_path / 'gap.db')
    db.migrate(path, directory=str(staged))
    return path


def _apply_008(path):
    conn = db.connect(path)
    conn.executescript(open(EIGHT, encoding='utf-8').read())
    conn.commit()
    conn.close()


@pytest.fixture
def seeded(tmp_path):
    """A database at 007 holding the shapes 008 has to survive."""
    import collection as col
    import lists

    path = _migrate_to_007(tmp_path)
    conn = db.connect(path)
    orks = db.upsert_faction(conn, 'Orks', 'orks')
    sheets = {}
    for slug, name in (('boyz', 'Boyz'), ('trukk', 'Trukk'),
                       ('warboss', 'Warboss'), ('kans', 'Killa Kans')):
        cur = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)',
            (slug, name, orks, db.now(), db.now()))
        sheets[name] = cur.lastrowid

    # A box whose contents are known from the catalogue, adopted into units.
    template_id = conn.execute(
        'INSERT INTO kit_templates (name, faction_id, created_at, updated_at) '
        'VALUES (?, ?, ?, ?)', ('Combat Patrol: Orks', orks, db.now(), db.now())
    ).lastrowid
    for name, count in (('Boyz', 10), ('Trukk', 1), ('Warboss', 1)):
        conn.execute('INSERT INTO kit_template_units (kit_template_id, '
                     'datasheet_id, model_count) VALUES (?, ?, ?)',
                     (template_id, sheets[name], count))
    kit_id, _units = col.instantiate_template(conn, template_id)

    # A box with no template at all — bought loose, units added by hand.
    loose_kit = col.create_kit(conn, 'Killa Kans')
    col.create_unit(conn, sheets['Killa Kans'], 3, kit_id=loose_kit)

    # A box recorded off a barcode and never identified: no template, no units.
    empty_kit = col.create_kit(conn, 'Unidentified box 5011921000000')

    # A unit with no kit at all — pasted in, or built years ago.
    col.create_unit(conn, sheets['Boyz'], 5)

    # And a list, because 008 rebuilds the table its entries live in.
    #
    # The entries are written with 007-era SQL rather than through
    # `lists.add_entry`. That helper targets the current schema — it assigns
    # `position` and `resolved_by`, columns 008 is about to add — so calling it
    # here would fail against the very database shape this fixture exists to
    # reproduce. Application code always runs against a migrated database
    # (`init_db()` migrates on boot); only this fixture deliberately does not.
    list_id = lists.create_list(conn, 'Saturday', faction_id=orks)
    entry_ids = []
    for name, count in (('Boyz', 20), ('Warboss', 1), ('Killa Kans', 3)):
        entry_ids.append(conn.execute(
            'INSERT INTO list_entries (list_id, datasheet_id, model_count, '
            'points_snapshot, is_proxy) VALUES (?, ?, ?, NULL, 0)',
            (list_id, sheets[name], count)).lastrowid)
    conn.commit()
    conn.close()
    return {'path': path, 'sheets': sheets, 'kit_id': kit_id,
            'loose_kit': loose_kit, 'empty_kit': empty_kit,
            'list_id': list_id, 'entry_ids': entry_ids}


# ── It applies to a database that already has data in it ─────────────────────

def test_nothing_is_lost_rebuilding_list_entries(seeded):
    """The rebuild that makes datasheet_id nullable must not cost a row."""
    conn = db.connect(seeded['path'])
    before = [dict(r) for r in conn.execute(
        'SELECT id, list_id, datasheet_id, model_count, points_snapshot, '
        'is_proxy FROM list_entries ORDER BY id')]
    conn.close()

    _apply_008(seeded['path'])

    conn = db.connect(seeded['path'])
    after = [dict(r) for r in conn.execute(
        'SELECT id, list_id, datasheet_id, model_count, points_snapshot, '
        'is_proxy FROM list_entries ORDER BY id')]
    assert after == before, 'ids and values both have to survive the rebuild'
    conn.close()


def test_existing_entries_keep_the_order_they_display_in(seeded):
    """`position` is new, so it has to be derived — and a list that reordered
    itself on upgrade would be a silent corruption of what Clay wrote down."""
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    rows = conn.execute('SELECT id, position FROM list_entries '
                        'WHERE list_id = ? ORDER BY position',
                        (seeded['list_id'],)).fetchall()
    assert [r['id'] for r in rows] == seeded['entry_ids']
    assert [r['position'] for r in rows] == [0, 1, 2]
    conn.close()


def test_existing_entries_are_marked_manually_resolved(seeded):
    """They predate the parser. An entry could not exist without a datasheet,
    so every one of them was Clay's own choice — never a fuzzy guess."""
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    kinds = {r['resolved_by'] for r in
             conn.execute('SELECT resolved_by FROM list_entries')}
    assert kinds == {'manual'}
    conn.close()


def test_the_list_itself_keeps_its_wishlist_link(seeded):
    """`models.wishlist_source_list_id` points at army_lists. Adding a second
    parallel `lists` table, as the spec writes it, would have split that."""
    import lists as lists_mod
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    raised = lists_mod.raise_wishlist(conn, seeded['list_id'])
    assert raised > 0
    stamped = conn.execute(
        'SELECT COUNT(*) FROM models WHERE wishlist_source_list_id = ?',
        (seeded['list_id'],)).fetchone()[0]
    assert stamped == raised
    conn.close()


# ── The backfill ─────────────────────────────────────────────────────────────

def test_every_model_gets_its_unit_s_datasheet(seeded):
    """The kickoff expected a partial backfill and a list of leftovers to map
    by hand. There are none: a model in a Killa Kans unit is a Killa Kan, and
    that is recorded fact rather than an inference from the box."""
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    total, done = conn.execute(
        'SELECT COUNT(*), COUNT(datasheet_id) FROM models').fetchone()
    assert total > 0 and done == total
    mismatched = conn.execute("""
        SELECT COUNT(*) FROM models m JOIN units u ON u.id = m.unit_id
         WHERE m.datasheet_id IS NOT u.datasheet_id""").fetchone()[0]
    assert mismatched == 0
    conn.close()


def test_the_backfill_is_idempotent(seeded):
    """Re-running the whole file is how a botched deploy gets retried."""
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    first = [dict(r) for r in conn.execute(
        'SELECT id, datasheet_id, is_flexible FROM models ORDER BY id')]
    conn.close()

    # Only the parts that can legally run twice — the DDL cannot, and the
    # migration runner never offers it the chance.
    conn = db.connect(seeded['path'])
    conn.executescript("""
        UPDATE models SET datasheet_id =
          (SELECT u.datasheet_id FROM units u WHERE u.id = models.unit_id)
         WHERE datasheet_id IS NULL;
        INSERT OR IGNORE INTO kit_datasheets (kit_id, datasheet_id)
        SELECT k.id, ktu.datasheet_id FROM kits k
          JOIN kit_template_units ktu ON ktu.kit_template_id = k.kit_template_id;
        INSERT OR IGNORE INTO kit_datasheets (kit_id, datasheet_id)
        SELECT u.kit_id, u.datasheet_id FROM units u WHERE u.kit_id IS NOT NULL;
    """)
    conn.commit()
    second = [dict(r) for r in conn.execute(
        'SELECT id, datasheet_id, is_flexible FROM models ORDER BY id')]
    assert second == first
    conn.close()


def test_a_hand_set_datasheet_is_not_overwritten(seeded):
    """The backfill only fills blanks. A model Clay has told the app is a
    Warglaive must not be reset to whatever its unit says."""
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    model_id = conn.execute('SELECT id FROM models LIMIT 1').fetchone()[0]
    conn.execute('UPDATE models SET datasheet_id = ?, is_flexible = 1 '
                 'WHERE id = ?', (seeded['sheets']['Trukk'], model_id))
    conn.execute("""
        UPDATE models SET datasheet_id =
          (SELECT u.datasheet_id FROM units u WHERE u.id = models.unit_id)
         WHERE datasheet_id IS NULL""")
    row = conn.execute('SELECT datasheet_id, is_flexible FROM models '
                       'WHERE id = ?', (model_id,)).fetchone()
    assert row['datasheet_id'] == seeded['sheets']['Trukk']
    assert row['is_flexible'] == 1
    conn.close()


# ── kit_datasheets: what a box can become ────────────────────────────────────

def test_a_kit_learns_its_template_s_whole_contents(seeded):
    """Every datasheet in the box, not only the ones with units in them —
    that is what lets allocation offer an unbuilt sprue as buildable."""
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    got = {r['datasheet_id'] for r in conn.execute(
        'SELECT datasheet_id FROM kit_datasheets WHERE kit_id = ?',
        (seeded['kit_id'],))}
    sheets = seeded['sheets']
    assert got == {sheets['Boyz'], sheets['Trukk'], sheets['Warboss']}
    conn.close()


def test_a_kit_with_no_template_learns_from_its_units(seeded):
    """Bought loose and entered by hand. The catalogue knows nothing about it,
    but the units in it are recorded fact."""
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    got = {r['datasheet_id'] for r in conn.execute(
        'SELECT datasheet_id FROM kit_datasheets WHERE kit_id = ?',
        (seeded['loose_kit'],))}
    assert got == {seeded['sheets']['Killa Kans']}
    conn.close()


def test_an_unidentified_box_maps_to_nothing_rather_than_a_guess(seeded):
    """A shelved barcode with no contents defined. Inventing what is in it is
    the one change to this repo that would do real damage."""
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    n = conn.execute('SELECT COUNT(*) FROM kit_datasheets WHERE kit_id = ?',
                     (seeded['empty_kit'],)).fetchone()[0]
    assert n == 0
    conn.close()


def test_the_report_names_what_could_not_be_mapped(seeded, capsys):
    """"Print anything you couldn't map so I can fill it in.\""""
    import scripts.report_kit_datasheets as report
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    needs = report.report(conn)
    out = capsys.readouterr().out
    assert needs == 1
    assert 'Unidentified box' in out
    assert 'Combat Patrol: Orks' in out, 'the multi-datasheet kit is worth naming'
    conn.close()


# ── The three model states ───────────────────────────────────────────────────

def test_an_unresolved_entry_can_exist(seeded):
    """The parser's contract: a line that matched nothing becomes a visible row
    to fix, never a dropped line. That needs datasheet_id nullable, which is the
    only reason the table is rebuilt at all."""
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    conn.execute('INSERT INTO list_entries (list_id, position, raw_name, '
                 'model_count) VALUES (?, 9, ?, 1)',
                 (seeded['list_id'], 'Warboss on Warbike'))
    row = conn.execute("SELECT datasheet_id, resolved_by FROM list_entries "
                       "WHERE raw_name = 'Warboss on Warbike'").fetchone()
    assert row['datasheet_id'] is None and row['resolved_by'] is None
    conn.close()


def test_resolved_by_rejects_a_value_nobody_meant(seeded):
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('INSERT INTO list_entries (list_id, position, model_count,'
                     ' resolved_by) VALUES (?, 0, 1, ?)',
                     (seeded['list_id'], 'probably'))
    conn.close()


def test_one_spelling_means_one_datasheet(seeded):
    """An alias that could mean two things taught the picker nothing."""
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    sheets = seeded['sheets']
    conn.execute('INSERT INTO datasheet_aliases (alias, datasheet_id, '
                 'created_at) VALUES (?, ?, ?)',
                 ('warboss on warbike', sheets['Warboss'], db.now()))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('INSERT INTO datasheet_aliases (alias, datasheet_id, '
                     'created_at) VALUES (?, ?, ?)',
                     ('warboss on warbike', sheets['Boyz'], db.now()))
    conn.close()


def test_magnetised_survives_a_stage_advance(seeded):
    """Spec case 12. You set it once when you magnetise the model; it has to
    still be true after it is painted, based and battle ready."""
    import collection as col
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    unit_id = conn.execute('SELECT id FROM units LIMIT 1').fetchone()[0]
    conn.execute('UPDATE models SET is_flexible = 1 WHERE unit_id = ?',
                 (unit_id,))
    for _ in range(len(col.stage_ladder(conn))):
        col.advance_unit(conn, unit_id)
    rows = conn.execute("""
        SELECT m.is_flexible, s.is_terminal FROM models m
          JOIN stages s ON s.id = m.stage_id WHERE m.unit_id = ?""",
                        (unit_id,)).fetchall()
    assert rows and all(r['is_terminal'] for r in rows), 'expected battle ready'
    assert all(r['is_flexible'] == 1 for r in rows)
    conn.close()


def test_deleting_a_kit_takes_its_capability_rows(seeded):
    """ON DELETE CASCADE, so a deleted box leaves no orphan claim that Clay
    owns plastic that could become something."""
    _apply_008(seeded['path'])
    conn = db.connect(seeded['path'])
    conn.execute('DELETE FROM units WHERE kit_id = ?', (seeded['loose_kit'],))
    conn.execute('DELETE FROM kits WHERE id = ?', (seeded['loose_kit'],))
    n = conn.execute('SELECT COUNT(*) FROM kit_datasheets WHERE kit_id = ?',
                     (seeded['loose_kit'],)).fetchone()[0]
    assert n == 0
    conn.close()
