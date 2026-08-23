"""Route wiring, auth, and the API contracts the UI depends on."""

import json

import pytest

import collection as col
import database as db
import lists as lists_mod
import scanning


@pytest.fixture
def client(db_path, monkeypatch):
    """A logged-in test client pointed at an isolated database."""
    import app as appmod
    monkeypatch.setattr(appmod.db, 'DB_PATH', db_path)
    appmod.app.config['TESTING'] = True
    appmod._AUTH_FAILURES.clear()
    with db.connect(db_path) as conn:
        if not conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            import bcrypt
            conn.execute(
                'INSERT INTO users (id, name, password_hash, role, created_at) '
                'VALUES (?, ?, ?, ?, ?)',
                ('u1', 'Clay',
                 bcrypt.hashpw(b'pw', bcrypt.gensalt()).decode(), 'owner', db.now()))
    c = appmod.app.test_client()
    c.post('/api/auth/login', json={'login': 'Clay', 'password': 'pw'})
    return c


@pytest.fixture
def army_with_unit(db_path):
    with db.connect(db_path) as conn:
        faction_id = db.upsert_faction(conn, 'Orks', 'orks')
        cur = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'min_models, max_models, created_at, updated_at) '
            "VALUES ('boyz', 'Boyz', ?, 1, 10, 20, ?, ?)",
            (faction_id, db.now(), db.now()))
        datasheet_id = cur.lastrowid
        army_id = col.create_army(conn, 'Da Boyz')
        unit_id = col.create_unit(conn, datasheet_id, 10, army_id=army_id)
    return {'army_id': army_id, 'unit_id': unit_id, 'datasheet_id': datasheet_id}


# ── Auth ─────────────────────────────────────────────────

@pytest.mark.parametrize('path', ['/', '/kits', '/paint', '/reference'])
def test_pages_require_login(db_path, monkeypatch, path):
    import app as appmod
    monkeypatch.setattr(appmod.db, 'DB_PATH', db_path)
    anon = appmod.app.test_client()
    res = anon.get(path)
    assert res.status_code == 302 and '/login' in res.headers['Location']


def test_api_returns_401_not_a_redirect(db_path, monkeypatch):
    """A fetch() from the page needs a status it can act on, not HTML."""
    import app as appmod
    monkeypatch.setattr(appmod.db, 'DB_PATH', db_path)
    anon = appmod.app.test_client()
    assert anon.post('/api/units', json={}).status_code == 401


def test_healthz_is_public(db_path, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod.db, 'DB_PATH', db_path)
    assert appmod.app.test_client().get('/healthz').status_code == 200


# ── Pages render ─────────────────────────────────────────

@pytest.mark.parametrize('path', ['/', '/kits', '/paint', '/reference'])
def test_pages_render(client, army_with_unit, path):
    assert client.get(path).status_code == 200


def test_army_and_unit_pages_render(client, army_with_unit):
    assert client.get(f'/armies/{army_with_unit["army_id"]}').status_code == 200
    assert client.get(f'/units/{army_with_unit["unit_id"]}').status_code == 200
    assert client.get('/armies/unassigned').status_code == 200
    assert client.get(f'/paint/{army_with_unit["unit_id"]}').status_code == 200


def test_adding_a_unit_twice_makes_one_line(client, army_with_unit, db_path):
    """"if I add more of a model it needs to add them not make 2 lines."" The
    collection is where Clay saw it, so this asserts on the rendered screen and
    not only on the count of rows."""
    payload = {'datasheet_id': army_with_unit['datasheet_id'],
               'model_count': 5, 'army_id': army_with_unit['army_id']}
    first = client.post('/api/units', json=payload).get_json()
    second = client.post('/api/units', json=payload).get_json()
    assert second['id'] == first['id']
    assert second['extended'] is True

    body = client.get('/collection').get_data(as_text=True)
    assert body.count('class="unit-line"') == 1, \
        'one squad, one line — a second line is a second thing to think about'
    assert '20 models' in body


def test_collection_nests_its_unit_lines(client, army_with_unit):
    """Clay: "A line to me is a defining break between records, but not the
    case in this design." A unit line sits inside its datasheet's card, so it
    has to be wrapped in something that says so — bare siblings separated by a
    rule are what read as records in the first place."""
    body = client.get('/collection').get_data(as_text=True)
    assert 'class="unit-lines"' in body
    assert body.index('class="unit-lines"') < body.index('class="unit-line"')


def test_missing_things_404(client):
    assert client.get('/armies/999').status_code == 404
    assert client.get('/units/999').status_code == 404


# ── The primary interaction ──────────────────────────────

def test_advance_all_needs_no_body(client, army_with_unit, db_path):
    """One tap. The endpoint must work with nothing at all in the request."""
    res = client.post(f'/api/units/{army_with_unit["unit_id"]}/advance', json={})
    assert res.status_code == 200
    assert res.json['moved'] == 10
    assert len(res.json['breakdown']) == 8, 'the UI repaints from this'


def test_advance_n(client, army_with_unit):
    res = client.post(f'/api/units/{army_with_unit["unit_id"]}/advance',
                      json={'count': 4})
    assert res.json['moved'] == 4


def test_advance_from_a_single_stage(client, army_with_unit, db_path):
    with db.connect(db_path) as conn:
        sprue = db.first_owned_stage(conn)['id']
    res = client.post(f'/api/units/{army_with_unit["unit_id"]}/advance',
                      json={'count': 1, 'from_stage_id': sprue})
    assert res.json['moved'] == 1


def test_advancing_a_finished_unit_reports_zero(client, army_with_unit):
    for _ in range(10):
        client.post(f'/api/units/{army_with_unit["unit_id"]}/advance', json={})
    res = client.post(f'/api/units/{army_with_unit["unit_id"]}/advance', json={})
    assert res.status_code == 200 and res.json['moved'] == 0


# ── Bulk stage set ───────────────────────────────────────

def test_bulk_stage_set_by_model_ids(client, army_with_unit, db_path):
    with db.connect(db_path) as conn:
        models = col.unit_models(conn, army_with_unit['unit_id'])
        painted = [s for s in col.stage_ladder(conn) if s['name'] == 'Painted'][0]
    ids = [m['id'] for m in models][:3]
    res = client.post(f'/api/units/{army_with_unit["unit_id"]}/stage',
                      json={'model_ids': ids, 'stage_id': painted['id']})
    assert res.json['moved'] == 3


def test_a_unit_cannot_move_another_units_models(client, army_with_unit, db_path):
    """Guard against an id from the wrong page reaching the wrong unit."""
    with db.connect(db_path) as conn:
        other = col.create_unit(conn, army_with_unit['datasheet_id'], 2)
        foreign = [m['id'] for m in col.unit_models(conn, other)]
        painted = [s for s in col.stage_ladder(conn) if s['name'] == 'Painted'][0]
    res = client.post(f'/api/units/{army_with_unit["unit_id"]}/stage',
                      json={'model_ids': foreign, 'stage_id': painted['id']})
    assert res.json['moved'] == 0
    with db.connect(db_path) as conn:
        assert {m['stage_name'] for m in col.unit_models(conn, other)} == {'On sprue'}


def test_set_a_count_without_selecting_anything(client, army_with_unit, db_path):
    with db.connect(db_path) as conn:
        primed = [s for s in col.stage_ladder(conn) if s['name'] == 'Primed'][0]
    res = client.post(f'/api/units/{army_with_unit["unit_id"]}/stage',
                      json={'stage_id': primed['id'], 'count': 6})
    assert res.json['moved'] == 6


def test_stage_set_requires_a_stage(client, army_with_unit):
    res = client.post(f'/api/units/{army_with_unit["unit_id"]}/stage', json={})
    assert res.status_code == 400


# ── Creating things ──────────────────────────────────────

def test_create_army(client):
    res = client.post('/api/armies', json={'name': 'Speed Freeks'})
    assert res.status_code == 201 and res.json['id']


def test_army_needs_a_name(client):
    assert client.post('/api/armies', json={'name': '  '}).status_code == 400


def test_create_unit(client, army_with_unit):
    res = client.post('/api/units', json={
        'datasheet_id': army_with_unit['datasheet_id'],
        'model_count': 20, 'army_id': army_with_unit['army_id']})
    assert res.status_code == 201


def test_unit_needs_a_real_datasheet(client):
    """Never a free-text unit name — that's how a collection goes wrong."""
    assert client.post('/api/units', json={'model_count': 5}).status_code == 400


def test_unit_needs_at_least_one_model(client, army_with_unit):
    res = client.post('/api/units', json={
        'datasheet_id': army_with_unit['datasheet_id'], 'model_count': 0})
    assert res.status_code == 400


def test_move_unit_between_armies_and_back_to_unassigned(client, army_with_unit, db_path):
    res = client.post(f'/api/units/{army_with_unit["unit_id"]}/move',
                      json={'army_id': None})
    assert res.status_code == 200
    with db.connect(db_path) as conn:
        assert col.get_unit(conn, army_with_unit['unit_id'])['army_id'] is None


# ── Kits ─────────────────────────────────────────────────

def test_create_kit_stores_money_as_cents(client):
    res = client.post('/api/kits', json={'name': 'Wrecka Krew', 'cost': '55.50'})
    assert res.status_code == 201


def test_kit_disposal_records_what_it_went_for(client, db_path):
    kit_id = client.post('/api/kits', json={'name': 'Killa Kans'}).json['id']
    res = client.post(f'/api/kits/{kit_id}/status',
                      json={'status': 'sold', 'price': '40', 'note': 'to Dave'})
    assert res.status_code == 200
    with db.connect(db_path) as conn:
        kit = col.get_kit(conn, kit_id)
    assert kit['status'] == 'sold' and kit['disposed_price_cents'] == 4000


def test_unknown_kit_status_is_rejected(client):
    kit_id = client.post('/api/kits', json={'name': 'Killa Kans'}).json['id']
    res = client.post(f'/api/kits/{kit_id}/status', json={'status': 'incinerated'})
    assert res.status_code == 400


# ── Datasheet picker ─────────────────────────────────────

def test_datasheet_search_needs_two_characters(client, army_with_unit):
    assert client.get('/api/datasheets?q=B').json['results'] == []
    assert client.get('/api/datasheets?q=Boy').json['results']


# ── Scanning (step 4) ────────────────────────────────────

@pytest.fixture
def a_template(client, army_with_unit):
    """A kit template with contents, created through the API."""
    res = client.post('/api/templates', json={
        'name': 'Combat Patrol: Orks', 'year': 2024, 'code': '5011921204021',
        'contents': [{'datasheet_id': army_with_unit['datasheet_id'],
                      'model_count': 20}]})
    assert res.status_code == 201
    return res.json['id']


@pytest.mark.parametrize('path', ['/scan', '/scan/review', '/templates'])
def test_scanning_pages_render(client, army_with_unit, path):
    assert client.get(path).status_code == 200


def test_scanning_pages_require_login(db_path, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod.db, 'DB_PATH', db_path)
    anon = appmod.app.test_client()
    assert anon.get('/scan').status_code == 302
    assert anon.post('/api/scan', json={'code': '1'}).status_code == 401


def test_a_scan_is_saved_immediately(client, army_with_unit):
    res = client.post('/api/scan', json={'code': '5011921204021'})
    assert res.status_code == 201
    assert res.json['quantity'] == 1 and res.json['known'] is False
    assert res.json['summary']['open_boxes'] == 1


def test_rescanning_the_same_box_bumps_the_quantity(client, army_with_unit):
    client.post('/api/scan', json={'code': '5011921204021'})
    res = client.post('/api/scan', json={'code': '5011921204021'})
    assert res.json['quantity'] == 2 and res.json['duplicate'] is True
    assert res.json['summary']['open_rows'] == 1


def test_a_junk_code_is_rejected_but_a_real_one_never_is(client, army_with_unit):
    assert client.post('/api/scan', json={'code': 'abc'}).status_code == 400
    # A non-GW prefix is a warning, not a refusal.
    res = client.post('/api/scan', json={'code': '4006874052004'})
    assert res.status_code == 201
    assert res.json['notes'], 'it should say something about the prefix'


def test_check_endpoint_describes_a_code_without_saving_it(client, army_with_unit):
    res = client.get('/api/scan/check?code=9781839062865')
    assert res.status_code == 200
    assert res.json['known'] is False
    assert any('book' in n for n in res.json['notes'])
    assert client.get('/scan/review').status_code == 200
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) c FROM scan_queue').fetchone()['c'] == 0


def test_a_known_code_comes_back_resolved(client, a_template):
    res = client.post('/api/scan', json={'code': '5011921204021'})
    assert res.json['known'] is True
    assert res.json['name'] == 'Combat Patrol: Orks'


def test_confirming_a_scan_creates_the_kit_and_models(client, a_template, db_path):
    queue_id = client.post('/api/scan', json={'code': '5011921204021'}).json['queue_id']
    res = client.post(f'/api/scan/{queue_id}/resolve', json={'box_state': 'opened'})
    assert res.status_code == 200 and len(res.json['kits']) == 1
    with db.connect(db_path) as conn:
        # 10 from the army fixture's unit, plus 20 from the box.
        assert conn.execute('SELECT COUNT(*) c FROM models').fetchone()['c'] == 30


def test_quantity_decides_how_many_kits(client, a_template, db_path):
    queue_id = client.post('/api/scan', json={'code': '5011921204021'}).json['queue_id']
    client.post(f'/api/scan/{queue_id}/quantity', json={'quantity': 3})
    res = client.post(f'/api/scan/{queue_id}/resolve', json={})
    assert len(res.json['kits']) == 3


def test_an_unknown_code_cannot_be_confirmed(client, army_with_unit):
    queue_id = client.post('/api/scan', json={'code': '5011921999999'}).json['queue_id']
    res = client.post(f'/api/scan/{queue_id}/resolve', json={})
    assert res.status_code == 400
    assert 'template' in res.json['error']


def test_a_scan_can_be_discarded(client, army_with_unit):
    queue_id = client.post('/api/scan', json={'code': '5011921999999'}).json['queue_id']
    assert client.delete(f'/api/scan/{queue_id}').status_code == 200


def test_creating_a_template_links_its_barcode(client, a_template, db_path):
    """The step that makes every future scan of that box instant."""
    with db.connect(db_path) as conn:
        assert scanning.template_for_code(conn, '5011921204021')['id'] == a_template


def test_a_template_with_no_contents_is_refused(client, army_with_unit):
    res = client.post('/api/templates', json={'name': 'Mystery Box', 'contents': []})
    assert res.status_code == 400


def test_contents_may_arrive_as_a_json_string(client, army_with_unit):
    """Form posts send it as text; fetch() sends a list. Both must work."""
    res = client.post('/api/templates', json={
        'name': 'Wrecka Krew',
        'contents': json.dumps([{'datasheet_id': army_with_unit['datasheet_id'],
                                 'model_count': 5}])})
    assert res.status_code == 201


def test_editing_a_template_cannot_empty_it(client, a_template):
    res = client.patch(f'/api/templates/{a_template}', json={'contents': []})
    assert res.status_code == 400


def test_linking_another_barcode(client, a_template, db_path):
    res = client.post(f'/api/templates/{a_template}/barcodes',
                      json={'code': '5011921204038'})
    assert res.status_code == 201
    with db.connect(db_path) as conn:
        assert len(scanning.get_template(conn, a_template)['barcodes']) == 2


def test_linking_a_junk_barcode_is_refused(client, a_template):
    assert client.post(f'/api/templates/{a_template}/barcodes',
                       json={'code': '--'}).status_code == 400


def test_template_detail_renders(client, a_template):
    assert client.get(f'/templates/{a_template}').status_code == 200
    assert client.get('/templates/999').status_code == 404


# ── Recording a box without its contents ─────────────────
#
# The route half of tests/test_shelving.py. These exist because the review
# screen's buttons post specific shapes, and a handler that works against a
# different one is a screen that fails only in a browser.

def _models_in_kit(conn, kit_id):
    return conn.execute(
        'SELECT COUNT(*) FROM models m JOIN units u ON u.id = m.unit_id '
        'WHERE u.kit_id = ?', (kit_id,)).fetchone()[0]


def test_shelve_records_a_box_with_no_template(client, db_path):
    client.post('/api/scan', json={'code': '5011921225712'})
    with db.connect(db_path) as conn:
        queue_id = conn.execute('SELECT id FROM scan_queue').fetchone()[0]

    res = client.post(f'/api/scan/{queue_id}/shelve', json={})

    assert res.status_code == 200
    assert len(res.json['kits']) == 1
    with db.connect(db_path) as conn:
        kit = conn.execute('SELECT * FROM kits').fetchone()
        assert kit['kit_template_id'] is None
        assert kit['source_ref'] == '5011921225712'
        assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 0


def test_shelve_refuses_a_row_twice(client, db_path):
    client.post('/api/scan', json={'code': '5011921225712'})
    with db.connect(db_path) as conn:
        queue_id = conn.execute('SELECT id FROM scan_queue').fetchone()[0]

    assert client.post(f'/api/scan/{queue_id}/shelve', json={}).status_code == 200
    second = client.post(f'/api/scan/{queue_id}/shelve', json={})
    assert second.status_code == 400
    assert 'already been resolved' in second.json['error']


def test_adopt_fills_in_a_shelved_box(client, db_path, a_template):
    client.post('/api/scan', json={'code': '5011921225712'})
    with db.connect(db_path) as conn:
        queue_id = conn.execute('SELECT id FROM scan_queue').fetchone()[0]
    kit_id = client.post(f'/api/scan/{queue_id}/shelve', json={}).json['kits'][0]

    res = client.post(f'/api/kits/{kit_id}/adopt',
                      json={'kit_template_id': a_template})

    assert res.status_code == 200
    assert len(res.json['units']) == 1
    with db.connect(db_path) as conn:
        assert _models_in_kit(conn, kit_id) == 20
        assert conn.execute('SELECT name FROM kits WHERE id = ?',
                            (kit_id,)).fetchone()[0] == 'Combat Patrol: Orks'


def test_adopt_twice_is_refused_over_http(client, db_path, a_template):
    client.post('/api/scan', json={'code': '5011921225712'})
    with db.connect(db_path) as conn:
        queue_id = conn.execute('SELECT id FROM scan_queue').fetchone()[0]
    kit_id = client.post(f'/api/scan/{queue_id}/shelve', json={}).json['kits'][0]
    client.post(f'/api/kits/{kit_id}/adopt', json={'kit_template_id': a_template})

    res = client.post(f'/api/kits/{kit_id}/adopt',
                      json={'kit_template_id': a_template})

    assert res.status_code == 400
    assert 'already has contents' in res.json['error']
    with db.connect(db_path) as conn:
        assert _models_in_kit(conn, kit_id) == 20, 'the refusal must not have added a second set'


def test_the_review_screen_lists_boxes_awaiting_contents(client, db_path):
    client.post('/api/scan', json={'code': '5011921225712'})
    with db.connect(db_path) as conn:
        queue_id = conn.execute('SELECT id FROM scan_queue').fetchone()[0]
    client.post(f'/api/scan/{queue_id}/shelve', json={})

    body = client.get('/scan/review').get_data(as_text=True)

    assert 'Recorded, contents not yet known' in body
    assert 'Unidentified box 5011921225712' in body


# ── One kit: view, edit, delete ──────────────────────────
#
# The Kits table could show a kit holding "0 units, 0 models" and offer
# nowhere to go and find out why, and nothing anywhere could correct a name or
# remove a mis-scan.

def _a_kit(client, db_path, **fields):
    res = client.post('/api/kits', json={'name': 'Wrecka Krew', **fields})
    assert res.status_code == 201
    return res.json['id']


def test_the_kit_page_renders_and_lists_its_contents(client, db_path, army_with_unit):
    kit_id = _a_kit(client, db_path)
    with db.connect(db_path) as conn:
        conn.execute('UPDATE units SET kit_id = ? WHERE id = ?',
                     (kit_id, army_with_unit['unit_id']))

    body = client.get(f'/kits/{kit_id}').get_data(as_text=True)

    assert 'Wrecka Krew' in body
    assert 'Boyz' in body, 'the units inside the box are the point of the page'


def test_a_missing_kit_is_a_404(client):
    assert client.get('/kits/999').status_code == 404


def test_editing_only_touches_the_fields_sent(client, db_path):
    """A form posting three fields must not blank the other seven."""
    kit_id = _a_kit(client, db_path, notes='keep me', acquired_on='2026-01-02')

    assert client.post(f'/api/kits/{kit_id}',
                       json={'name': 'Wrecka Krew 2024'}).status_code == 200

    with db.connect(db_path) as conn:
        kit = conn.execute('SELECT * FROM kits WHERE id = ?', (kit_id,)).fetchone()
    assert kit['name'] == 'Wrecka Krew 2024'
    assert kit['notes'] == 'keep me'
    assert kit['acquired_on'] == '2026-01-02'


def test_a_kit_cannot_be_renamed_to_nothing(client, db_path):
    kit_id = _a_kit(client, db_path)
    res = client.post(f'/api/kits/{kit_id}', json={'name': '   '})
    assert res.status_code == 400
    with db.connect(db_path) as conn:
        assert conn.execute('SELECT name FROM kits WHERE id = ?',
                            (kit_id,)).fetchone()[0] == 'Wrecka Krew'


def test_deleting_takes_its_units_and_models_with_it(client, db_path, army_with_unit):
    kit_id = _a_kit(client, db_path)
    with db.connect(db_path) as conn:
        conn.execute('UPDATE units SET kit_id = ? WHERE id = ?',
                     (kit_id, army_with_unit['unit_id']))

    assert client.delete(f'/api/kits/{kit_id}').status_code == 200

    with db.connect(db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM kits').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM units').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 0


def test_deleting_a_scanned_kit_keeps_the_scan(client, db_path):
    """The scan really did happen — the queue is the audit trail of how the
    collection was built. It just stops pointing at a kit that is gone."""
    client.post('/api/scan', json={'code': '5011921225712'})
    with db.connect(db_path) as conn:
        queue_id = conn.execute('SELECT id FROM scan_queue').fetchone()[0]
    kit_id = client.post(f'/api/scan/{queue_id}/shelve', json={}).json['kits'][0]

    assert client.delete(f'/api/kits/{kit_id}').status_code == 200

    with db.connect(db_path) as conn:
        row = conn.execute('SELECT * FROM scan_queue WHERE id = ?',
                           (queue_id,)).fetchone()
    assert row is not None, 'the scan is history, not a consequence of the kit'
    assert row['kit_id'] is None


def test_deleting_a_missing_kit_is_a_404(client):
    assert client.delete('/api/kits/999').status_code == 404


def test_disposing_is_not_deleting(client, db_path, army_with_unit):
    """The invariant, asserted rather than assumed: a sold kit keeps every row."""
    kit_id = _a_kit(client, db_path)
    with db.connect(db_path) as conn:
        conn.execute('UPDATE units SET kit_id = ? WHERE id = ?',
                     (kit_id, army_with_unit['unit_id']))

    assert client.post(f'/api/kits/{kit_id}/status',
                       json={'status': 'sold', 'price': '25.00'}).status_code == 200

    with db.connect(db_path) as conn:
        kit = conn.execute('SELECT * FROM kits WHERE id = ?', (kit_id,)).fetchone()
        assert kit['status'] == 'sold'
        assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 10


# ── The collection screen (spec §2.1, §2.3) ──────────────

def test_the_collection_screen_shows_what_is_owned(client, db_path, army_with_unit):
    body = client.get('/collection').get_data(as_text=True)

    assert 'Boyz' in body
    assert '10' in body


def test_searching_the_collection_answers_for_something_unowned(client, db_path,
                                                                army_with_unit):
    """The own-it check over HTTP: a nil answer is still an answer."""
    with db.connect(db_path) as conn:
        faction = conn.execute("SELECT id FROM factions LIMIT 1").fetchone()[0]
        conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            "created_at, updated_at) VALUES ('grot', 'Gretchin', ?, 1, ?, ?)",
            (faction, db.now(), db.now()))

    body = client.get('/collection?q=Gretchin').get_data(as_text=True)

    assert 'Gretchin' in body
    assert 'You own none' in body


def test_the_bare_collection_screen_is_not_a_catalogue_dump(client, db_path,
                                                            army_with_unit):
    with db.connect(db_path) as conn:
        faction = conn.execute("SELECT id FROM factions LIMIT 1").fetchone()[0]
        conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            "created_at, updated_at) VALUES ('grot', 'Gretchin', ?, 1, ?, ?)",
            (faction, db.now(), db.now()))

    body = client.get('/collection').get_data(as_text=True)

    assert 'Boyz' in body
    assert 'Gretchin' not in body, 'unowned datasheets appear only under search'


def test_the_ownership_api_answers_for_one_datasheet(client, army_with_unit):
    res = client.get(f"/api/collection/{army_with_unit['datasheet_id']}")

    assert res.status_code == 200
    assert res.json['owns_any'] is True
    assert res.json['owned_count'] == 10


def test_the_ownership_api_404s_for_a_missing_datasheet(client):
    assert client.get('/api/collection/999').status_code == 404


def test_the_nav_leads_with_the_collection_not_the_scanner(client):
    """Spec §1: scanning is onboarding, not the point."""
    body = client.get('/collection').get_data(as_text=True)
    assert body.index('/collection') < body.index('/scan')


# ── Lists, the gap and the wishlist (spec §2.6) ──────────

def _a_list(client, name='Saturday'):
    res = client.post('/api/lists', json={'name': name})
    assert res.status_code == 201
    return res.json['id']


def test_a_list_shows_its_gap(client, db_path, army_with_unit):
    """Owns 10 Boyz, none finished; the list asks for 20."""
    list_id = _a_list(client)
    client.post(f'/api/lists/{list_id}/entries',
                json={'datasheet_id': army_with_unit['datasheet_id'],
                      'model_count': 20})

    body = client.get(f'/lists/{list_id}').get_data(as_text=True)

    assert 'to buy' in body and 'to paint' in body
    assert '10' in body


def test_a_list_needs_a_name_over_http(client):
    res = client.post('/api/lists', json={'name': '  '})
    assert res.status_code == 400


def test_raising_the_wishlist_creates_wants_not_owns(client, db_path,
                                                     army_with_unit):
    list_id = _a_list(client)
    client.post(f'/api/lists/{list_id}/entries',
                json={'datasheet_id': army_with_unit['datasheet_id'],
                      'model_count': 20})

    res = client.post(f'/api/lists/{list_id}/wishlist', json={})

    assert res.status_code == 200
    assert res.json['added'] == 10, 'owns 10 of the 20'
    with db.connect(db_path) as conn:
        wanted = conn.execute(
            'SELECT COUNT(*) FROM models m JOIN stages s ON s.id = m.stage_id '
            'WHERE s.is_owned = 0').fetchone()[0]
    assert wanted == 10


def test_raising_the_wishlist_twice_does_not_stack(client, army_with_unit):
    list_id = _a_list(client)
    client.post(f'/api/lists/{list_id}/entries',
                json={'datasheet_id': army_with_unit['datasheet_id'],
                      'model_count': 20})
    client.post(f'/api/lists/{list_id}/wishlist', json={})

    second = client.post(f'/api/lists/{list_id}/wishlist', json={})

    assert second.json['added'] == 0


def test_removing_an_entry_closes_its_gap(client, army_with_unit):
    list_id = _a_list(client)
    entry = client.post(f'/api/lists/{list_id}/entries',
                        json={'datasheet_id': army_with_unit['datasheet_id'],
                              'model_count': 20}).json['id']

    assert client.delete(f'/api/lists/entries/{entry}').status_code == 200

    body = client.get(f'/lists/{list_id}').get_data(as_text=True)
    assert 'Nothing in this list yet' in body


def test_a_missing_list_is_a_404(client):
    assert client.get('/lists/999').status_code == 404
    assert client.delete('/api/lists/999').status_code == 404
    assert client.post('/api/lists/999/wishlist', json={}).status_code == 404


def test_the_collection_screen_can_move_a_model_forward(client, db_path,
                                                        army_with_unit):
    """The front door has to be actionable. It rendered a stage bar and offered
    no way to move anything, so from the app's main screen nothing could be
    advanced at all."""
    body = client.get('/collection').get_data(as_text=True)

    assert f'data-unit="{army_with_unit["unit_id"]}"' in body
    assert 'Advance all' in body
    assert f'/units/{army_with_unit["unit_id"]}' in body, 'and it links through'


def test_a_finished_unit_offers_no_advance(client, db_path, army_with_unit):
    with db.connect(db_path) as conn:
        terminal = db.terminal_stage(conn)['id']
        conn.execute('UPDATE models SET stage_id = ? WHERE unit_id = ?',
                     (terminal, army_with_unit['unit_id']))

    body = client.get('/collection').get_data(as_text=True)

    assert 'Battle ready' in body
    assert 'Advance all' not in body


def test_each_box_advances_on_its_own(client, db_path, army_with_unit):
    """Two boxes of the same unit are one inventory row but two units, and
    advancing one must not touch the other."""
    with db.connect(db_path) as conn:
        second = col.create_unit(conn, army_with_unit['datasheet_id'], 10)

    body = client.get('/collection').get_data(as_text=True)

    assert f'data-unit="{army_with_unit["unit_id"]}"' in body
    assert f'data-unit="{second}"' in body


def test_a_wishlist_unit_says_bought_it_not_advance(client, db_path,
                                                    army_with_unit):
    """Advancing a wishlist model moves it to On sprue — it turns a want into
    something owned. That is the right action and the wrong word for it."""
    with db.connect(db_path) as conn:
        wishlist = db.wishlist_stage(conn)['id']
        col.create_unit(conn, army_with_unit['datasheet_id'], 5,
                        stage_id=wishlist)

    body = client.get('/collection').get_data(as_text=True)

    assert 'Bought it' in body
    assert '· wanted' in body


# ── The box page: one barcode, everything known about it ──

def test_an_unknown_code_offers_to_have_its_contents_defined(client):
    res = client.get('/box/5011921225712')

    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'Never seen this code' in body
    # And straight into the define flow, landing back here afterwards.
    assert 'templates?code=5011921225712&amp;next=/box/5011921225712' in body


def test_a_known_code_shows_what_is_in_the_box(client, army_with_unit, db_path):
    with db.connect(db_path) as conn:
        template = scanning.create_template(
            conn, 'Combat Patrol: Orks',
            [{'datasheet_id': army_with_unit['datasheet_id'], 'model_count': 20}],
            year=2024)
        scanning.link_barcode(conn, '5011921204021', template)

    body = client.get('/box/5011921204021').get_data(as_text=True)

    assert 'Combat Patrol: Orks' in body
    assert 'Boyz' in body


def test_the_box_page_offers_to_fill_in_every_recorded_copy(client,
                                                            army_with_unit,
                                                            db_path):
    with db.connect(db_path) as conn:
        template = scanning.create_template(
            conn, 'Combat Patrol: Orks',
            [{'datasheet_id': army_with_unit['datasheet_id'], 'model_count': 20}])
        qid = scanning.enqueue_scan(conn, '5011921204021')['queue_id']
        scanning.set_queue_quantity(conn, qid, 2)
        scanning.shelve_queue_row(conn, qid)
        scanning.link_barcode(conn, '5011921204021', template)

    body = client.get('/box/5011921204021').get_data(as_text=True)
    assert 'Fill in 2 boxes' in body

    res = client.post('/api/box/5011921204021/adopt-all', json={})
    assert res.status_code == 200
    assert len(res.get_json()['kits']) == 2
    with db.connect(db_path) as conn:
        assert col.kits_awaiting_contents(conn) == []


def test_adopt_all_on_an_undefined_code_is_refused(client):
    res = client.post('/api/box/5011921225712/adopt-all', json={})
    assert res.status_code == 400
    assert 'no kit template' in res.get_json()['error']


def test_a_typed_code_is_normalised_by_the_box_page(client):
    """Scanners and humans both add spaces and dashes."""
    assert client.get('/box/5011921-225712').status_code == 200


# ── Sweeping the queue ───────────────────────────────────

def test_sweeping_onboards_the_whole_queue(client, army_with_unit, db_path):
    with db.connect(db_path) as conn:
        template = scanning.create_template(
            conn, 'Combat Patrol: Orks',
            [{'datasheet_id': army_with_unit['datasheet_id'], 'model_count': 20}])
        scanning.link_barcode(conn, '5011921204021', template)
        scanning.enqueue_scan(conn, '5011921204021')
        scanning.enqueue_scan(conn, '5011921225712')

    res = client.post('/api/scan/sweep', json={})

    assert res.status_code == 200
    assert res.get_json() == {'confirmed': 1, 'shelved': 1,
                              'summary': {'open_rows': 0, 'open_boxes': 0,
                                          'known': 0, 'unknown': 0}}


# ── Pasting a shelf in ───────────────────────────────────

def test_the_add_page_renders(client):
    assert client.get('/add').status_code == 200


def test_preview_matches_what_it_can_and_asks_about_the_rest(client,
                                                             army_with_unit):
    res = client.post('/add/preview', data={
        'text': '20 Boyz built\n5 Nothing Real',
        'game_system': 'wh40k', 'stage_id': ''})

    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'Boyz' in body
    assert 'no datasheet with this name' in body


def test_committing_creates_the_units(client, army_with_unit, db_path):
    res = client.post('/api/add/commit', json={
        'rows': [{'datasheet_id': army_with_unit['datasheet_id'],
                  'count': 20, 'stage_word': 'built'}]})

    assert res.status_code == 200
    assert len(res.get_json()['units']) == 1
    with db.connect(db_path) as conn:
        rows = {r['name']: r for r in col.inventory(conn)}
        assert rows['Boyz']['owned_count'] == 30, 'the fixture 10 plus these 20'


def test_committing_an_unresolved_line_is_refused(client, army_with_unit):
    res = client.post('/api/add/commit', json={
        'rows': [{'datasheet_id': None, 'count': 5}]})

    assert res.status_code == 400
    assert 'still need a datasheet' in res.get_json()['error']


# ── The catalogue screen ─────────────────────────────────

@pytest.fixture
def catalogued_box(db_path):
    with db.connect(db_path) as conn:
        faction_id = db.upsert_faction(conn, 'Orks', 'orks')
        sheet = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            "created_at, updated_at) VALUES ('boyz', 'Boyz', ?, 1, ?, ?)",
            (faction_id, db.now(), db.now())).lastrowid
        import scanning as scan
        return scan.create_template(
            conn, 'Orks: Trukk Boyz',
            [{'datasheet_id': sheet, 'model_count': 11}],
            faction_id=faction_id, year=2026)


def test_the_catalogue_lists_a_box_it_knows(client, catalogued_box):
    body = client.get('/catalogue').get_data(as_text=True)
    assert 'Orks: Trukk Boyz' in body
    assert '11× Boyz' in body, 'contents show without opening the box'


def test_the_catalogue_is_in_the_nav(client):
    assert 'href="/catalogue"' in client.get('/').get_data(as_text=True)


def test_an_empty_catalogue_says_how_to_fill_it(client):
    body = client.get('/catalogue').get_data(as_text=True)
    assert 'seed/derived_kits.py' in body


def test_wanting_a_box_over_http(client, catalogued_box):
    res = client.post(f'/api/templates/{catalogued_box}/want')

    assert res.status_code == 200
    assert res.get_json()['added'] == 11
    assert 'On the wishlist' in client.get('/catalogue').get_data(as_text=True)


def test_unwanting_a_box_over_http(client, catalogued_box):
    client.post(f'/api/templates/{catalogued_box}/want')

    res = client.delete(f'/api/templates/{catalogued_box}/want')

    assert res.get_json()['removed'] == 11
    assert 'Want it' in client.get('/catalogue').get_data(as_text=True)


def test_owning_a_box_from_the_catalogue(client, catalogued_box):
    """The same action the scanner takes on a known barcode, for a box Clay
    owns but never scanned."""
    res = client.post(f'/api/templates/{catalogued_box}/own', json={})

    assert res.status_code == 201
    assert len(res.get_json()['units']) == 1
    assert 'Own' in client.get('/catalogue').get_data(as_text=True)


def test_the_catalogue_filters_to_what_is_not_owned(client, catalogued_box):
    assert 'Orks: Trukk Boyz' in client.get(
        '/catalogue?owned=no').get_data(as_text=True)

    client.post(f'/api/templates/{catalogued_box}/own', json={})

    assert 'Orks: Trukk Boyz' not in client.get(
        '/catalogue?owned=no').get_data(as_text=True)


def test_the_catalogue_searches_by_name(client, catalogued_box):
    assert 'Trukk' in client.get('/catalogue?q=Trukk').get_data(as_text=True)
    assert 'Trukk' not in client.get('/catalogue?q=Necron').get_data(as_text=True)


def test_wanting_a_box_that_does_not_exist_is_a_400(client):
    assert client.post('/api/templates/999/want').status_code == 400


# ── Home, from Tracker Wireframes §3a ────────────────────

def test_home_leads_with_the_effort_weighted_percentage(client, army_with_unit):
    body = client.get('/').get_data(as_text=True)
    assert 'battle ready' in body
    assert 'headline' in body


def test_home_offers_something_to_pick_back_up(client, army_with_unit):
    """A tracker that only keeps score is one you stop opening."""
    body = client.get('/').get_data(as_text=True)
    assert 'Pick up where you left off' in body


def test_the_armies_index_kept_its_own_screen(client, army_with_unit):
    assert client.get('/armies').status_code == 200


def test_stepping_back_over_http(client, army_with_unit):
    unit_id = army_with_unit['unit_id']
    client.post(f'/api/units/{unit_id}/advance', json={})

    res = client.post(f'/api/units/{unit_id}/retreat', json={'count': 1})

    assert res.status_code == 200
    assert res.get_json()['moved'] == 1


def test_the_collection_chip_rail_filters(client, army_with_unit):
    body = client.get('/collection').get_data(as_text=True)
    assert 'chips' in body and 'Unpainted' in body
    assert client.get('/collection?filter=unpainted').status_code == 200


def test_a_refused_reconcile_still_returns_the_true_counts(client, army_with_unit):
    """Asking for more models than the unit holds legitimately moves nothing.
    The response still has to carry the real counts, because the screen paints
    its number from this — and a count that disagrees with the data is worse
    than no count."""
    unit_id = army_with_unit['unit_id']
    stage = client.get(f'/units/{unit_id}').status_code
    assert stage == 200

    res = client.post(f'/api/units/{unit_id}/stage',
                      json={'stage_id': 2, 'count': 999})

    body = res.get_json()
    assert body['moved'] == 0, 'nothing to move — there are not 999 models'
    total = sum(s['count'] for s in body['breakdown'])
    assert total < 999, 'the breakdown reports what is really there'


def test_unit_detail_shows_the_ramp_not_the_old_count_form(client, army_with_unit):
    body = client.get(f"/units/{army_with_unit['unit_id']}").get_data(as_text=True)
    assert 'count-at' in body, 'counts are editable in place'
    assert 'id="count-form"' not in body, 'the separate Set-a-count form is gone'
    assert 'untick' in body, 'and the ramp carries −1'


def test_every_pipeline_row_carries_its_stage_id(client, army_with_unit):
    """The screen repaints by matching rows to stage ids. A row without one is
    never matched, so the model moves and the page silently does not — which is
    exactly what happened when repaintPipe changed to id-matching while this
    markup still identified rows by position. Caught here rather than by
    someone tapping advance and seeing nothing.
    """
    import re
    unit_id = army_with_unit['unit_id']
    for url in (f'/units/{unit_id}', f'/paint/{unit_id}'):
        body = client.get(url).get_data(as_text=True)
        pipeline = re.search(r'<ul class="(?:ramp|pipe)[^"]*"[^>]*>(.*?)</ul>',
                             body, re.S)
        assert pipeline, f'{url} has no pipeline to repaint'
        rows = re.findall(r'<li[^>]*>', pipeline.group(1))
        assert rows, f'{url} pipeline has no rows'
        for row in rows:
            assert 'data-stage=' in row, (
                f'{url}: a pipeline row has no data-stage, so repainting it '
                f'after an advance would silently do nothing — {row[:80]}')


# ── Money and time ───────────────────────────────────────
#
# Money is stored in cents everywhere and only becomes a symbol at the edge.
# The symbol was hardcoded in three templates with two different format
# strings, which is how a fourth ends up in a third format.

def test_money_renders_in_the_configured_currency():
    import app as appmod
    assert appmod.money(9000) == '$90.00'
    assert appmod.money(6550) == '$65.50'


def test_a_thousand_dollar_kit_is_readable():
    import app as appmod
    assert appmod.money(123456) == '$1,234.56'


def test_no_recorded_price_is_a_dash_not_zero():
    """A kit nobody priced and a kit that cost nothing are the same fact on
    screen. "$0.00" reads like a claim that it was free."""
    import app as appmod
    assert appmod.money(None) == '—'
    assert appmod.money(0) == '—'


def test_an_unknown_currency_code_still_says_which_one():
    """Better a visible "SEK 90.00" than a dollar sign on kronor."""
    import app as appmod
    assert appmod.CURRENCY_SYMBOLS.get('SEK') is None
    assert 'USD' not in appmod.CURRENCY_SYMBOLS.get('GBP', '')


def test_no_template_hardcodes_a_currency_symbol():
    """Every price goes through the filter, so changing CURRENCY changes all
    of them and not most of them."""
    import glob
    import re
    for path in glob.glob('templates/*.html'):
        body = open(path, encoding='utf-8').read()
        assert '£' not in body, f'{path} hardcodes a pound sign'
        # A bare $ is fine in prose; a $ glued to a formatted number is not.
        assert not re.search(r'\$\{\{|\$%\.2f', body), \
            f'{path} formats money without the filter'


def test_prices_on_screen_use_the_filter(client, a_template):
    body = client.get('/templates').get_data(as_text=True)
    assert '£' not in body


def test_faction_pickers_never_print_a_bare_ambiguous_name(client):
    """Two options reading "Adepta Sororitas" is two options you cannot choose
    between. Every picker prints the disambiguated label."""
    import glob
    import re
    for path in glob.glob('templates/*.html'):
        body = open(path, encoding='utf-8').read()
        for block in re.findall(r'\{%\s*for f in factions\s*%\}.*?\{%\s*endfor\s*%\}',
                                body, re.S):
            assert '{{ f.name }}' not in block, (
                f'{path} prints a bare faction name in a picker')


# ── The camera's secure-context guard ────────────────────
#
# getUserMedia refuses to run outside a secure context, so scanning does
# nothing over a plain-http LAN address or a bare Tailscale IP. The app cannot
# fix that — but "use the HTTPS address" is a poor answer to give someone
# holding a box, so it hands over the address instead of naming it. Any
# https:// origin qualifies: the Cloudflare Tunnel, or tailscale serve on the
# MagicDNS name.

def test_the_scan_page_offers_the_secure_address(client, monkeypatch):
    import app as appmod
    monkeypatch.setenv('PUBLIC_URL', 'https://tracker.example.com')

    body = client.get('/scan').get_data(as_text=True)

    assert 'https://tracker.example.com/scan' in body
    assert 'id="insecure"' in body


def test_a_trailing_slash_does_not_double_up(monkeypatch):
    """PUBLIC_URL=https://x/ must not produce https://x//scan."""
    import importlib
    import os
    monkeypatch.setenv('PUBLIC_URL', 'https://tracker.example.com/')
    value = (os.getenv('PUBLIC_URL') or '').strip().rstrip('/')
    assert value + '/scan' == 'https://tracker.example.com/scan'


def test_without_a_public_url_it_says_how_to_set_one(client, monkeypatch):
    """Rather than an empty link that goes nowhere."""
    monkeypatch.delenv('PUBLIC_URL', raising=False)
    import importlib
    import app as appmod
    body = client.get('/scan').get_data(as_text=True)

    assert 'id="insecure"' in body, 'the panel still explains the problem'


def test_the_warning_ships_hidden(client):
    """Whether the context is secure is a browser fact the server cannot know,
    so the panel is revealed by script rather than rendered conditionally —
    otherwise every secure visit would show a warning that does not apply."""
    body = client.get('/scan').get_data(as_text=True)
    import re
    panel = re.search(r'<div class="([^"]*)" id="insecure"', body)
    assert panel and 'hidden' in panel.group(1)


# ── Adding a set, three ways in ──────────────────────────
#
# By name is the door. A barcode is only quicker when the box is already in
# hand, and the camera only when there are twenty of them — so they stay as
# options rather than as the way in.

def test_the_add_a_set_screen_offers_all_three(client):
    body = client.get('/sets/new').get_data(as_text=True)
    assert 'id="set-q"' in body, 'by name'
    assert 'id="set-code"' in body, 'by code'
    assert 'href="/scan"' in body, 'with the camera'


def test_searching_sets_by_name(client, a_template):
    res = client.get('/api/templates/search?q=Combat')
    names = [r['name'] for r in res.get_json()['results']]
    assert any('Combat' in n for n in names)


def test_searching_sets_by_what_is_inside_them(client, a_template):
    """"Boyz" should find the box that holds them, not just a box called Boyz."""
    res = client.get('/api/templates/search?q=Boyz')
    assert res.get_json()['results'], 'found by contents'


def test_a_one_letter_search_returns_nothing(client, a_template):
    """Every keystroke hits this; one letter would return the whole catalogue."""
    assert client.get('/api/templates/search?q=C').get_json()['results'] == []


def test_search_results_carry_what_is_in_the_box(client, a_template):
    """So Clay can tell two similarly-named boxes apart without opening either."""
    row = client.get('/api/templates/search?q=Combat').get_json()['results'][0]
    assert 'contents' in row and 'model_count' in row
    assert 'owned_count' in row, 'and whether he already has one'


def test_the_define_form_opens_with_the_typed_name(client):
    """A name typed once should not be typed again."""
    body = client.get('/templates?name=Nobz+Mob').get_data(as_text=True)
    assert 'value="Nobz Mob"' in body
    assert 'hidden' not in body.split('id="new-template"')[0][-120:], \
        'the form is open, not collapsed'


def test_recording_a_set_by_name_invents_no_models(client):
    """The honest bargain the scanner already makes: ownership now, contents
    whenever. Guessing contents from a name is the one thing this will not do."""
    res = client.post('/api/kits', json={'name': 'Nobz Mob 2019'})

    assert res.status_code == 201
    kit_id = res.get_json()['id']
    body = client.get(f'/kits/{kit_id}').get_data(as_text=True)
    assert 'Nobz Mob 2019' in body


# ── List import (spec §2.7) ──────────────────────────────

def test_the_import_screen_exists(client):
    body = client.get('/lists/import').get_data(as_text=True)
    assert 'textarea' in body and 'name="name"' in body


def test_lists_links_to_the_importer(client):
    assert '/lists/import' in client.get('/lists').get_data(as_text=True)


def test_the_preview_shows_every_line_before_writing(client, army_with_unit):
    res = client.post('/lists/import/preview', data={
        'name': 'Saturday', 'text': '+ HQ +\n20x Boyz (200)\nTotal: 200pts'})

    body = res.get_data(as_text=True)
    assert 'Boyz' in body
    # Asserted against the rows rather than the whole page. The paste itself is
    # now echoed into a hidden field so it can be stored with the list and
    # re-read later, so "+ HQ +" appearing *somewhere* is correct — what must
    # not happen is it appearing as a unit Clay is asked to identify.
    rows = body.split('<form id="lines">', 1)[1]
    assert '+ HQ +' not in rows, 'section headings are skipped, not reported'
    assert 'Total: 200pts' not in rows


def test_importing_a_list_writes_nothing_until_confirmed(client, db_path,
                                                        army_with_unit):
    """Asserted against the data, not the page: "Saturday" is also the
    placeholder in the new-list form, so counting it on screen measures the
    template rather than the database."""
    client.post('/lists/import/preview', data={'name': 'Saturday',
                                               'text': '20x Boyz'})

    with db.connect(db_path) as conn:
        assert conn.execute(
            'SELECT COUNT(*) FROM army_lists').fetchone()[0] == 0


def test_a_list_without_a_name_is_refused(client):
    res = client.post('/api/lists/import', json={'rows': [], 'name': '  '})
    assert res.status_code == 400
    assert 'name' in res.get_json()['error'].lower()


# ── The export endpoint's auth ───────────────────────────────────────────────
#
# `api_tokens` was created by migration 001 and had no consumer until this
# endpoint. The consumer is a script, so a session cookie is no use to it.

def _mint(db_path, device='test optimiser'):
    """A token, and the plaintext to present. Only the hash is stored."""
    import hashlib
    import secrets
    token = secrets.token_urlsafe(32)
    with db.connect(db_path) as conn:
        user = conn.execute('SELECT id FROM users LIMIT 1').fetchone()
    original, db.DB_PATH = db.DB_PATH, db_path
    try:
        db.create_api_token(user['id'],
                            hashlib.sha256(token.encode()).hexdigest(), device)
    finally:
        db.DB_PATH = original
    return token


@pytest.fixture
def anon(db_path, monkeypatch):
    """A client with no session — the state a script is in."""
    import app as appmod
    monkeypatch.setattr(appmod.db, 'DB_PATH', db_path)
    appmod.app.config['TESTING'] = True
    with db.connect(db_path) as conn:
        if not conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            import bcrypt
            conn.execute(
                'INSERT INTO users (id, name, password_hash, role, created_at) '
                'VALUES (?, ?, ?, ?, ?)',
                ('u1', 'Clay',
                 bcrypt.hashpw(b'pw', bcrypt.gensalt()).decode(), 'owner',
                 db.now()))
    return appmod.app.test_client()


def test_the_export_refuses_an_anonymous_caller(anon):
    assert anon.get('/api/export/inventory').status_code == 401


def test_the_export_refuses_a_token_it_does_not_know(anon):
    got = anon.get('/api/export/inventory',
                   headers={'Authorization': 'Bearer not-a-real-token'})
    assert got.status_code == 401


def test_the_export_refuses_a_malformed_authorization_header(anon):
    for header in ('Bearer', 'Bearer   ', 'Basic abc123', 'nonsense'):
        got = anon.get('/api/export/inventory',
                       headers={'Authorization': header})
        assert got.status_code == 401, header


def test_a_minted_token_opens_the_export(anon, db_path):
    token = _mint(db_path)
    got = anon.get('/api/export/inventory',
                   headers={'Authorization': f'Bearer {token}'})
    assert got.status_code == 200
    assert 'datasheets' in got.get_json()


def test_using_a_token_records_that_it_was_used(anon, db_path):
    """The only way to tell a live token from one Clay forgot he minted."""
    token = _mint(db_path)
    with db.connect(db_path) as conn:
        assert conn.execute(
            'SELECT last_used_at FROM api_tokens').fetchone()['last_used_at'] is None
    anon.get('/api/export/inventory',
             headers={'Authorization': f'Bearer {token}'})
    with db.connect(db_path) as conn:
        assert conn.execute(
            'SELECT last_used_at FROM api_tokens').fetchone()['last_used_at']


def test_a_revoked_token_stops_working(anon, db_path):
    import hashlib
    token = _mint(db_path)
    header = {'Authorization': f'Bearer {token}'}
    assert anon.get('/api/export/inventory', headers=header).status_code == 200
    original, db.DB_PATH = db.DB_PATH, db_path
    try:
        db.delete_api_token_by_hash(hashlib.sha256(token.encode()).hexdigest())
    finally:
        db.DB_PATH = original
    assert anon.get('/api/export/inventory', headers=header).status_code == 401


def test_a_session_opens_the_export_too(client):
    """So it stays clickable in a browser while developing, which is how the
    shape gets checked without writing a client first."""
    assert client.get('/api/export/inventory').status_code == 200


def test_a_token_does_not_open_the_rest_of_the_api(anon, db_path):
    """Deliberately narrower than the spec's "use api_tokens". A token that can
    read the inventory is a very different thing to leave in a script's config
    than one that can delete a kit, and widening it should be a decision rather
    than a side effect."""
    token = _mint(db_path)
    header = {'Authorization': f'Bearer {token}'}
    assert anon.get('/api/collection/1', headers=header).status_code == 401
    assert anon.post('/api/units', json={}, headers=header).status_code == 401


def test_the_export_takes_its_parameters_from_the_query(client, army_with_unit):
    got = client.get('/api/export/inventory?include_unassigned=true'
                     '&include_capability=false').get_json()
    assert got['datasheets']
    assert all('buildable_from_spare' not in d for d in got['datasheets'])


def test_the_export_refuses_a_format_it_cannot_write(client):
    assert client.get('/api/export/inventory?format=xml').status_code == 400


def test_an_army_that_does_not_exist_is_a_404(client):
    assert client.get('/api/export/inventory?army_id=9999').status_code == 404


def test_the_csv_form_flattens(client, army_with_unit):
    """"csv flattens, dropping by_stage and points." A nested structure encoded
    inside a CSV cell is a thing every consumer parses slightly differently."""
    got = client.get('/api/export/inventory?format=csv&include_unassigned=true')
    assert got.status_code == 200
    assert got.mimetype == 'text/csv'
    body = got.get_data(as_text=True)
    header = body.splitlines()[0]
    assert 'bsdata_id' in header and 'owned' in header
    assert 'by_stage' not in header and 'points' not in header


# ── The reference screen says which rules revision this is ───────────────────

def test_reference_names_the_manual_the_points_came_from(client):
    """"Am I quoting current points?" had no answer short of a shell, and a
    list priced from a superseded manual is wrong in the one way that does not
    look wrong."""
    body = client.get('/reference').get_data(as_text=True)
    assert 'Where this came from' in body
    assert 'Munitorum points' in body
    import rules_data
    assert rules_data.MFM_SHA[:12] in body, 'the pin, so a fetch is reproducible'


def test_reference_warns_when_the_files_are_newer_than_the_database(
        client, db_path, monkeypatch):
    """The files were updated and the importer never re-run. Every points
    figure in the app is the older manual's."""
    import app as appmod
    with db.connect(db_path) as conn:
        sheet = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, effort, created_at, '
            'updated_at) VALUES (?, ?, 1, ?, ?)',
            ('boyz', 'Boyz', db.now(), db.now())).lastrowid
        conn.execute('INSERT INTO datasheet_points (datasheet_id, model_count, '
                     'points, tier_min, effective_from) '
                     "VALUES (?, 10, 90, 1, '2026-08-05')", (sheet,))
    monkeypatch.setattr(appmod.rules_data, 'mfm_meta',
                        lambda: {'version': '1.3', 'lastUpdated': '2026-09-02'})
    body = client.get('/reference').get_data(as_text=True)
    assert 'newer than the points in the database' in body
    assert 'scripts/import_bsdata.py' in body


def test_reference_does_not_reach_the_network_to_render(client, monkeypatch):
    """A page that could not render because GitHub was down would be a worse
    page. "Has upstream moved?" belongs to the weekly sweep."""
    import rules_data

    def forbidden(*a, **kw):
        raise AssertionError('/reference asked the network for something')

    monkeypatch.setattr(rules_data.subprocess, 'run', forbidden)
    assert client.get('/reference').status_code == 200


# ── The gap report's own screen and controls ─────────────────────────────────

@pytest.fixture
def gap_list(db_path, army_with_unit):
    """A list whose report has something to say: one entry covered, one short.

    `army_with_unit` owns ten Boyz, so asking for twenty leaves ten missing —
    which is the case the whole gap checker exists for.
    """
    with db.connect(db_path) as conn:
        list_id = lists_mod.create_list(conn, 'Saturday',
                                        raw_text='20x Boyz [180pts]',
                                        source_format='gw_app')
        entry = lists_mod.add_entry(conn, list_id,
                                    army_with_unit['datasheet_id'], 20)
        unresolved = conn.execute(
            'INSERT INTO list_entries (list_id, position, raw_name, '
            'model_count) VALUES (?, 9, ?, 1)',
            (list_id, 'Warboss on Warbike')).lastrowid
    return {'list_id': list_id, 'entry': entry, 'unresolved': unresolved,
            **army_with_unit}


def test_the_report_renders_the_row_states(client, gap_list):
    body = client.get(f'/lists/{gap_list["list_id"]}').get_data(as_text=True)
    assert 'row-short' in body, 'ten owned against twenty needed'
    assert 'row-unresolved' in body
    assert 'to buy' in body and 'swaps' in body


def test_the_report_says_unresolved_rows_count_toward_nothing(client, gap_list):
    """"Never let an unresolved row quietly deflate the numbers." Saying so is
    part of the requirement, not a nicety."""
    body = client.get(f'/lists/{gap_list["list_id"]}').get_data(as_text=True)
    assert 'counted in none of this' in body


def test_the_report_is_recomputed_on_every_load(client, gap_list, db_path):
    """"Paint three Meganobz, reload the list, the numbers move.\""""
    import collection as col
    first = client.get(f'/lists/{gap_list["list_id"]}').get_data(as_text=True)
    assert 'row-short' in first
    with db.connect(db_path) as conn:
        col.add_or_extend_unit(conn, gap_list['datasheet_id'], 10)
    second = client.get(f'/lists/{gap_list["list_id"]}').get_data(as_text=True)
    assert 'row-short' not in second, 'buying ten more closes the gap'


def test_the_unassigned_toggle_changes_the_answer(client, gap_list, db_path):
    import collection as col
    with db.connect(db_path) as conn:
        col.add_or_extend_unit(conn, gap_list['datasheet_id'], 10)
    everything = client.get(f'/lists/{gap_list["list_id"]}').get_data(as_text=True)
    committed = client.get(
        f'/lists/{gap_list["list_id"]}?include_unassigned=0').get_data(as_text=True)
    assert 'row-short' not in everything
    assert 'row-short' in committed, 'the extra ten are in no army'


def test_resolving_a_row_teaches_the_alias_and_re_runs(client, gap_list, db_path):
    got = client.patch(
        f'/api/lists/{gap_list["list_id"]}/entries/{gap_list["unresolved"]}',
        json={'datasheet_id': gap_list['datasheet_id']})
    assert got.status_code == 200
    assert 'gap' in got.get_json(), 'the numbers move on the same request'
    with db.connect(db_path) as conn:
        alias = conn.execute('SELECT datasheet_id FROM datasheet_aliases '
                             "WHERE alias = 'warboss on warbike'").fetchone()
    assert alias and alias['datasheet_id'] == gap_list['datasheet_id']


def test_resolving_without_a_datasheet_is_refused(client, gap_list):
    got = client.patch(
        f'/api/lists/{gap_list["list_id"]}/entries/{gap_list["unresolved"]}',
        json={})
    assert got.status_code == 400


def test_the_report_response_carries_no_model_ids(client, gap_list):
    """A picker only needs the numbers. Shipping a few hundred assignment rows
    to move one badge is waste."""
    got = client.patch(
        f'/api/lists/{gap_list["list_id"]}/entries/{gap_list["unresolved"]}',
        json={'datasheet_id': gap_list['datasheet_id']}).get_json()
    assert 'entries' not in got['gap']
    assert 'short' in got['gap']


def test_a_pasted_list_can_be_read_again(client, gap_list, db_path):
    """"When the parser gets better, old lists can be re-parsed without
    re-pasting.\""""
    got = client.post(f'/api/lists/{gap_list["list_id"]}/reparse')
    assert got.status_code == 200
    assert got.get_json()['resolved'] == 1
    with db.connect(db_path) as conn:
        rows = conn.execute('SELECT raw_name, model_count FROM list_entries '
                            'WHERE list_id = ?',
                            (gap_list['list_id'],)).fetchall()
    assert len(rows) == 1 and rows[0]['model_count'] == 20


def test_re_reading_keeps_what_you_taught_it(client, gap_list, db_path):
    """The reason throwing the rows away is safe: resolving one wrote an alias,
    and the alias is the first thing resolution consults."""
    with db.connect(db_path) as conn:
        conn.execute('UPDATE army_lists SET raw_text = ? WHERE id = ?',
                     ('Warboss on Warbike', gap_list['list_id']))
    client.patch(
        f'/api/lists/{gap_list["list_id"]}/entries/{gap_list["unresolved"]}',
        json={'datasheet_id': gap_list['datasheet_id']})
    got = client.post(f'/api/lists/{gap_list["list_id"]}/reparse').get_json()
    assert got['resolved'] == 1 and got['unresolved'] == 0


def test_a_hand_built_list_has_no_text_to_re_read(client, db_path):
    with db.connect(db_path) as conn:
        list_id = lists_mod.create_list(conn, 'By hand')
    got = client.post(f'/api/lists/{list_id}/reparse')
    assert got.status_code == 400
    assert 'not pasted' in got.get_json()['error']


# ── What a multi-option box got built as ─────────────────────────────────────

@pytest.fixture
def armiger(db_path):
    """A kit that builds two things, which is the only case this asks about."""
    import collection as col
    with db.connect(db_path) as conn:
        faction = db.upsert_faction(conn, 'Imperial Knights', 'imperial-knights')
        sheets = {}
        for bsid, name in (('warglaive', 'Armiger Warglaive'),
                           ('helverin', 'Armiger Helverin')):
            sheets[name] = conn.execute(
                'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
                'created_at, updated_at) VALUES (?, ?, ?, 4, ?, ?)',
                (bsid, name, faction, db.now(), db.now())).lastrowid
        kit_id = col.create_kit(conn, 'Armiger box')
        for datasheet_id in sheets.values():
            conn.execute('INSERT OR IGNORE INTO kit_datasheets (kit_id, '
                         'datasheet_id) VALUES (?, ?)', (kit_id, datasheet_id))
        unit = col.add_or_extend_unit(conn, sheets['Armiger Warglaive'], 1,
                                      kit_id=kit_id)
    return {'unit_id': unit['unit_id'], **sheets}


def test_a_multi_option_kit_asks_what_it_was_built_as(client, armiger):
    body = client.get(f'/units/{armiger["unit_id"]}').get_data(as_text=True)
    assert 'What did this get built as' in body
    assert 'Armiger Helverin' in body and 'Magnetised' in body


def test_a_single_option_kit_is_not_asked_about(client, army_with_unit):
    """Most kits build one thing, `add_models` already stamped it, and there is
    nothing to ask. A prompt on every unit would be noise."""
    body = client.get(f'/units/{army_with_unit["unit_id"]}').get_data(as_text=True)
    assert 'What did this get built as' not in body


def test_saying_what_it_was_built_as_moves_the_models(client, armiger, db_path):
    got = client.post(f'/api/units/{armiger["unit_id"]}/built-as',
                      json={'datasheet_id': armiger['Armiger Helverin'],
                            'is_flexible': True})
    assert got.status_code == 200
    with db.connect(db_path) as conn:
        row = conn.execute('SELECT datasheet_id, is_flexible FROM models '
                           'WHERE unit_id = ?', (armiger['unit_id'],)).fetchone()
    assert row['datasheet_id'] == armiger['Armiger Helverin']
    assert row['is_flexible'] == 1


def test_a_box_cannot_be_built_as_something_it_never_contained(client, armiger,
                                                               army_with_unit):
    """Letting it would put models in the gap report that do not exist, which
    is the failure the whole gap checker is for."""
    got = client.post(f'/api/units/{armiger["unit_id"]}/built-as',
                      json={'datasheet_id': army_with_unit['datasheet_id']})
    assert got.status_code == 400


def test_the_report_offers_a_magnetised_model_to_the_other_datasheet(
        client, armiger, db_path):
    """End to end, through the routes: say it is magnetised, then ask a list
    for the datasheet it is not currently built as."""
    client.post(f'/api/units/{armiger["unit_id"]}/built-as',
                json={'datasheet_id': armiger['Armiger Warglaive'],
                      'is_flexible': True})
    import collection as col
    with db.connect(db_path) as conn:
        unit = col.get_unit(conn, armiger['unit_id'])
        while col.advance_unit(conn, armiger['unit_id']):
            pass
        list_id = lists_mod.create_list(conn, 'Knights')
        lists_mod.add_entry(conn, list_id, armiger['Armiger Helverin'], 1)
    body = client.get(f'/lists/{list_id}').get_data(as_text=True)
    assert 'row-swappable' in body
    assert 'Which models' in body, 'and it says which ones, and what they are'


def test_every_picker_has_somewhere_to_put_its_results(client, gap_list,
                                                       army_with_unit):
    """A picker outside a form used to throw on `null.closest('form')` at page
    load, and a form with pickers but no `.results` gave them nothing to render
    into — which is what both paste-confirmation screens shipped as. Only the
    "did you mean" buttons ever worked there, so it went unnoticed.

    Asserted at the template level because that is where it can be checked
    without a browser: any screen carrying a picker either supplies a results
    list or is one the script builds one for. The browser pass is what proved
    the fix; this is what stops it coming back.
    """
    import os
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, 'static', 'js', 'app.js'),
              encoding='utf-8') as fh:
        script = fh.read()
    assert 'function resultsListFor' in script, \
        'the picker must be able to build its own results list'
    # And nothing may assume a form is there without checking.
    unguarded = re.findall(r"input\.closest\('form'\)\.\w", script)
    assert not unguarded, f'unguarded closest(form): {unguarded}'


def test_the_resolve_button_does_not_shadow_its_own_row(client, gap_list):
    """`data-entry` sat on the row *and* the button, so `closest` from the
    button matched the button — the click read no datasheet and silently did
    nothing at all."""
    body = client.get(f'/lists/{gap_list["list_id"]}').get_data(as_text=True)
    row = body.split('row-unresolved', 1)[1].split('</li>', 1)[0]
    assert 'data-resolve=' in row
    assert 'entry-resolve" data-entry=' not in row
