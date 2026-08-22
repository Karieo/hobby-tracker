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


# ── Sweeping the whole queue ─────────────────────────────

def test_sweep_confirms_known_and_shelves_unknown(conn, template):
    """One tap on onboarding day: template-backed rows become kits with
    models, unknown codes become recorded boxes awaiting contents."""
    known = _queue(conn, '5011921204021')      # linked to the template below
    scan.link_barcode(conn, '5011921204021', template)
    unknown = _queue(conn, '5011921225712')

    result = scan.sweep_queue(conn)

    assert len(result['confirmed']) == 1
    assert len(result['shelved']) == 1
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 20
    assert len(col.kits_awaiting_contents(conn)) == 1
    for qid in (known, unknown):
        assert conn.execute('SELECT resolved_at FROM scan_queue WHERE id = ?',
                            (qid,)).fetchone()['resolved_at']


def test_sweep_honours_quantity(conn):
    _queue(conn, '5011921225712', quantity=3)
    result = scan.sweep_queue(conn)
    assert len(result['shelved']) == 3


def test_sweep_shelves_a_template_with_no_contents(conn):
    """Mirrors the per-row 'ready' rule: resolving an empty template would
    fail, and the box still deserves to be recorded."""
    empty = _empty_template(conn)
    qid = _queue(conn, '5011921111111')
    scan.link_barcode(conn, '5011921111111', empty)

    result = scan.sweep_queue(conn)

    assert result['confirmed'] == []
    assert len(result['shelved']) == 1
    assert conn.execute('SELECT COUNT(*) FROM units').fetchone()[0] == 0


def test_sweeping_an_empty_queue_is_a_no_op(conn):
    assert scan.sweep_queue(conn) == {'confirmed': [], 'shelved': []}


def test_sweep_applies_the_stage_default_to_confirmed_models(conn, template):
    stages = {s['name']: s['id'] for s in col.stage_ladder(conn)}
    scan.link_barcode(conn, '5011921204021', template)
    _queue(conn, '5011921204021')

    scan.sweep_queue(conn, stage_id=stages['Assembled'])

    at = {r['stage_id'] for r in conn.execute('SELECT stage_id FROM models')}
    assert at == {stages['Assembled']}


# ── Its own barcode says what it is ──────────────────────

def test_a_shelved_box_suggests_the_template_its_code_now_links_to(conn, template):
    """Defining Combat Patrol's contents once pays for every copy already on
    the shelf — the boxes were recorded before anyone knew what they were."""
    scan.shelve_queue_row(conn, _queue(conn, '5011921204021'))
    assert col.kits_awaiting_contents(conn)[0]['suggested_template_id'] is None

    scan.link_barcode(conn, '5011921204021', template)

    row = col.kits_awaiting_contents(conn)[0]
    assert row['suggested_template_id'] == template
    assert row['suggested_template_name'] == 'Combat Patrol: Orks'


def test_a_template_with_no_contents_is_never_suggested(conn):
    """Adopting it would fail, so offering it is a dead end."""
    empty = _empty_template(conn)
    scan.shelve_queue_row(conn, _queue(conn, '5011921111111'))
    scan.link_barcode(conn, '5011921111111', empty)

    assert col.kits_awaiting_contents(conn)[0]['suggested_template_id'] is None


def test_a_hand_added_box_has_no_code_and_no_suggestion(conn):
    col.create_kit(conn, 'Something from a bring-and-buy')
    row = col.kits_awaiting_contents(conn)[0]
    assert row['code'] is None
    assert row['suggested_template_id'] is None


# ── Filling in every copy at once ────────────────────────

def test_adopt_all_fills_every_recorded_copy_of_the_code(conn, template):
    scan.shelve_queue_row(conn, _queue(conn, '5011921204021', quantity=3))
    scan.link_barcode(conn, '5011921204021', template)

    filled = col.adopt_all_for_code(conn, '5011921204021')

    assert len(filled) == 3
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 60
    assert col.kits_awaiting_contents(conn) == []


def test_adopt_all_finds_the_template_from_the_barcode(conn, template):
    """Clay scanned a box; the code is the only thing he had to supply."""
    scan.shelve_queue_row(conn, _queue(conn, '5011921204021'))
    scan.link_barcode(conn, '5011921204021', template)

    assert len(col.adopt_all_for_code(conn, '5011921204021')) == 1


def test_adopt_all_skips_boxes_already_filled_in(conn, template):
    """A part-filled shelf is the normal state halfway through, and failing
    the whole action over one finished box is its own dead end."""
    kits = scan.shelve_queue_row(conn, _queue(conn, '5011921204021', quantity=2))
    scan.link_barcode(conn, '5011921204021', template)
    col.adopt_template(conn, kits[0], template)

    filled = col.adopt_all_for_code(conn, '5011921204021')

    assert filled == [kits[1]]
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 40


def test_adopt_all_refuses_an_unlinked_code(conn):
    scan.shelve_queue_row(conn, _queue(conn, '5011921204021'))
    with pytest.raises(ValueError, match='no kit template'):
        col.adopt_all_for_code(conn, '5011921204021')


def test_adopt_all_leaves_other_codes_alone(conn, template):
    scan.shelve_queue_row(conn, _queue(conn, '5011921204021'))
    scan.shelve_queue_row(conn, _queue(conn, '5011921225712'))
    scan.link_barcode(conn, '5011921204021', template)

    col.adopt_all_for_code(conn, '5011921204021')

    left = col.kits_awaiting_contents(conn)
    assert [k['code'] for k in left] == ['5011921225712']


def test_adopt_all_honours_the_stage_default(conn, template):
    stages = {s['name']: s['id'] for s in col.stage_ladder(conn)}
    scan.shelve_queue_row(conn, _queue(conn, '5011921204021'))
    scan.link_barcode(conn, '5011921204021', template)

    col.adopt_all_for_code(conn, '5011921204021', stage_id=stages['Assembled'])

    at = {r['stage_id'] for r in conn.execute('SELECT stage_id FROM models')}
    assert at == {stages['Assembled']}
