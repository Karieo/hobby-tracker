"""Route wiring, auth, and the API contracts the UI depends on."""

import pytest

import collection as col
import database as db


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
