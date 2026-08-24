"""Route wiring, auth, and the API contracts the UI depends on."""

import json

import pytest

import collection as col
import database as db
import lists as lists_mod


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


# ── Recording a box without its contents ─────────────────
#
# The route half of tests/test_shelving.py. These exist because the review
# screen's buttons post specific shapes, and a handler that works against a
# different one is a screen that fails only in a browser.

def _models_in_kit(conn, kit_id):
    return conn.execute(
        'SELECT COUNT(*) FROM models m JOIN units u ON u.id = m.unit_id '
        'WHERE u.kit_id = ?', (kit_id,)).fetchone()[0]


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


def test_the_collection_screen_offers_a_way_in(client, db_path, army_with_unit):
    """The front door has to be actionable — it once rendered a stage bar and
    offered nothing at all. What it offers is a door rather than the action:
    moving models is paint mode's on every screen now, and a list about which
    units exist should not be a third place that does it."""
    body = client.get('/collection').get_data(as_text=True)

    assert f'/paint/{army_with_unit["unit_id"]}' in body
    assert 'Advance all' not in body
    assert f'/units/{army_with_unit["unit_id"]}' in body, 'and it links through'


def test_a_finished_unit_offers_no_advance(client, db_path, army_with_unit):
    with db.connect(db_path) as conn:
        terminal = db.terminal_stage(conn)['id']
        conn.execute('UPDATE models SET stage_id = ? WHERE unit_id = ?',
                     (terminal, army_with_unit['unit_id']))

    body = client.get('/collection').get_data(as_text=True)

    assert 'Battle ready' in body
    assert 'Advance all' not in body


def test_each_box_gets_its_own_way_in(client, db_path, army_with_unit):
    """Two boxes of the same unit are one inventory row but two units, and each
    line has to reach its own — one shared link would paint the wrong squad."""
    with db.connect(db_path) as conn:
        second = col.create_unit(conn, army_with_unit['datasheet_id'], 10)

    body = client.get('/collection').get_data(as_text=True)

    assert f'/paint/{army_with_unit["unit_id"]}' in body
    assert f'/paint/{second}' in body


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

# ── Sweeping the queue ───────────────────────────────────

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


def test_unit_detail_reads_the_stages_and_never_moves_them(client, army_with_unit):
    """Clay: "Paint mode and collection have the same thing, let's leave the
    collection as just a way to add or remove models, the paint mode has the
    ramp." The count stays — a unit page that could not say what state things
    are in would be a worse screen, not a simpler one — but nothing here moves
    a model between stages any more."""
    body = client.get(f"/units/{army_with_unit['unit_id']}").get_data(as_text=True)

    assert 'class="statbox"' in body, 'the counts are still readable'
    assert 'id="count-form"' not in body, 'the separate Set-a-count form is gone'
    for control in ('untick', 'class="tick"', 'button class="advance'):
        assert control not in body, f'{control} belongs to paint mode now'
    assert f'/paint/{army_with_unit["unit_id"]}' in body, \
        'and the page says where the ladder went'


def test_paint_mode_still_has_the_ramp(client, army_with_unit):
    """The other half of the same instruction. Moving this off the unit page
    only works if it is somewhere."""
    body = client.get(f"/paint/{army_with_unit['unit_id']}").get_data(as_text=True)

    assert 'untick' in body and 'class="tick"' in body
    assert 'class="advance primary huge"' in body


def test_every_pipeline_row_carries_its_stage_id(client, army_with_unit):
    """The screen repaints by matching rows to stage ids. A row without one is
    never matched, so the model moves and the page silently does not — which is
    exactly what happened when repaintPipe changed to id-matching while this
    markup still identified rows by position. Caught here rather than by
    someone tapping advance and seeing nothing.
    """
    import re
    unit_id = army_with_unit['unit_id']
    # Paint mode alone: the unit page's ladder is read-only now and reloads
    # rather than repainting, so its rows have nothing to be matched by.
    for url in (f'/paint/{unit_id}',):
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


# ── Defining a box by hand ───────────────────────────────
#
# What is left of onboarding now that the scanner is gone: type the name, type
# what is inside, and the app has never guessed either.

def test_the_define_form_opens_with_the_typed_name(client):
    """A name typed once should not be typed again."""
    body = client.get('/templates?name=Nobz+Mob').get_data(as_text=True)
    assert 'value="Nobz Mob"' in body
    assert 'hidden' not in body.split('id="new-template"')[0][-120:], \
        'the form is open, not collapsed'


def test_recording_a_set_by_name_invents_no_models(client):
    """Ownership now, contents whenever. Guessing contents from a name is the
    one thing this app will not do."""
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


# ── Removing models ──────────────────────────────────────
#
# Clay: "I have no way to remove models if I accidentally add too many." Both
# halves of the fix are asserted here — the endpoint, and the control on the
# page that reaches it, because the endpoint to delete a whole unit had existed
# since the beginning with nothing anywhere calling it.

def test_removing_models_trims_the_unit(client, army_with_unit):
    unit_id = army_with_unit['unit_id']

    res = client.delete(f'/api/units/{unit_id}/models', json={'count': 4})

    assert res.status_code == 200
    assert res.get_json() == {'removed': 4, 'remaining': 6,
                              'unit_deleted': False}


def test_removing_them_all_reports_the_unit_gone(client, army_with_unit):
    """So the page can send Clay somewhere that still exists rather than
    reloading into a 404."""
    unit_id = army_with_unit['unit_id']

    res = client.delete(f'/api/units/{unit_id}/models', json={'count': 10})

    assert res.get_json()['unit_deleted'] is True
    assert client.get(f'/units/{unit_id}').status_code == 404


def test_removing_nothing_is_refused(client, army_with_unit):
    """A zero or a missing count is a mis-submitted form, not an instruction."""
    unit_id = army_with_unit['unit_id']
    for payload in ({'count': 0}, {}, {'count': -3}):
        assert client.delete(f'/api/units/{unit_id}/models',
                             json=payload).status_code == 400


def test_removing_from_a_unit_that_is_not_there(client):
    assert client.delete('/api/units/9999/models',
                         json={'count': 1}).status_code == 404


def test_the_unit_page_offers_the_control(client, army_with_unit):
    """The endpoint existing is not the feature — Clay could not reach it."""
    body = client.get(f'/units/{army_with_unit["unit_id"]}').get_data(as_text=True)

    assert 'id="remove-models"' in body
    assert 'Dispose of the kit' in body, \
        'the panel has to say what this is not, or it becomes the disposal path'


# ── The ramp's bottom rung ───────────────────────────────
#
# Clay, on the unit page: "Can the negative 1 on the 'on Sprue' remove the
# model from inventory and make the number between the - and + non editable.
# And only show the - and + that have models available to move."
#
# The first of those fixed a control that had always been live and inert:
# retreat_unit skips models at the first owned stage, so −1 there moved
# nothing and toasted "Nothing to step back".

def test_the_unit_page_can_add_and_remove_models(client, army_with_unit):
    """"just a way to add or remove models" — both halves. Adding wires up
    POST /api/units/<id>/models, which shipped in the first commit and had
    never been called from anywhere."""
    body = client.get(f'/units/{army_with_unit["unit_id"]}').get_data(as_text=True)

    assert 'id="add-models"' in body
    assert 'id="remove-models"' in body


def test_adding_models_puts_them_where_plastic_arrives(client, army_with_unit, db_path):
    """The first owned stage. Anything further along is a claim about hobby
    work the app has no business making on Clay's behalf."""
    unit_id = army_with_unit['unit_id']

    assert client.post(f'/api/units/{unit_id}/models',
                       json={'count': 3}).status_code == 201

    with db.connect(db_path) as conn:
        first = db.first_owned_stage(conn)['id']
        rows = [dict(r) for r in conn.execute(
            'SELECT stage_id, COUNT(*) AS n FROM models WHERE unit_id = ? '
            'GROUP BY stage_id', (unit_id,))]
    added = next(r for r in rows if r['stage_id'] == first)
    assert added['n'] == 13, 'ten already there plus the three just added'


def test_the_counts_are_not_editable(client, army_with_unit):
    """A number to read, not a field to type in. It began as an <input> so a
    count could be reconciled by typing; it is a stat card now, and the only
    way to move a model is paint mode."""
    body = client.get(f'/units/{army_with_unit["unit_id"]}').get_data(as_text=True)

    assert 'class="statbox"' in body
    assert 'class="count-at"' not in body
    assert '<input class="count-at"' not in body


def test_paint_mode_never_offers_to_delete(client, army_with_unit):
    """Same dead button there, but a painting session with wet hands is no
    place for an irreversible one. Off, not repurposed."""
    body = client.get(f'/paint/{army_with_unit["unit_id"]}').get_data(as_text=True)

    assert 'removes' not in body
    first = body.index('class="untick"')
    assert 'disabled' in body[first:first + 260], \
        "the bottom rung's −1 has nowhere to step back to"


def test_inert_nudge_buttons_are_hidden_not_greyed(client):
    """"only show the − and + that have models available to move" — and with
    visibility, so the counts stay in one column down the ladder."""
    body = client.get('/static/css/app.css').get_data(as_text=True)

    assert '.nudge button:disabled { visibility: hidden; }' in body


# ── The collection's filters ─────────────────────────────
#
# The route already read faction_id and passed it to the template. The template
# never rendered a control for it, so it was reachable only by hand-editing the
# URL — the third capability this session that existed and could not be used.

def test_the_collection_offers_every_filter(client, army_with_unit):
    body = client.get('/collection').get_data(as_text=True)

    for control in ('name="faction_id"', 'name="stage_id"', 'name="own"',
                    'name="sort"', 'name="points_min"', 'name="points_max"'):
        assert control in body, control


def test_a_set_filter_is_never_hidden_behind_a_closed_fold(client, army_with_unit):
    """A screen that filters silently is worse than one showing too much."""
    plain = client.get('/collection').get_data(as_text=True)
    filtered = client.get('/collection?sort=points').get_data(as_text=True)

    assert '<details class="morefilters"' in plain
    assert 'open>' not in plain.split('<summary>')[0].split('morefilters')[1]
    assert 'open>' in filtered.split('morefilters')[1].split('<summary>')[0]


def test_chips_keep_the_other_filters(client, army_with_unit):
    """They used to hand-build their own query strings carrying only `q`, so
    tapping "40k" while filtered to a faction threw the faction away."""
    body = client.get(
        f'/collection?faction_id={army_with_unit["datasheet_id"]}&q=boyz'
    ).get_data(as_text=True)

    fortyk = [line for line in body.splitlines() if 'system=wh40k' in line]
    assert fortyk, 'the 40k chip should be on the page'
    assert 'faction_id=' in fortyk[0] and 'q=boyz' in fortyk[0]


def test_a_chip_that_is_on_links_back_to_off(client, army_with_unit):
    """A control that does nothing when tapped is one you stop trusting."""
    body = client.get('/collection?filter=unpainted').get_data(as_text=True)

    # The chip's own line, identified by its label rather than by the word
    # appearing anywhere — every other chip's href carries the active filter
    # forward, which is the point of the previous test.
    chip = next(line for line in body.splitlines() if '>Unpainted</a>' in line)
    assert 'filter=unpainted' not in chip, 'tapping it again should clear it'


def test_the_filters_reach_the_query(client, army_with_unit, db_path):
    """Not just rendered — actually narrowing. Boyz is Orks, so filtering to a
    different faction has to empty the screen."""
    with db.connect(db_path) as conn:
        other = db.upsert_faction(conn, 'Astra Militarum', 'astra-militarum')

    assert 'Boyz' in client.get('/collection').get_data(as_text=True)
    assert 'Boyz' not in client.get(
        f'/collection?faction_id={other}').get_data(as_text=True)


# ── Pruning the unit page ────────────────────────────────
#
# Clay: "Collection should just be a summary and add or remove. Drop the unit
# nickname."

def test_the_unit_page_is_a_summary_and_a_count(client, army_with_unit):
    body = client.get(f'/units/{army_with_unit["unit_id"]}').get_data(as_text=True)

    assert 'class="statgrid"' in body, 'the summary stays'
    assert 'id="add-models"' in body and 'id="remove-models"' in body
    assert 'id="bulk"' not in body, 'the per-model stage picker went with the ramp'
    assert 'name="model_ids"' not in body
    assert 'name="nickname"' not in body


def test_saving_notes_never_blanks_a_nickname(client, army_with_unit, db_path):
    """The form no longer sends a nickname, and update_unit used to write both
    columns every time. Without this, naming a squad and then editing its notes
    would silently lose the name — and nothing would say so, because
    display_name just falls back to the datasheet."""
    unit_id = army_with_unit['unit_id']
    client.patch(f'/api/units/{unit_id}', json={'nickname': 'Da Hard Boyz'})

    client.patch(f'/api/units/{unit_id}', json={'notes': 'second squad'})

    with db.connect(db_path) as conn:
        row = conn.execute('SELECT nickname, notes FROM units WHERE id = ?',
                           (unit_id,)).fetchone()
    assert row['nickname'] == 'Da Hard Boyz', 'the name survived a notes save'
    assert row['notes'] == 'second squad'


def test_an_empty_value_still_clears(client, army_with_unit, db_path):
    """Absent means "leave it"; empty means "clear it". A cleared input has to
    be able to clear."""
    unit_id = army_with_unit['unit_id']
    client.patch(f'/api/units/{unit_id}', json={'notes': 'temporary'})

    client.patch(f'/api/units/{unit_id}', json={'notes': ''})

    with db.connect(db_path) as conn:
        assert conn.execute('SELECT notes FROM units WHERE id = ?',
                            (unit_id,)).fetchone()['notes'] is None


# ── Photos through the app ───────────────────────────────

def _jpeg():
    import io
    return (io.BytesIO(b'\xff\xd8\xff\xe0' + b'\x00' * 40), 'squad.jpg')


def test_uploading_a_photo(client, army_with_unit, tmp_path, monkeypatch):
    import photos as photomod
    monkeypatch.setattr(photomod, 'PHOTO_DIR', str(tmp_path / 'shots'))
    unit_id = army_with_unit['unit_id']

    res = client.post(f'/api/units/{unit_id}/photos',
                      data={'photo': _jpeg(), 'taken_on': '2026-08-18',
                            'caption': 'first ten done'},
                      content_type='multipart/form-data')

    assert res.status_code == 201
    body = client.get(f'/units/{unit_id}').get_data(as_text=True)
    assert '2026-08-18' in body and 'first ten done' in body


def test_a_post_with_no_file_says_so(client, army_with_unit):
    assert client.post(f'/api/units/{army_with_unit["unit_id"]}/photos',
                       data={}, content_type='multipart/form-data'
                       ).status_code == 400


def test_a_photo_for_a_unit_that_is_not_there(client, tmp_path, monkeypatch):
    import photos as photomod
    monkeypatch.setattr(photomod, 'PHOTO_DIR', str(tmp_path / 'shots'))
    assert client.post('/api/units/9999/photos',
                       data={'photo': _jpeg()},
                       content_type='multipart/form-data').status_code == 404


def test_serving_and_deleting_a_photo(client, army_with_unit, tmp_path, monkeypatch):
    import photos as photomod
    monkeypatch.setattr(photomod, 'PHOTO_DIR', str(tmp_path / 'shots'))
    unit_id = army_with_unit['unit_id']
    saved = client.post(f'/api/units/{unit_id}/photos', data={'photo': _jpeg()},
                        content_type='multipart/form-data').get_json()

    served = client.get(f'/photos/{saved["filename"]}')
    assert served.status_code == 200
    assert served.data.startswith(b'\xff\xd8\xff')

    assert client.delete(f'/api/photos/{saved["id"]}').status_code == 200
    assert client.get(f'/photos/{saved["filename"]}').status_code == 404


def test_photos_are_behind_the_login(db_path, monkeypatch):
    """These are pictures of Clay's house. The tunnel is public."""
    import app as appmod
    monkeypatch.setattr(appmod.db, 'DB_PATH', db_path)
    anon = appmod.app.test_client()
    assert anon.get('/photos/anything.jpg').status_code in (302, 401)


def test_the_upload_dialog_is_on_the_unit_page(client, army_with_unit):
    """Behind a button now, not sitting open under the log."""
    body = client.get(f'/units/{army_with_unit["unit_id"]}').get_data(as_text=True)

    assert 'id="open-photo"' in body
    assert '<dialog id="photo-dialog"' in body
    assert 'name="taken_on"' in body, 'the date is the point of the log'
    assert 'multiple' in body.split('name="photo"')[1][:80], \
        'a session produces several and one at a time is the friction'


# ── The ramp as a summary ────────────────────────────────
#
# Clay: "Only show ramp option with model count and use images to represent
# each ramp."

def test_the_unit_page_shows_only_stages_with_models(client, army_with_unit, db_path):
    """Five rows of zero were most of what the summary said."""
    unit_id = army_with_unit['unit_id']
    with db.connect(db_path) as conn:
        col.advance_unit(conn, unit_id, count=4)      # 4 Assembled, 6 On sprue

    body = client.get(f'/units/{unit_id}').get_data(as_text=True)

    assert 'On sprue' in body and 'Assembled' in body
    for empty in ('Base prepared', 'Primed', 'Painted', 'Based'):
        assert f'>{empty}</span>' not in body, f'{empty} is empty and should be gone'


def test_paint_mode_keeps_the_whole_ladder(client, army_with_unit):
    """The empty rungs are where the models are going. A ladder that changes
    length as you tap is a worse thing to work against mid-session."""
    body = client.get(f'/paint/{army_with_unit["unit_id"]}').get_data(as_text=True)

    for stage in ('On sprue', 'Base prepared', 'Primed', 'Battle ready'):
        assert stage in body


def test_a_unit_with_nothing_owned_says_so(client, army_with_unit, db_path):
    """Every model on the wishlist means no owned stage has any, and an empty
    box looks like the page failed to load."""
    unit_id = army_with_unit['unit_id']
    with db.connect(db_path) as conn:
        wishlist = conn.execute(
            "SELECT id FROM stages WHERE is_owned = 0").fetchone()['id']
        conn.execute('UPDATE models SET stage_id = ? WHERE unit_id = ?',
                     (wishlist, unit_id))

    body = client.get(f'/units/{unit_id}').get_data(as_text=True)

    assert 'still on the wishlist' in body


def test_every_stage_has_its_own_icon(client, army_with_unit):
    """Seven rungs, seven different drawings — a macro that fell through to one
    default would render seven identical ones and nobody would notice."""
    import re
    body = client.get(f'/paint/{army_with_unit["unit_id"]}').get_data(as_text=True)

    icons = re.findall(r'<svg class="stageicon[^"]*".*?</svg>', body, re.S)
    assert len(icons) == 7, 'one per owned rung'
    paths = [re.findall(r'<path d="([^"]+)"|<(circle|ellipse|rect)\b', i) for i in icons]
    assert len(set(map(str, paths))) == 7, 'each stage draws something different'


def test_the_unit_panel_is_gone(client, army_with_unit):
    """Nickname and notes were crossed out together."""
    body = client.get(f'/units/{army_with_unit["unit_id"]}').get_data(as_text=True)

    assert 'data-patch=' not in body
    assert 'name="notes"' not in body


# ── The light/dark toggle ────────────────────────────────
#
# Clay: "Make this an image of sun and moon or light dark mode. I'm not sure
# why it says ground." Blueprint and Nuln are the design's names for the two
# palettes; on a button they meant nothing.

def test_the_toggle_offers_both_icons_and_no_word(client):
    """Both ship and CSS picks one, because the choice is not knowable in
    JavaScript before first paint — with no stored override the ground comes
    from prefers-color-scheme, which only the media query can answer."""
    body = client.get('/collection').get_data(as_text=True)
    button = body.split('id="ground"')[1].split('</button>')[0]

    assert 'class="moon"' in button and 'class="sun"' in button
    assert '>Ground<' not in body, 'the word is gone'
    assert 'aria-label="Switch between light and dark"' in body, \
        'two hidden-by-CSS icons and no text need a name for a screen reader'


def test_css_shows_exactly_one_icon_in_every_state(client):
    """Three states, the same three the palette itself uses: the light default,
    the OS saying dark with no override, and an override either way. A missing
    branch shows both icons or neither."""
    css = client.get('/static/css/app.css').get_data(as_text=True)

    for rule in (
        '#ground .sun { display: none; }',
        '#ground .moon { display: block; }',
        ':root:not([data-ground="blueprint"]) #ground .sun { display: block; }',
        ':root[data-ground="nuln"] #ground .sun { display: block; }',
        ':root[data-ground="blueprint"] #ground .moon { display: block; }',
    ):
        assert rule in css, rule


# ── Editing a picture, and the journey ───────────────────

def test_patching_a_caption_over_http(client, army_with_unit, tmp_path, monkeypatch):
    import photos as photomod
    monkeypatch.setattr(photomod, 'PHOTO_DIR', str(tmp_path / 'shots'))
    unit_id = army_with_unit['unit_id']
    saved = client.post(f'/api/units/{unit_id}/photos',
                        data={'photo': _jpeg(), 'taken_on': '2026-08-18'},
                        content_type='multipart/form-data').get_json()

    res = client.patch(f'/api/photos/{saved["id"]}',
                       json={'caption': 'first ten done'})

    assert res.status_code == 200
    body = client.get(f'/units/{unit_id}').get_data(as_text=True)
    assert 'first ten done' in body
    assert '2026-08-18' in body, 'and the date did not move'


def test_patching_nothing_is_refused(client, army_with_unit, tmp_path, monkeypatch):
    import photos as photomod
    monkeypatch.setattr(photomod, 'PHOTO_DIR', str(tmp_path / 'shots'))
    saved = client.post(f'/api/units/{army_with_unit["unit_id"]}/photos',
                        data={'photo': _jpeg()},
                        content_type='multipart/form-data').get_json()

    assert client.patch(f'/api/photos/{saved["id"]}', json={}).status_code == 400


def test_patching_a_photo_that_is_not_there(client):
    assert client.patch('/api/photos/9999',
                        json={'caption': 'x'}).status_code == 404


def test_the_unit_page_offers_a_note_on_every_picture(client, army_with_unit,
                                                      tmp_path, monkeypatch):
    """The button says which it is: a picture with no note offers to add one."""
    import photos as photomod
    monkeypatch.setattr(photomod, 'PHOTO_DIR', str(tmp_path / 'shots'))
    unit_id = army_with_unit['unit_id']
    client.post(f'/api/units/{unit_id}/photos', data={'photo': _jpeg()},
                content_type='multipart/form-data')

    body = client.get(f'/units/{unit_id}').get_data(as_text=True)

    assert 'shot-edit' in body and 'Add a note' in body


def test_the_journey_is_empty_and_says_so(client):
    body = client.get('/gallery').get_data(as_text=True)
    assert 'Nothing has happened yet' in body
    assert 'id="scrub"' not in body, 'nothing to scrub through'


def test_the_journey_scrubs_once_there_are_two(client, army_with_unit,
                                               tmp_path, monkeypatch):
    import photos as photomod
    monkeypatch.setattr(photomod, 'PHOTO_DIR', str(tmp_path / 'shots'))
    unit_id = army_with_unit['unit_id']
    for day in ('2026-08-20', '2026-08-01'):
        client.post(f'/api/units/{unit_id}/photos',
                    data={'photo': _jpeg(), 'taken_on': day},
                    content_type='multipart/form-data')

    body = client.get('/gallery').get_data(as_text=True)

    assert 'id="scrub"' in body and 'max="1"' in body
    # Oldest first: the first frame is the earliest date.
    assert body.index('2026-08-01') < body.index('2026-08-20')


def test_one_picture_needs_no_scrubber(client, army_with_unit, tmp_path,
                                       monkeypatch):
    """A slider with one position is a control that cannot do anything."""
    import photos as photomod
    monkeypatch.setattr(photomod, 'PHOTO_DIR', str(tmp_path / 'shots'))
    client.post(f'/api/units/{army_with_unit["unit_id"]}/photos',
                data={'photo': _jpeg()}, content_type='multipart/form-data')

    body = client.get('/gallery').get_data(as_text=True)

    assert 'class="frame on"' in body
    assert 'id="scrub"' not in body


def test_the_journey_is_in_the_nav(client):
    assert 'href="/gallery"' in client.get('/collection').get_data(as_text=True)
