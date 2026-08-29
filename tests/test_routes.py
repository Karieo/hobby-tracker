"""Route wiring, auth, and the API contracts the UI depends on."""

import json

import pytest

import collection as col
import database as db
import games as games_mod
import lists as lists_mod


#: What the fixtures below log in with. Deliberately not `OWNER_PASSWORD` from
#: conftest — see `_ensure_owner` for why the difference matters.
LOGIN, PASSWORD = 'Clay', 'pw'


def _ensure_owner(db_path):
    """Put exactly one known user in the database, whatever was there before.

    This used to be `if not (SELECT 1 FROM users): insert`, and that guard made
    the suite order-dependent in a way that hid itself well.

    `app.py` calls `seed_owner()` at import time, and these fixtures import
    `app` *inside* themselves. So whichever test imports `app` first — and only
    that one — gets an owner seeded into its own temp database, hashed from
    conftest's `OWNER_PASSWORD` rather than the `PASSWORD` above. The guard
    then found that row, skipped inserting its own, and the login failed.

    Run the file in order and everything passes: `app` is imported and cached
    by an early test, every later test gets an empty `users` table, and the
    guard does the right thing. Run one test alone and it is the first
    importer, so it seeds itself and then cannot log in.

    Deleting first rather than guarding makes the state identical either way.
    There are no rows referencing `users` at fixture time — the database is
    freshly migrated — so the delete has nothing to cascade into.
    """
    import bcrypt
    with db.connect(db_path) as conn:
        conn.execute('DELETE FROM users')
        conn.execute(
            'INSERT INTO users (id, name, password_hash, role, created_at) '
            'VALUES (?, ?, ?, ?, ?)',
            ('u1', LOGIN,
             bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode(),
             'owner', db.now()))


@pytest.fixture
def client(db_path, monkeypatch):
    """A logged-in test client pointed at an isolated database."""
    import app as appmod
    monkeypatch.setattr(appmod.db, 'DB_PATH', db_path)
    appmod.app.config['TESTING'] = True
    appmod._AUTH_FAILURES.clear()
    _ensure_owner(db_path)
    c = appmod.app.test_client()
    res = c.post('/api/auth/login', json={'login': LOGIN, 'password': PASSWORD})
    # Never hand back a client that is not logged in. The response used to be
    # discarded, which is how a broken login stayed quiet: every page returns
    # the /login redirect, and the ~130 tests on this fixture keep passing any
    # `assert something not in body` against a redirect that contains nothing.
    # A failure here should read as "the fixture broke", not as one puzzling
    # assertion somewhere else.
    assert res.status_code == 200, (
        f'fixture login failed ({res.status_code}): '
        f'{res.get_data(as_text=True)[:200]}')
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
    return {'army_id': army_id, 'unit_id': unit_id,
            'datasheet_id': datasheet_id, 'faction_id': faction_id}


# ── Auth ─────────────────────────────────────────────────

@pytest.mark.parametrize('path', ['/', '/collection', '/paint', '/backlog',
                                  '/shopping', '/sale', '/reference'])
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

@pytest.mark.parametrize('path', ['/', '/collection', '/paint', '/backlog',
                                  '/shopping', '/sale', '/reference'])
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

def test_datasheet_search_needs_two_characters(client, army_with_unit):
    assert client.get('/api/datasheets?q=B').json['results'] == []
    assert client.get('/api/datasheets?q=Boy').json['results']


# ── Kit templates ────────────────────────────────────────
#
# Named for the scanner once, because the scanner was the only
# thing that read them. `/templates` outlived it: it is how Clay
# says what is in a box once so buying another copy is one action.

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


# ── Home, from Tracker Wireframes §3a ────────────────────

def test_home_leads_with_the_effort_weighted_percentage(client, army_with_unit):
    body = client.get('/').get_data(as_text=True)
    assert 'battle ready' in body
    assert 'headline' in body


def test_home_offers_something_to_pick_back_up(client, army_with_unit):
    """A tracker that only keeps score is one you stop opening."""
    body = client.get('/').get_data(as_text=True)
    assert 'Pick up where you left off' in body


def test_home_says_what_you_got_done_lately(client, army_with_unit):
    """Spec §5.1, read back out of `stage_events`."""
    with db.connect(db.DB_PATH) as conn:
        for _ in range(6):
            col.advance_unit(conn, army_with_unit['unit_id'])

    body = client.get('/').get_data(as_text=True)

    assert 'Last 30 days' in body
    assert '10 models finished' in ' '.join(body.split())
    assert 'effort spent' in body


def test_a_month_that_finished_nothing_leads_with_the_work(client, army_with_unit):
    """Priming sixty Boyz is an evening every night and finishes not one
    model. Answering that with "0 finished" is the abandonment failure this
    app is designed against, so the headline becomes what did move."""
    with db.connect(db.DB_PATH) as conn:
        col.advance_unit(conn, army_with_unit['unit_id'])

    body = ' '.join(client.get('/').get_data(as_text=True).split())

    assert '10 models moved forward' in body
    assert 'models finished' not in body


def test_home_stays_quiet_about_a_month_with_no_work_in_it(client, army_with_unit):
    """A line reading "0 finished" every day is furniture. The unit exists and
    its models arrived — arrivals are not work, so there is nothing to say."""
    body = client.get('/').get_data(as_text=True)

    assert 'Last 30 days' not in body


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
    _ensure_owner(db_path)
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


# ── Selling, trading, wanting more ───────────────────────────────────────────

def test_the_unit_page_offers_all_three_piles(client, army_with_unit):
    """The endpoint existing is not the feature — several of the last things
    Clay asked for turned out to be built already and unreachable."""
    body = client.get(f'/units/{army_with_unit["unit_id"]}').get_data(as_text=True)

    for pile in ('owned', 'wishlist', 'sell'):
        assert f'data-pile="{pile}"' in body, pile
    assert 'data-delta="1"' in body and 'data-delta="-1"' in body


def test_no_money_is_asked_for_anywhere(client, army_with_unit):
    """Clay: "I don't care about sell price or purchase price." A field the app
    does not need is a decision it should not ask for."""
    body = client.get(f'/units/{army_with_unit["unit_id"]}').get_data(as_text=True)

    assert 'name="price"' not in body
    import app as appmod
    assert appmod.CURRENCY_SYMBOL not in body


def test_a_pile_moves_one_at_a_time(client, army_with_unit, db_path):
    unit = army_with_unit['unit_id']

    got = client.post(f'/api/units/{unit}/pile/sell', json={'delta': 1})

    assert got.status_code == 200
    # Owned is untouched: a model listed to part with is still on the shelf.
    assert got.get_json() == {'moved': 1, 'owned': 10, 'wishlist': 0, 'sell': 1}


def test_every_pile_has_an_undo(client, army_with_unit):
    """Nothing here is confirmed, because every button has its opposite beside
    it — which is a better undo than a dialog."""
    unit = army_with_unit['unit_id']

    for pile in ('owned', 'wishlist', 'sell'):
        client.post(f'/api/units/{unit}/pile/{pile}', json={'delta': 1})
        back = client.post(f'/api/units/{unit}/pile/{pile}', json={'delta': -1})
        assert back.status_code == 200, pile
    counts = back.get_json()
    assert counts['owned'] == 10 and counts['wishlist'] == 0
    assert counts['sell'] == 0


def test_an_unknown_pile_is_a_404(client, army_with_unit):
    assert client.post(f'/api/units/{army_with_unit["unit_id"]}/pile/attic',
                       json={'delta': 1}).status_code == 404


def test_a_pile_needs_a_direction(client, army_with_unit):
    assert client.post(f'/api/units/{army_with_unit["unit_id"]}/pile/sell',
                       json={'delta': 0}).status_code == 400


def test_a_unit_that_is_not_there_is_a_404(client):
    assert client.post('/api/units/9999/pile/sell',
                       json={'delta': 1}).status_code == 404


def _csv_names(response):
    """The name column of a collection CSV, in order."""
    import csv as csv_mod
    body = response.get_data(as_text=True).splitlines()
    return [r['name'] for r in csv_mod.DictReader(body)]


def test_the_collection_downloads_as_csv(client, army_with_unit):
    got = client.get('/collection.csv')

    assert got.status_code == 200
    assert got.mimetype == 'text/csv'
    header = got.get_data(as_text=True).splitlines()[0]
    assert header.startswith('name,faction,game_system,owned,built,battle_ready')


def test_the_download_is_what_the_screen_is_showing(client, army_with_unit,
                                                    db_path):
    """The whole reason this is its own route. Both go through one
    `_collection_filters` and one `_collection_rows`, so a filter cannot be
    honoured by the page and quietly dropped by the file."""
    import collection as col_mod
    with db.connect(db_path) as conn:
        other = db.upsert_faction(conn, 'Necrons', 'necrons')
        conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'created_at, updated_at) VALUES (?,?,?,1,?,?)',
            ('warriors', 'Necron Warriors', other, db.now(), db.now()))
        sheet = conn.execute(
            "SELECT id FROM datasheets WHERE bsdata_id = 'warriors'"
        ).fetchone()['id']
        col_mod.create_unit(conn, sheet, 10)

    for args in ('', '?faction_id=%d' % other, '?filter=unpainted',
                 '?q=Boyz&own=mine', '?sort=owned'):
        page = client.get('/collection' + args).get_data(as_text=True)
        names = _csv_names(client.get('/collection.csv' + args))
        for name in names:
            assert name in page, f'{name} is in the file but not the page ({args})'


def test_the_download_honours_the_faction_filter(client, army_with_unit,
                                                  db_path):
    with db.connect(db_path) as conn:
        other = db.upsert_faction(conn, 'Necrons', 'necrons')
        conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'created_at, updated_at) VALUES (?,?,?,1,?,?)',
            ('warriors', 'Necron Warriors', other, db.now(), db.now()))

    names = _csv_names(client.get('/collection.csv?faction_id=%d'
                                  % army_with_unit['faction_id']))

    assert names == ['Boyz']


def test_the_download_honours_a_chip(client, army_with_unit, db_path):
    """The chips narrow rows already loaded, so they are the filter most
    easily forgotten by a second code path. There is no second code path."""
    import collection as col_mod
    with db.connect(db_path) as conn:
        unit = army_with_unit['unit_id']
        for _ in range(9):
            col_mod.advance_unit(conn, unit)

    assert _csv_names(client.get('/collection.csv')) == ['Boyz']
    assert _csv_names(client.get('/collection.csv?filter=unpainted')) == []


def test_the_filename_says_what_is_in_it(client, army_with_unit):
    """Four downloads called collection.csv are four files called
    "collection (3).csv" and no way to tell the Orks from the Knights."""
    plain = client.get('/collection.csv')
    narrowed = client.get('/collection.csv?faction_id=%d&filter=unpainted'
                          % army_with_unit['faction_id'])

    assert 'filename="collection.csv"' in plain.headers['Content-Disposition']
    assert ('filename="collection-orks-unpainted.csv"'
            in narrowed.headers['Content-Disposition'])


def test_the_download_needs_a_session(db_path, monkeypatch):
    """It is a page, not an API route, so it redirects rather than 401s — and
    a bearer token does not open it, because TOKEN_PATHS is /api/export/."""
    import app as appmod
    monkeypatch.setattr(appmod.db, 'DB_PATH', db_path)
    anon_client = appmod.app.test_client()

    got = anon_client.get('/collection.csv')

    assert got.status_code == 302 and '/login' in got.headers['Location']


def test_effort_rides_along_with_the_raw_counts(client, army_with_unit):
    """Every progress figure is effort-weighted, and raw counts show alongside
    rather than instead — a Knight and a Termagant are both "1 model"."""
    header = client.get('/collection.csv').get_data(as_text=True).splitlines()[0]

    for column in ('owned', 'battle_ready', 'effort_total', 'effort_done',
                   'completion_pct'):
        assert column in header


# ── `faction=` narrows the export ────────────────────────────────────────────

def test_the_export_filters_by_faction_name(client, army_with_unit, db_path):
    with db.connect(db_path) as conn:
        other = db.upsert_faction(conn, 'Necrons', 'necrons')
        conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'created_at, updated_at) VALUES (?,?,?,1,?,?)',
            ('warriors', 'Necron Warriors', other, db.now(), db.now()))

    rows = client.get(
        '/api/export/inventory?faction=Orks').get_json()['datasheets']

    assert rows and all(r['faction'] == 'Orks' for r in rows)


def test_a_faction_can_be_a_slug_or_a_name_in_either_case(client, army_with_unit):
    """It gets typed into a curl. `?faction=orks` is writable from memory;
    `?faction_id=1` is a lookup first."""
    for value in ('Orks', 'orks', 'ORKS'):
        rows = client.get('/api/export/inventory?faction='
                          + value).get_json()['datasheets']
        assert rows, value


def test_a_faction_that_does_not_exist_is_a_404(client, army_with_unit):
    """Not an empty list. A cheerful zero rows is how you conclude you own no
    Orks when you actually mistyped the name."""
    got = client.get('/api/export/inventory?faction=Tyrandis')

    assert got.status_code == 404
    assert 'Tyrandis' in got.get_json()['error']


def test_faction_and_fields_compose(client, army_with_unit):
    rows = client.get('/api/export/inventory'
                      '?faction=orks&fields=name,owned').get_json()['datasheets']

    assert rows and set(rows[0]) == {'name', 'owned'}


# ── `fields=` narrows the export ─────────────────────────────────────────────
#
# The full row is built for a list optimiser — join keys, every points tier,
# the whole stage breakdown. The question actually asked at a phone on the sofa
# is "what do I have and how much of it is finished", which was a curl piped
# through python until this existed.

def test_fields_returns_only_what_was_asked_for(client, army_with_unit):
    got = client.get('/api/export/inventory'
                     '?fields=name,owned,battle_ready').get_json()

    assert got['datasheets']
    for row in got['datasheets']:
        assert set(row) == {'name', 'owned', 'battle_ready'}


def test_fields_leaves_the_envelope_alone(client, army_with_unit):
    """It narrows the rows; it does not turn the response into a different
    shape. A consumer can add it to a URL without rewriting how it reads the
    reply."""
    got = client.get('/api/export/inventory?fields=name').get_json()

    assert set(got) == {'army', 'stages', 'generated_at', 'datasheets'}


def test_an_unknown_field_is_refused_rather_than_dropped(client, army_with_unit):
    """A typo that silently returns fewer columns is a spreadsheet with a
    column missing and nothing saying why — the same failure as a silently
    dropped import line."""
    got = client.get('/api/export/inventory?fields=name,batle_ready')

    assert got.status_code == 400
    assert 'batle_ready' in got.get_json()['error']


def test_the_refusal_lists_what_it_could_have_been(client, army_with_unit):
    """The point of the 400 is to end the guessing, not to start it."""
    error = client.get(
        '/api/export/inventory?fields=nonsense').get_json()['error']

    for name in ('name', 'owned', 'battle_ready', 'by_stage'):
        assert name in error


def test_csv_columns_come_out_in_the_order_asked_for(client, army_with_unit):
    got = client.get('/api/export/inventory'
                     '?format=csv&fields=battle_ready,name,owned')

    assert got.get_data(as_text=True).splitlines()[0] == \
        'battle_ready,name,owned'


def test_a_nested_field_cannot_be_a_csv_column(client, army_with_unit):
    """csv has always dropped by_stage and points because a nested structure in
    a cell is parsed slightly differently by everything. Asking for one as a
    column says so rather than writing a repr nobody can read."""
    got = client.get('/api/export/inventory?format=csv&fields=name,by_stage')

    assert got.status_code == 400
    assert 'by_stage' in got.get_json()['error']


def test_a_nested_field_is_fine_in_json(client, army_with_unit):
    rows = client.get(
        '/api/export/inventory?fields=name,by_stage').get_json()['datasheets']

    assert rows and isinstance(rows[0]['by_stage'], dict)


def test_a_field_that_was_switched_off_says_so(client, army_with_unit):
    """`include_capability=0` means buildable_from_spare was never computed.
    "No such field" would send Clay looking for a typo that is not there."""
    got = client.get('/api/export/inventory'
                     '?include_capability=0&fields=name,buildable_from_spare')

    assert got.status_code == 400
    assert 'include_capability' in got.get_json()['error']


def test_fields_tolerates_spacing_and_repeats(client, army_with_unit):
    """Typed by hand on a phone, so `name, owned` and a duplicate are not
    worth a 400."""
    got = client.get('/api/export/inventory?format=csv&fields=name,%20owned%20,name')

    assert got.status_code == 200
    assert got.get_data(as_text=True).splitlines()[0] == 'name,owned'


def test_an_empty_fields_is_refused(client, army_with_unit):
    """`fields=` is a caller that meant to send something. Treating it as "all
    of them" would answer a question that was not asked."""
    assert client.get('/api/export/inventory?fields=').status_code == 400
    assert client.get('/api/export/inventory?fields=,,').status_code == 400


def test_no_fields_is_still_the_whole_row(client, army_with_unit):
    """The parameter is additive: leaving it off changes nothing."""
    rows = client.get('/api/export/inventory').get_json()['datasheets']

    assert set(rows[0]) == set(col.EXPORT_FIELDS)


def test_every_declared_field_can_actually_be_asked_for(client, army_with_unit):
    """EXPORT_FIELDS is what the 400 offers, so a name in it that the row does
    not carry would be advertising a column that comes back empty."""
    got = client.get('/api/export/inventory?fields='
                     + ','.join(col.EXPORT_FIELDS)).get_json()

    assert set(got['datasheets'][0]) == set(col.EXPORT_FIELDS)


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
    assert 'id="built-as"' in body, 'the picker itself, not the prose round it'
    assert 'Built as' in body
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

    assert 'data-pile="owned"' in body and 'data-delta="-1"' in body
    # CLAUDE.md: every screen offering one has to say which it is, or the cheap
    # control becomes the one Clay reaches for and the spend history empties.
    # The wording has changed three times and the control twice — a paragraph,
    # then a sentence, now a row label. What it has to do has not changed.
    assert 'shortlist' in body and 'stay yours' in body, \
        'the page has to say the sell row does not remove anything'


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

    assert 'data-pile="owned"' in body
    assert 'data-delta="1"' in body and 'data-delta="-1"' in body


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


def test_no_filter_is_ever_hidden(client, army_with_unit):
    """A screen that filters silently is worse than one showing too much.

    This used to guard a disclosure that opened itself whenever a filter was
    set. Clay: "This is a mess and hard to use. Please simplify and alway
    show." There is no fold now, which is the same guarantee without the
    machinery — so the test is that every control is on the page
    unconditionally, and that nothing can collapse them again."""
    plain = client.get('/collection').get_data(as_text=True)
    filtered = client.get('/collection?sort=points&own=wanted').get_data(as_text=True)

    for body in (plain, filtered):
        assert '<details' not in body, 'nothing on this screen folds away'
        for control in ('name="faction_id"', 'name="stage_id"', 'name="sort"',
                        'name="points_min"', 'name="points_max"'):
            assert control in body, control


def test_what_to_show_is_chips_not_a_select(client, army_with_unit):
    """Three choices behind a dropdown cost a labelled row and a tap to open.
    Chips say which one is on without being opened."""
    body = client.get('/collection?own=wanted').get_data(as_text=True)

    assert 'aria-label="What to show"' in body
    assert '<select name="own"' not in body
    # The one that is on is marked, or the rail says nothing.
    rail = body.split('aria-label="What to show"')[1].split('</nav>')[0]
    assert 'chip on' in rail.replace('  ', ' ')


def test_the_filters_need_no_apply(client, army_with_unit):
    """The Apply button was a second tap for a decision already made, and on a
    phone it sat below the fold often enough that filters looked broken."""
    body = client.get('/collection').get_data(as_text=True)

    assert '>Apply<' not in body


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
    assert 'data-pile="owned"' in body, 'both halves of the count'
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

    assert 'shot-edit' in body and 'Note</button>' in body


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


# ── The faction filter, and the rows that filter to nothing ──

def test_an_empty_faction_is_not_offered_in_the_collection_filter(
        client, army_with_unit):
    """Clay: *"the filtering on the factions is still not working properly."*

    A faction nothing points at can only ever filter to an empty screen. They
    turn up on their own: placing a Kill Team under Orks moves its operatives
    to the Orks row and leaves the team's own row holding nothing.
    """
    import database as db
    with db.connect() as conn:
        db.upsert_faction(conn, 'Greenskin', 'kt-greenskin')
        conn.commit()

    page = client.get('/collection').get_data(as_text=True)

    assert 'Greenskin' not in page
    assert 'Orks' in page, 'the one with datasheets behind it still shows'


def test_an_empty_faction_is_still_offered_where_a_faction_is_assigned(
        client, army_with_unit):
    """Only the filter drops them. Starting an army for a faction with nothing
    imported against it yet is a real thing to do, and the armies page is where
    that choice is made."""
    import database as db
    with db.connect() as conn:
        db.upsert_faction(conn, 'Greenskin', 'kt-greenskin')
        conn.commit()

    page = client.get('/armies').get_data(as_text=True)

    assert 'Greenskin' in page


def test_a_selected_faction_survives_even_when_it_is_empty(
        client, army_with_unit):
    """A bookmarked URL has to keep saying what it is showing. Dropping the
    selected option would silently reset the page to "Any faction" and show
    everything, which reads as data appearing out of nowhere."""
    import database as db
    with db.connect() as conn:
        fid = db.upsert_faction(conn, 'Greenskin', 'kt-greenskin')
        conn.commit()

    page = client.get(f'/collection?faction_id={fid}').get_data(as_text=True)

    assert 'Greenskin' in page


# ── The backlog ──────────────────────────────────────────

def test_the_backlog_offers_every_way_of_sorting_it(client, army_with_unit):
    """The sorting *is* the feature — "a big push or a quick win" is a question
    about order. A chip missing here is an ordering Clay cannot reach."""
    import backlog as bl
    page = client.get('/backlog').get_data(as_text=True)

    for _key, label in bl.SORTS:
        assert label in page, label


def test_an_unknown_sort_does_not_break_the_backlog(client, army_with_unit):
    """It comes off a query string, so it is whatever was typed or bookmarked."""
    assert client.get('/backlog?sort=nonsense').status_code == 200


def test_the_backlog_is_reachable_without_a_nav_entry(client, army_with_unit):
    """The nav is already five items on a phone and Clay has complained about
    clutter. It is linked from the two places the question gets asked instead:
    the home screen, and the paint picker."""
    assert '/backlog' in client.get('/').get_data(as_text=True)
    assert '/backlog' in client.get('/paint').get_data(as_text=True)


def test_the_shopping_page_renders_a_real_plan(client, army_with_unit, db_path):
    """The empty page is the easy half. This one exercises the branches that
    can actually throw: a box line with a quantity and a price, the à la carte
    comparison, and the uncovered panel — all on one render."""
    import kit_templates as kt
    with db.connect(db_path) as conn:
        boyz = army_with_unit['datasheet_id']
        warboss = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'created_at, updated_at) VALUES (?,?,?,1,?,?)',
            ('wb', 'Warboss', army_with_unit['faction_id'],
             db.now(), db.now())).lastrowid
        stages = {s['name']: s['id'] for s in col.stage_ladder(conn)}
        col.create_unit(conn, boyz, 20, stage_id=stages['Wishlist'])
        col.create_unit(conn, warboss, 1, stage_id=stages['Wishlist'])
        kt.create_template(conn, 'Boyz',
                           [{'datasheet_id': boyz, 'model_count': 10}],
                           rrp_cents=3750)

    res = client.get('/shopping')
    body = res.get_data(as_text=True)

    assert res.status_code == 200
    assert '2 × ' in body, 'twenty against a box of ten is two boxes'
    assert 'Warboss' in body, 'the uncovered want must never be dropped'


def test_the_shopping_page_shows_no_money_anywhere(client, army_with_unit,
                                                   db_path):
    """Clay: "Spend and kits are obsolete." The screen answers which boxes and
    how much spare, and says nothing about cost."""
    import kit_templates as kt
    with db.connect(db_path) as conn:
        boyz = army_with_unit['datasheet_id']
        stages = {s['name']: s['id'] for s in col.stage_ladder(conn)}
        col.create_unit(conn, boyz, 10, stage_id=stages['Wishlist'])
        kt.create_template(conn, 'Boyz',
                           [{'datasheet_id': boyz, 'model_count': 10}])

    body = client.get('/shopping').get_data(as_text=True)

    assert 'Boyz' in body, 'the plan still recommends the box'
    for word in ('for the lot', 'at least', 'separately', 'no price recorded'):
        assert word not in body, word

def test_the_list_page_offers_a_way_to_delete_the_list(client, db_path):
    """Clay, on an empty list he could not get rid of: "No way to delete
    list." `DELETE /api/lists/<id>` and `lists.delete_list` both already
    existed and nothing called either — the endpoint-with-no-caller pattern
    `CLAUDE.md` names. This asserts the control is on the page, because that
    is the half that was missing."""
    with db.connect(db_path) as conn:
        list_id = lists_mod.create_list(conn, 'Imperial Knights')

    body = client.get(f'/lists/{list_id}').get_data(as_text=True)

    assert 'id="delete-list"' in body
    assert f'data-list="{list_id}"' in body


def test_deleting_a_list_from_the_route_removes_it(client, db_path):
    with db.connect(db_path) as conn:
        list_id = lists_mod.create_list(conn, 'Imperial Knights')

    assert client.delete(f'/api/lists/{list_id}').status_code == 200
    assert client.get(f'/lists/{list_id}').status_code == 404
    with db.connect(db_path) as conn:
        assert lists_mod.list_lists(conn) == []


def test_deleting_a_list_leaves_the_wishlist_it_raised(client, db_path,
                                                       army_with_unit):
    """The reason this button is safe to offer at all. Models a list put on
    the wishlist are still wanted after it is gone."""
    with db.connect(db_path) as conn:
        list_id = lists_mod.create_list(conn, 'Saturday')
        lists_mod.add_entry(conn, list_id, army_with_unit['datasheet_id'], 30)
        lists_mod.raise_wishlist(conn, list_id)
        before = lists_mod.wishlist(conn)[0]['wanted']

    client.delete(f'/api/lists/{list_id}')

    with db.connect(db_path) as conn:
        assert lists_mod.wishlist(conn)[0]['wanted'] == before


def test_the_sale_page_renders_both_sections_and_the_caveat(client, db_path,
                                                            army_with_unit):
    """The empty page is the easy half. This exercises every branch that can
    throw on one render: a sealed box free to sell, one held back, a surplus
    row, and the unresolved-list warning above them."""
    import kit_templates as kt
    with db.connect(db_path) as conn:
        boyz = army_with_unit['datasheet_id']
        gork = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            "created_at, updated_at) VALUES ('g','Gorkanaut',?,8,?,?)",
            (army_with_unit['faction_id'], db.now(), db.now())).lastrowid
        # A list needs 5 of the 10 Boyz owned, leaving 5 spare.
        list_id = lists_mod.create_list(conn, 'Saturday')
        lists_mod.add_entry(conn, list_id, boyz, 5)
        # ...and one row that never resolved, which makes it all optimistic.
        conn.execute('INSERT INTO list_entries (list_id, position, raw_name, '
                     'model_count) VALUES (?, 2, ?, 3)', (list_id, 'Sum Fing'))
        for name, sheet in (('Sealed Gork', gork), ('Sealed Boyz', boyz)):
            template = kt.create_template(
                conn, f'{name} tpl',
                [{'datasheet_id': sheet, 'model_count': 1}])
            kit = conn.execute(
                'INSERT INTO kits (name, kit_template_id, box_state, status, '
                "created_at, updated_at) VALUES (?,?,'sealed','owned',?,?)",
                (name, template, db.now(), db.now())).lastrowid
            conn.execute('INSERT INTO kit_datasheets (kit_id, datasheet_id) '
                         'VALUES (?,?)', (kit, sheet))

    res = client.get('/sale')
    body = res.get_data(as_text=True)

    assert res.status_code == 200
    assert 'never matched a datasheet' in body, 'the caveat leads'
    assert 'Sealed Gork' in body, 'nothing wants a Gorkanaut'
    assert 'Sealed, but spoken for' in body, 'the Boyz box is held back'
    assert 'More than any list needs' in body


def test_the_sale_page_says_so_when_there_is_nothing_to_spare(client, db_path,
                                                              army_with_unit):
    """Every model owned is wanted by a list, so the screen has to say "nothing
    to spare" rather than render three empty headings."""
    with db.connect(db_path) as conn:
        list_id = lists_mod.create_list(conn, 'Saturday')
        lists_mod.add_entry(conn, list_id, army_with_unit['datasheet_id'], 10)

    body = client.get('/sale').get_data(as_text=True)

    assert 'Nothing to spare' in body


def test_the_collection_offers_the_sale_screen_from_the_shortlist(client):
    """The shortlist says what is going; /sale proposes what could. The link
    only appears in that view — the nav is five items on a phone already."""
    assert 'href="/sale"' in client.get(
        '/collection?own=sell').get_data(as_text=True)
    assert 'href="/sale"' not in client.get(
        '/collection').get_data(as_text=True)


def test_the_list_forms_offer_battle_sizes_not_a_number_box(client):
    """Clay: "There are only 2 list battle sizes for list." Typing a third is a
    way to build a list that cannot be played."""
    for path in ('/lists', '/lists/import'):
        body = client.get(path).get_data(as_text=True)
        assert 'Battle size' in body, path
        assert 'Incursion (1000 pts)' in body, path
        assert 'Strike Force (2000 pts)' in body, path
        assert 'name="points_limit" type="number"' not in body, path


def test_picking_a_battle_size_sets_the_limit(client, db_path):
    res = client.post('/api/lists', json={'name': 'Saturday',
                                          'points_limit': 2000})
    assert res.status_code in (200, 201)

    with db.connect(db_path) as conn:
        row = lists_mod.list_lists(conn)[0]
    assert row['points_limit'] == 2000
    assert row['battle_size'] == 'Strike Force'


def test_the_index_shows_the_pastes_figure_when_it_cannot_price_the_list(
        client, db_path, army_with_unit):
    """"0 / 2000 pts" for a 1,985-point army is wrong in the direction that
    matters. The paste's own figure is better information than a zero —
    labelled, because it is a claim and not a calculation."""
    with db.connect(db_path) as conn:
        list_id = lists_mod.create_list(conn, 'Pasted', points_limit=2000,
                                        points_total=1985)
        lists_mod.add_entry(conn, list_id, army_with_unit['datasheet_id'], 20)

    body = client.get('/lists').get_data(as_text=True)

    assert '1985' in body
    assert 'from the paste' in body


def test_every_page_offers_a_way_to_sign_out(client):
    """`POST /api/auth/logout` shipped in the first commit and nothing ever
    called it — the endpoint-with-no-caller pattern, and the one instance of it
    that left Clay with no way to end a session at all.

    In the footer rather than the nav: that is five items on a phone already,
    and the owner's name was sitting there doing nothing.
    """
    for path in ('/', '/collection', '/lists'):
        body = client.get(path).get_data(as_text=True)
        assert 'id="sign-out"' in body, path


def test_signing_out_clears_the_session(client):
    assert client.get('/').status_code == 200

    assert client.post('/api/auth/logout').status_code == 200

    res = client.get('/')
    assert res.status_code == 302 and '/login' in res.headers['Location']


def test_the_list_page_offers_a_way_to_edit_the_list(client, db_path):
    with db.connect(db_path) as conn:
        list_id = lists_mod.create_list(conn, 'Saturday', points_limit=1000)

    body = client.get(f'/lists/{list_id}').get_data(as_text=True)

    assert 'id="edit-list"' in body
    assert 'Strike Force (2000 pts)' in body


def test_patching_a_list_changes_it(client, db_path):
    with db.connect(db_path) as conn:
        list_id = lists_mod.create_list(conn, 'Saturdya', points_limit=1000)

    res = client.patch(f'/api/lists/{list_id}',
                       json={'name': 'Saturday', 'points_limit': '2000'})

    assert res.status_code == 200
    assert res.get_json()['battle_size'] == 'Strike Force'
    with db.connect(db_path) as conn:
        assert lists_mod.get_list(conn, list_id)['name'] == 'Saturday'


def test_patching_a_list_that_is_not_there_is_a_404(client):
    assert client.patch('/api/lists/9999', json={'name': 'x'}).status_code == 404


def test_a_list_kept_an_odd_limit_it_was_made_with(client, db_path):
    """A list from before the picker existed can carry any number. Opening the
    edit form must not silently discard it on save."""
    with db.connect(db_path) as conn:
        list_id = lists_mod.create_list(conn, 'Old', points_limit=1500)

    body = client.get(f'/lists/{list_id}').get_data(as_text=True)

    assert '1500 pts (not a battle size)' in body


# ── Games, per list ──────────────────────────────────────
#
# Clay: "games played by list, win/loss and point difference 0-100."

def test_recording_a_game_takes_two_scores_and_derives_the_rest(client, gap_list):
    res = client.post(f'/api/lists/{gap_list["list_id"]}/games',
                      json={'your_score': 85, 'their_score': 72})

    assert res.status_code == 200
    with db.connect(db.DB_PATH) as conn:
        game = games_mod.games_for(conn, gap_list['list_id'])[0]
    assert (game['result'], game['margin']) == ('won', 13)


def test_a_score_outside_the_range_is_refused_by_the_route(client, gap_list):
    """400 with the reason, not a clamp. A mistyped 850 stored as 100 is a
    wrong record that reads like a real one."""
    res = client.post(f'/api/lists/{gap_list["list_id"]}/games',
                      json={'your_score': 850, 'their_score': 72})

    assert res.status_code == 400
    assert '0 and 100' in res.get_json()['error']


def test_recording_a_game_against_a_list_that_is_gone_is_a_404(client):
    res = client.post('/api/lists/9999/games',
                      json={'your_score': 85, 'their_score': 72})

    assert res.status_code == 404


def test_the_list_page_shows_the_record_and_the_games(client, gap_list):
    for yours, theirs in ((85, 72), (40, 90)):
        client.post(f'/api/lists/{gap_list["list_id"]}/games',
                    json={'your_score': yours, 'their_score': theirs})

    body = ' '.join(
        client.get(f'/lists/{gap_list["list_id"]}').get_data(as_text=True).split())

    assert '1–1' in body, 'the record, in the heading'
    assert '85–72' in body and '40–90' in body
    assert 'average margin' in body


def test_a_list_that_never_played_says_so_rather_than_showing_zeroes(client, gap_list):
    body = client.get(f'/lists/{gap_list["list_id"]}').get_data(as_text=True)

    assert 'No games yet.' in body
    assert '0–0' not in body


def test_the_index_carries_each_list_s_record(client, gap_list):
    client.post(f'/api/lists/{gap_list["list_id"]}/games',
                json={'your_score': 85, 'their_score': 72})

    assert '1–0' in client.get('/lists').get_data(as_text=True)


def test_a_game_can_be_removed(client, gap_list):
    res = client.post(f'/api/lists/{gap_list["list_id"]}/games',
                      json={'your_score': 85, 'their_score': 72})
    game_id = res.get_json()['id']

    assert client.delete(f'/api/games/{game_id}').status_code == 200
    assert client.delete(f'/api/games/{game_id}').status_code == 404


# ── /add takes an app export too ─────────────────────────
#
# Clay: "I want to be able to paste in a list and it reconcile against the
# datasheets and add."

GW_EXPORT = """Da Green Tide (2000 points)
Orks
Strike Force (2000 points)
Waaagh! Tribe

CHARACTERS

Warboss (65 points)

BATTLELINE

20x Boyz [180pts]
"""


def test_pasting_an_export_into_add_offers_only_its_units(client, army_with_unit):
    """The preamble and the section headings are not units. Offering seven of
    them as unknowns on every paste is how Clay learns to ignore the unresolved
    rows, which are the one thing here he must not learn to ignore."""
    body = client.post('/add/preview', data={'text': GW_EXPORT}).get_data(as_text=True)

    assert 'Da Green Tide' not in body
    assert 'Waaagh! Tribe' not in body
    assert 'CHARACTERS' not in body
    assert 'Boyz' in body


def test_the_preview_says_it_recognised_an_export(client, army_with_unit):
    """The parser switched itself, and a screen that quietly changed how it
    read your paste is one you stop trusting.

    It says "an app export" rather than naming one. The detector's two names
    come from samples written to a documented shape, and the only real export
    this repo has detects as New Recruit while looking like neither — so naming
    an app to Clay about his own list would be a confident claim with nothing
    behind it.
    """
    body = client.post('/add/preview', data={'text': GW_EXPORT}).get_data(as_text=True)

    assert 'Read as an app export' in body
    assert 'New Recruit' not in body


def test_a_shelf_paste_says_nothing_about_a_format(client, army_with_unit):
    """There is no format to name, and "Read as unknown" would be noise."""
    body = client.post('/add/preview',
                       data={'text': '20 Boyz built'}).get_data(as_text=True)

    assert 'Read as' not in body
    assert 'Boyz' in body


def test_an_exported_unit_commits_at_the_batch_stage(client, army_with_unit):
    """An export carries no stage words, so the batch default is what every
    model from one arrives at."""
    with db.connect(db.DB_PATH) as conn:
        stages = {s['name']: s['id'] for s in col.stage_ladder(conn)}
        before = conn.execute('SELECT COUNT(*) n FROM models').fetchone()['n']

    res = client.post('/api/add/commit', json={
        'stage_id': stages['Painted'],
        'rows': [{'datasheet_id': army_with_unit['datasheet_id'], 'count': 20}]})

    assert res.status_code == 200
    with db.connect(db.DB_PATH) as conn:
        after = conn.execute(
            'SELECT COUNT(*) n FROM models m JOIN stages s ON s.id = m.stage_id '
            " WHERE s.name = 'Painted'").fetchone()['n']
        total = conn.execute('SELECT COUNT(*) n FROM models').fetchone()['n']
    assert after == 20 and total == before + 20


def test_an_export_says_all_of_it_lands_at_one_stage(client, army_with_unit):
    """"Lines without a stage word" implies some have one. An export carries
    none, and a 2000-point paste is fifty-odd models arriving somewhere Clay
    had better have chosen on purpose."""
    body = ' '.join(client.post('/add/preview', data={'text': GW_EXPORT})
                    .get_data(as_text=True).split())

    assert 'All of it lands at' in body
    assert 'Lines without a stage word' not in body


def test_a_shelf_paste_still_talks_about_lines_without_a_stage_word(client, army_with_unit):
    body = ' '.join(client.post('/add/preview',
                                data={'text': '20 Boyz built\n1 Trukk'})
                    .get_data(as_text=True).split())

    assert 'Lines without a stage word land at' in body
