"""Recording a box without knowing what is in it.

Defining contents costs a form per distinct product. With ~100 boxes and no
catalogue to seed from, that is hours of typing before the app has recorded
anything at all — the friction the spec says kills trackers. So ownership is
recorded on its own, and contents arrive later.

The bargain only works if the second half exists, so adopt_template is tested
as hard as the shelving is.
"""

import pytest

import collection as col
import database as db
import scanning as scan


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / 't.db')
    db.migrate(path)
    c = db.connect(path)
    yield c
    c.close()


@pytest.fixture
def boyz(conn):
    faction = db.upsert_faction(conn, 'Orks', 'orks')
    cur = conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
        'created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)',
        ('boyz', 'Boyz', faction, db.now(), db.now()))
    return cur.lastrowid


@pytest.fixture
def template(conn, boyz):
    return scan.create_template(
        conn, 'Combat Patrol: Orks',
        [{'datasheet_id': boyz, 'model_count': 20}], year=2024)


def _empty_template(conn, name='Contents never defined'):
    """create_template refuses these, so one can only arrive by another route —
    a seed writing rows directly, or a future importer. The guards downstream
    still have to hold if one turns up."""
    cur = conn.execute(
        'INSERT INTO kit_templates (name, created_at, updated_at) '
        'VALUES (?, ?, ?)', (name, db.now(), db.now()))
    return cur.lastrowid


def _queue(conn, code='5011921225712', quantity=1):
    result = scan.enqueue_scan(conn, code)
    qid = result['queue_id'] if isinstance(result, dict) else result
    if quantity != 1:
        scan.set_queue_quantity(conn, qid, quantity)
    return qid


# ── Recording the box ────────────────────────────────────

def test_a_box_can_be_recorded_with_no_template_at_all(conn):
    kit_ids = scan.shelve_queue_row(conn, _queue(conn))

    assert len(kit_ids) == 1
    kit = conn.execute('SELECT * FROM kits WHERE id = ?', (kit_ids[0],)).fetchone()
    assert kit['kit_template_id'] is None
    assert kit['status'] == 'owned'
    # The code is kept on the kit: it is the only identifying thing known.
    assert kit['source_ref'] == '5011921225712'


def test_the_placeholder_name_carries_the_code(conn):
    kit_ids = scan.shelve_queue_row(conn, _queue(conn))
    name = conn.execute('SELECT name FROM kits WHERE id = ?',
                        (kit_ids[0],)).fetchone()['name']
    assert name == 'Unidentified box 5011921225712'


def test_a_name_clay_types_is_kept(conn):
    kit_ids = scan.shelve_queue_row(conn, _queue(conn), name='  Ork Boyz  ')
    name = conn.execute('SELECT name FROM kits WHERE id = ?',
                        (kit_ids[0],)).fetchone()['name']
    assert name == 'Ork Boyz'


def test_quantity_makes_that_many_boxes(conn):
    kit_ids = scan.shelve_queue_row(conn, _queue(conn, quantity=3))
    assert len(kit_ids) == 3
    assert len(set(kit_ids)) == 3


def test_shelving_resolves_the_queue_row(conn):
    qid = _queue(conn)
    scan.shelve_queue_row(conn, qid)
    row = conn.execute('SELECT * FROM scan_queue WHERE id = ?', (qid,)).fetchone()
    assert row['resolved_at']
    assert row['kit_id']


def test_a_row_cannot_be_shelved_twice(conn):
    qid = _queue(conn)
    scan.shelve_queue_row(conn, qid)
    with pytest.raises(ValueError, match='already been resolved'):
        scan.shelve_queue_row(conn, qid)


def test_no_models_are_invented(conn):
    """The whole point. A shelved box asserts ownership and nothing else."""
    scan.shelve_queue_row(conn, _queue(conn))
    assert conn.execute('SELECT COUNT(*) FROM units').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 0


# ── The backlog stays visible ────────────────────────────

def test_a_shelved_box_shows_up_as_awaiting_contents(conn):
    scan.shelve_queue_row(conn, _queue(conn))
    awaiting = col.kits_awaiting_contents(conn)
    assert len(awaiting) == 1
    assert awaiting[0]['code'] == '5011921225712'


def test_a_kit_with_contents_is_not_in_the_backlog(conn, template):
    col.instantiate_template(conn, template)
    assert col.kits_awaiting_contents(conn) == []


def test_a_disposed_box_leaves_the_backlog(conn):
    """Ownership ended; its unknown contents stopped being a question."""
    kit_ids = scan.shelve_queue_row(conn, _queue(conn))
    col.dispose_kit(conn, kit_ids[0], 'sold')
    assert col.kits_awaiting_contents(conn) == []


# ── Filling it in later ──────────────────────────────────

def test_adopting_a_template_creates_the_contents(conn, template):
    kit_id = scan.shelve_queue_row(conn, _queue(conn))[0]

    unit_ids = col.adopt_template(conn, kit_id, template)

    assert len(unit_ids) == 1
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 20
    kit = conn.execute('SELECT * FROM kits WHERE id = ?', (kit_id,)).fetchone()
    assert kit['kit_template_id'] == template
    assert col.kits_awaiting_contents(conn) == []


def test_adopting_replaces_the_placeholder_name(conn, template):
    kit_id = scan.shelve_queue_row(conn, _queue(conn))[0]
    col.adopt_template(conn, kit_id, template)
    name = conn.execute('SELECT name FROM kits WHERE id = ?',
                        (kit_id,)).fetchone()['name']
    assert name == 'Combat Patrol: Orks'


def test_adopting_keeps_a_name_clay_chose(conn, template):
    kit_id = scan.shelve_queue_row(conn, _queue(conn), name="Ork box from Dave")[0]
    col.adopt_template(conn, kit_id, template)
    name = conn.execute('SELECT name FROM kits WHERE id = ?',
                        (kit_id,)).fetchone()['name']
    assert name == 'Ork box from Dave'


def test_adopting_twice_is_refused(conn, template):
    """A kit holding two copies of its contents overstates every count that
    matters, and nothing in the UI would show it."""
    kit_id = scan.shelve_queue_row(conn, _queue(conn))[0]
    col.adopt_template(conn, kit_id, template)

    with pytest.raises(ValueError, match='already has contents'):
        col.adopt_template(conn, kit_id, template)
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 20


def test_an_empty_template_cannot_be_adopted(conn):
    kit_id = scan.shelve_queue_row(conn, _queue(conn))[0]
    empty = _empty_template(conn)
    with pytest.raises(ValueError, match='no contents defined'):
        col.adopt_template(conn, kit_id, empty)


def test_only_adoptable_templates_are_offered(conn, template):
    _empty_template(conn)
    offered = col.list_templates_with_contents(conn)
    assert [t['id'] for t in offered] == [template]
    assert offered[0]['model_count'] == 20
