"""Armies, units, models — and the interactions the app lives or dies on."""

import pytest

import collection as col
import database as db


@pytest.fixture
def orks(conn):
    """A faction and two datasheets with different effort weights."""
    faction_id = db.upsert_faction(conn, 'Orks', 'orks')
    ids = {}
    for name, effort in (('Boyz', 1), ('Deff Dread', 8)):
        cur = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
            (name.lower(), name, faction_id, effort, db.now(), db.now()))
        ids[name] = cur.lastrowid
    return {'faction_id': faction_id, **ids}


@pytest.fixture
def stages(conn):
    return {s['name']: s for s in col.stage_ladder(conn)}


# ── Unit creation ────────────────────────────────────────

def test_creating_a_unit_generates_all_its_models(conn, orks):
    unit_id = col.create_unit(conn, orks['Boyz'], 10)
    models = col.unit_models(conn, unit_id)
    assert len(models) == 10
    assert {m['stage_name'] for m in models} == {'On sprue'}


def test_every_model_gets_an_arrival_event(conn, orks):
    """A model that never moves still needs a record of when it arrived."""
    unit_id = col.create_unit(conn, orks['Boyz'], 3)
    events = conn.execute(
        'SELECT from_stage_id, to_stage_id FROM stage_events').fetchall()
    assert len(events) == 3
    assert all(e['from_stage_id'] is None for e in events)


def test_a_unit_needs_at_least_one_model(conn, orks):
    with pytest.raises(ValueError):
        col.create_unit(conn, orks['Boyz'], 0)


# ── A second helping joins the first ─────────────────────

def test_adding_more_extends_the_unit_rather_than_making_a_second(conn, orks):
    """Clay's report: "if I add more of a model it needs to add them not make
    2 lines." Three Killa Kans in two goes read as "1 model" and "2 models"
    with nothing on either line to tell them apart."""
    first = col.add_or_extend_unit(conn, orks['Boyz'], 1)
    second = col.add_or_extend_unit(conn, orks['Boyz'], 2)
    assert second['unit_id'] == first['unit_id']
    assert second['extended'] is True
    assert len(col.unit_models(conn, first['unit_id'])) == 3
    assert conn.execute('SELECT COUNT(*) FROM units').fetchone()[0] == 1


def test_the_extension_keeps_its_own_stage(conn, orks, stages):
    """One painted and two on sprue is one squad of three, told truthfully.
    Per-model stages exist for exactly this."""
    first = col.add_or_extend_unit(conn, orks['Boyz'], 1,
                                   stage_id=stages['Painted']['id'])
    col.add_or_extend_unit(conn, orks['Boyz'], 2,
                           stage_id=stages['On sprue']['id'])
    counts = {s['name']: s['count']
              for s in col.unit_breakdown(conn, first['unit_id'])}
    assert counts['Painted'] == 1
    assert counts['On sprue'] == 2


def test_only_the_new_models_come_back(conn, orks):
    """The caller stamps provenance on these ids, so they must not include the
    models that were already there — see lists._stamp."""
    first = col.add_or_extend_unit(conn, orks['Boyz'], 4)
    second = col.add_or_extend_unit(conn, orks['Boyz'], 2)
    assert len(second['model_ids']) == 2
    assert not set(second['model_ids']) & set(first['model_ids'])


def test_a_different_kit_stays_a_different_unit(conn, orks):
    """Disposals are per kit: sell the box and its models go with it. Two
    copies of a box poured into one unit could never be unpicked again."""
    one = col.create_kit(conn, 'Combat Patrol: Orks')
    two = col.create_kit(conn, 'Combat Patrol: Orks')
    a = col.add_or_extend_unit(conn, orks['Boyz'], 10, kit_id=one)
    b = col.add_or_extend_unit(conn, orks['Boyz'], 10, kit_id=two)
    assert a['unit_id'] != b['unit_id']
    assert b['extended'] is False


def test_a_different_army_stays_a_different_unit(conn, orks):
    speed = col.create_army(conn, 'Speed Freeks')
    goffs = col.create_army(conn, 'Goffs')
    a = col.add_or_extend_unit(conn, orks['Boyz'], 5, army_id=speed)
    b = col.add_or_extend_unit(conn, orks['Boyz'], 5, army_id=goffs)
    assert a['unit_id'] != b['unit_id']


def test_a_named_squad_stays_its_own_thing(conn, orks):
    a = col.add_or_extend_unit(conn, orks['Boyz'], 5, nickname="Grukk's lot")
    b = col.add_or_extend_unit(conn, orks['Boyz'], 5)
    assert a['unit_id'] != b['unit_id']


def test_wanted_models_never_join_owned_ones(conn, orks, stages):
    """A wishlist line offers "Bought it →" and an owned line "Advance all →".
    Merging them swallows the wishlist entry, and with it the loop closing."""
    wishlist = db.wishlist_stage(conn)
    owned = col.add_or_extend_unit(conn, orks['Boyz'], 5)
    wanted = col.add_or_extend_unit(conn, orks['Boyz'], 3,
                                    stage_id=wishlist['id'])
    assert wanted['unit_id'] != owned['unit_id']
    assert wanted['extended'] is False


def test_wanted_models_do_join_other_wanted_ones(conn, orks):
    wishlist = db.wishlist_stage(conn)
    a = col.add_or_extend_unit(conn, orks['Boyz'], 3, stage_id=wishlist['id'])
    b = col.add_or_extend_unit(conn, orks['Boyz'], 2, stage_id=wishlist['id'])
    assert b['unit_id'] == a['unit_id']


def test_a_disposed_kit_does_not_collect_new_models(conn, orks):
    """The sold squad stays sold. Its unit is out of the collection, so it must
    not silently swallow the replacements Clay buys."""
    kit_id = col.create_kit(conn, 'Boyz')
    sold = col.add_or_extend_unit(conn, orks['Boyz'], 10, kit_id=kit_id)
    col.dispose_kit(conn, kit_id, 'sold')
    fresh = col.add_or_extend_unit(conn, orks['Boyz'], 10, kit_id=kit_id)
    assert fresh['unit_id'] != sold['unit_id']


# ── Advancing: the primary interaction ───────────────────

def test_advance_all_moves_the_whole_unit_one_step(conn, orks, stages):
    """"I primed the squad" — one call, no selection, no per-model thought."""
    unit_id = col.create_unit(conn, orks['Boyz'], 10)
    assert col.advance_unit(conn, unit_id) == 10
    assert {m['stage_name'] for m in col.unit_models(conn, unit_id)} == {'Assembled'}


def test_advance_n_moves_the_least_advanced_models(conn, orks, stages):
    """"Six of these ten are primed" must not ask which six."""
    unit_id = col.create_unit(conn, orks['Boyz'], 10)
    col.advance_unit(conn, unit_id, count=4)          # 4 -> Assembled
    col.advance_unit(conn, unit_id, count=4)          # the 6 on sprue are behind
    counts = {s['name']: s['count'] for s in col.unit_breakdown(conn, unit_id)}
    assert counts['On sprue'] == 2
    assert counts['Assembled'] == 8, \
        'the second call must pick up models still on sprue, not the ones ahead'


def test_advance_stops_at_the_terminal_stage(conn, orks, stages):
    unit_id = col.create_unit(conn, orks['Boyz'], 2)
    for _ in range(20):
        col.advance_unit(conn, unit_id)
    models = col.unit_models(conn, unit_id)
    assert {m['stage_name'] for m in models} == {'Battle ready'}
    assert col.advance_unit(conn, unit_id) == 0, 'nothing left to advance'


def test_advance_from_one_stage_only(conn, orks, stages):
    """The per-stage increment control on the breakdown screen."""
    unit_id = col.create_unit(conn, orks['Boyz'], 6)
    col.advance_unit(conn, unit_id, count=3)          # 3 Assembled, 3 On sprue
    moved = col.advance_unit(conn, unit_id, count=1,
                             from_stage_id=stages['Assembled']['id'])
    assert moved == 1
    counts = {s['name']: s['count'] for s in col.unit_breakdown(conn, unit_id)}
    assert counts == {**counts, 'On sprue': 3, 'Assembled': 2, 'Base prepared': 1}


def test_advancing_a_wishlist_model_makes_it_owned(conn, orks, stages):
    """Wishlist is a stage, so buying the thing is the same action as any other."""
    unit_id = col.create_unit(conn, orks['Boyz'], 5,
                              stage_id=stages['Wishlist']['id'])
    assert col.list_units(conn)[0]['owned_count'] == 0
    col.advance_unit(conn, unit_id)
    unit = col.list_units(conn)[0]
    assert unit['owned_count'] == 5
    assert {m['stage_name'] for m in col.unit_models(conn, unit_id)} == {'On sprue'}


def test_every_move_writes_history(conn, orks):
    unit_id = col.create_unit(conn, orks['Boyz'], 4)
    col.advance_unit(conn, unit_id)
    moves = conn.execute(
        'SELECT COUNT(*) c FROM stage_events WHERE from_stage_id IS NOT NULL'
    ).fetchone()['c']
    assert moves == 4


# ── Bulk selection: the escape hatch ─────────────────────

def test_set_models_stage_moves_a_hand_picked_set(conn, orks, stages):
    unit_id = col.create_unit(conn, orks['Boyz'], 10)
    ids = [m['id'] for m in col.unit_models(conn, unit_id)][:3]
    assert col.set_models_stage(conn, ids, stages['Painted']['id']) == 3
    counts = {s['name']: s['count'] for s in col.unit_breakdown(conn, unit_id)}
    assert counts['Painted'] == 3
    assert counts['On sprue'] == 7


def test_setting_a_model_to_its_current_stage_writes_no_history(conn, orks, stages):
    """History records changes, not clicks."""
    unit_id = col.create_unit(conn, orks['Boyz'], 3)
    ids = [m['id'] for m in col.unit_models(conn, unit_id)]
    before = conn.execute('SELECT COUNT(*) c FROM stage_events').fetchone()['c']
    assert col.set_models_stage(conn, ids, stages['On sprue']['id']) == 0
    after = conn.execute('SELECT COUNT(*) c FROM stage_events').fetchone()['c']
    assert after == before


def test_set_unit_stage_counts_tops_up_from_the_back(conn, orks, stages):
    """"Actually, 6 of these 10 are primed" during a reconcile."""
    unit_id = col.create_unit(conn, orks['Boyz'], 10)
    col.set_unit_stage_counts(conn, unit_id, stages['Primed']['id'], 6)
    counts = {s['name']: s['count'] for s in col.unit_breakdown(conn, unit_id)}
    assert counts['Primed'] == 6
    assert counts['On sprue'] == 4


def test_set_unit_stage_counts_fills_from_behind_before_reaching_forward(
        conn, orks, stages):
    """Finished models are the last thing disturbed, never the first."""
    unit_id = col.create_unit(conn, orks['Boyz'], 6)
    models = col.unit_models(conn, unit_id)
    col.set_models_stage(conn, [m['id'] for m in models[:3]],
                         stages['Battle ready']['id'])
    # 3 Battle ready, 3 On sprue. Asking for 3 Primed must take the 3 on sprue.
    assert col.set_unit_stage_counts(conn, unit_id, stages['Primed']['id'], 3) == 3
    counts = {s['name']: s['count'] for s in col.unit_breakdown(conn, unit_id)}
    assert counts['Primed'] == 3
    assert counts['Battle ready'] == 3, 'the finished models must not have moved'


def test_set_unit_stage_counts_can_correct_downwards(conn, orks, stages):
    """Clay saying "only 2 are primed" is him fixing the app, not the reverse."""
    unit_id = col.create_unit(conn, orks['Boyz'], 4)
    col.set_models_stage(conn, [m['id'] for m in col.unit_models(conn, unit_id)],
                         stages['Battle ready']['id'])
    assert col.set_unit_stage_counts(conn, unit_id, stages['Primed']['id'], 2) == 2
    counts = {s['name']: s['count'] for s in col.unit_breakdown(conn, unit_id)}
    assert counts == {**counts, 'Primed': 2, 'Battle ready': 2}


def test_set_unit_stage_counts_reaches_forward_closest_first(conn, orks, stages):
    """When it must pull back, take the nearest model, not the most finished."""
    unit_id = col.create_unit(conn, orks['Boyz'], 2)
    models = col.unit_models(conn, unit_id)
    col.set_models_stage(conn, [models[0]['id']], stages['Painted']['id'])
    col.set_models_stage(conn, [models[1]['id']], stages['Battle ready']['id'])
    col.set_unit_stage_counts(conn, unit_id, stages['Primed']['id'], 1)
    counts = {s['name']: s['count'] for s in col.unit_breakdown(conn, unit_id)}
    assert counts['Primed'] == 1
    assert counts['Battle ready'] == 1, 'Painted was closer, so it moved instead'


# ── The breakdown reads as a pipeline ────────────────────

def test_empty_stages_stay_visible(conn, orks):
    """A gap you can see is information; a row that vanishes is not."""
    unit_id = col.create_unit(conn, orks['Boyz'], 5)
    rows = col.unit_breakdown(conn, unit_id)
    assert len(rows) == 8, 'all eight stages must appear, even the empty ones'
    assert [r['count'] for r in rows] == [0, 5, 0, 0, 0, 0, 0, 0]


# ── Effort weighting ─────────────────────────────────────

def test_progress_is_effort_weighted_not_model_counted(conn, orks, stages):
    """One Deff Dread outweighs eight Boyz, which is the whole point."""
    army_id = col.create_army(conn, 'Da Boyz')
    boyz = col.create_unit(conn, orks['Boyz'], 8, army_id=army_id)
    col.create_unit(conn, orks['Deff Dread'], 1, army_id=army_id)

    col.set_models_stage(conn, [m['id'] for m in col.unit_models(conn, boyz)],
                         stages['Battle ready']['id'])
    stats = col.army_stats(conn, army_id)
    assert stats['model_count'] == 9
    assert stats['done_count'] == 8
    assert stats['effort_total'] == 8 * 1 + 8      # 8 Boyz + 1 Dread at effort 8
    assert stats['effort_done'] == 8
    assert stats['completion'] == 50, \
        '8 of 9 models done is 50% of the effort, not 89%'


# ── Armies are not factions ──────────────────────────────

def test_an_army_may_hold_units_from_another_faction(conn, orks):
    """Clay's Imperial Knights army holds a Callidus Assassin."""
    agents = db.upsert_faction(conn, 'Agents of the Imperium', 'imperial-agents')
    cur = conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
        'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
        ('callidus', 'Callidus Assassin', agents, 2, db.now(), db.now()))
    knights = col.create_army(conn, 'Imperial Knights',
                              primary_faction_id=orks['faction_id'])
    col.create_unit(conn, cur.lastrowid, 1, army_id=knights)
    units = col.list_units(conn, army_id=knights)
    assert units[0]['faction_name'] == 'Agents of the Imperium'


def test_unassigned_units_get_their_own_bucket(conn, orks):
    """A sealed box not committed to an army must not vanish."""
    col.create_army(conn, 'Da Boyz')
    col.create_unit(conn, orks['Boyz'], 10)          # no army_id
    armies = col.list_armies(conn)
    unassigned = [a for a in armies if a['id'] is None]
    assert len(unassigned) == 1
    assert unassigned[0]['model_count'] == 10


def test_unassigned_bucket_hides_when_empty(conn, orks):
    col.create_army(conn, 'Da Boyz')
    assert all(a['id'] is not None for a in col.list_armies(conn))


def test_moving_a_unit_between_armies(conn, orks):
    a = col.create_army(conn, 'Da Boyz')
    b = col.create_army(conn, 'Speed Freeks')
    unit_id = col.create_unit(conn, orks['Boyz'], 10, army_id=a)
    col.move_unit_to_army(conn, unit_id, b)
    assert col.army_stats(conn, a)['model_count'] == 0
    assert col.army_stats(conn, b)['model_count'] == 10
    col.move_unit_to_army(conn, unit_id, None)
    assert col.list_units(conn, unassigned=True)[0]['id'] == unit_id


# ── Kits ─────────────────────────────────────────────────

def test_instantiating_a_template_creates_every_model(conn, orks, stages):
    """One action turns a scanned box into its whole contents."""
    cur = conn.execute(
        "INSERT INTO kit_templates (name, faction_id, year, contents_source, "
        "created_at, updated_at) VALUES ('Combat Patrol: Orks', ?, 2024, "
        "'manual', ?, ?)", (orks['faction_id'], db.now(), db.now()))
    template_id = cur.lastrowid
    for datasheet_id, n in ((orks['Boyz'], 20), (orks['Deff Dread'], 1)):
        conn.execute('INSERT INTO kit_template_units (kit_template_id, '
                     'datasheet_id, model_count) VALUES (?, ?, ?)',
                     (template_id, datasheet_id, n))

    kit_id, unit_ids = col.instantiate_template(conn, template_id)
    assert len(unit_ids) == 2
    assert conn.execute('SELECT COUNT(*) c FROM models').fetchone()['c'] == 21
    assert col.get_kit(conn, kit_id)['name'] == 'Combat Patrol: Orks'
    assert {m['stage_name'] for m in col.unit_models(conn, unit_ids[0])} == {'On sprue'}


def test_a_template_with_no_contents_is_refused(conn, orks):
    """Silently creating an empty kit would look like it worked."""
    cur = conn.execute(
        "INSERT INTO kit_templates (name, contents_source, created_at, updated_at) "
        "VALUES ('Mystery Box', 'manual', ?, ?)", (db.now(), db.now()))
    with pytest.raises(ValueError, match='no contents'):
        col.instantiate_template(conn, cur.lastrowid)


def test_recording_a_kit_by_name_invents_no_models(conn, orks):
    """Ownership now, contents whenever. Guessing what is inside a box from
    its name is the one thing this app will not do — a plausible guess is
    wrong in places with no signal about which.

    Moved down from the route tests with the Kits screens; `create_kit` is
    still what the magazine seed and `instantiate_template` call."""
    kit_id = col.create_kit(conn, 'Nobz Mob 2019')

    assert col.get_kit(conn, kit_id)['name'] == 'Nobz Mob 2019'
    assert conn.execute('SELECT COUNT(*) FROM units WHERE kit_id = ?',
                        (kit_id,)).fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 0


# ── The shortlist of things to part with ─────────────────
#
# Clay: "Not sold, sell a list of things to part with."
#
# The first attempt read that as a disposal — models that had *gone*, excluded
# from ownership. Wrong tense. These are still on the shelf and still his; he
# has only decided they are going. So the tests below are the mirror of the
# ones they replaced: where those checked a sold model left every count, these
# check a listed one leaves none of them.


def test_listing_to_sell_changes_nothing_about_owning(conn, orks):
    unit = col.create_unit(conn, orks['Boyz'], 10)

    col.list_for_sale(conn, unit, 3)

    assert col.inventory(conn)[0]['owned_count'] == 10


def test_the_shortlist_is_counted_separately(conn, orks):
    unit = col.create_unit(conn, orks['Boyz'], 10)

    col.list_for_sale(conn, unit, 3)

    assert col.pile_counts(conn, unit) == {'owned': 10, 'wishlist': 0,
                                           'sell': 3}


def test_a_listed_model_still_advances(conn, orks):
    """It is on the shelf, so paint mode still offers it. Deciding to sell
    something is not deciding to stop working on it — a squad often sells
    better finished."""
    unit = col.create_unit(conn, orks['Boyz'], 10)
    col.list_for_sale(conn, unit, 10)

    assert col.advance_unit(conn, unit) == 10


def test_the_most_advanced_are_listed_first(conn, orks):
    """The opposite order to removing, on purpose. Removing takes the models
    with no work in them; this is a shortlist for parting with, and a finished
    squad is what is actually worth listing."""
    unit = col.create_unit(conn, orks['Boyz'], 10)
    col.advance_unit(conn, unit, count=4)

    col.list_for_sale(conn, unit, 3)

    listed = [r['name'] for r in conn.execute("""
        SELECT s.name FROM models m JOIN stages s ON s.id = m.stage_id
         WHERE m.for_sale_on IS NOT NULL""")]
    assert listed == ['Assembled'] * 3


def test_changing_your_mind_clears_the_flag(conn, orks):
    unit = col.create_unit(conn, orks['Boyz'], 10)
    col.list_for_sale(conn, unit, 3)

    col.unlist_for_sale(conn, unit, 2)

    assert col.pile_counts(conn, unit)['sell'] == 1


def test_unlisting_writes_the_ids_it_selected(conn, orks):
    """It shipped as an UPDATE that built placeholders and passed no values —
    a 500 the browser swallowed as a toast, which is why the count in the
    verification run is what caught it rather than an exception."""
    unit = col.create_unit(conn, orks['Boyz'], 4)
    col.list_for_sale(conn, unit, 4)

    assert col.unlist_for_sale(conn, unit, 4) == 4
    assert col.pile_counts(conn, unit)['sell'] == 0


def test_listing_more_than_you_have_lists_what_you_have(conn, orks):
    unit = col.create_unit(conn, orks['Boyz'], 3)

    assert col.list_for_sale(conn, unit, 99) == 3


def test_listing_twice_does_not_double_count(conn, orks):
    """The second call has nothing left to flag, so it must find nothing
    rather than re-flagging what is already listed."""
    unit = col.create_unit(conn, orks['Boyz'], 3)
    col.list_for_sale(conn, unit, 3)

    assert col.list_for_sale(conn, unit, 3) == 0


def test_wishlisting_more_wants_without_owning(conn, orks):
    """Wishlist has been position 0 of the ladder since the first migration, so
    this needs no storage of its own."""
    unit = col.create_unit(conn, orks['Boyz'], 10)

    col.wishlist_models(conn, unit, 5)

    row = col.inventory(conn)[0]
    assert row['owned_count'] == 10 and row['wanted_count'] == 5


def test_no_ownership_surface_loses_a_listed_model(conn, orks):
    """The mirror of the sweep this replaced, and it matters for the same
    reason. Wiring `for_sale_on` into an ownership filter by mistake would
    make the collection *under*-count — the same silent-for-months failure as
    over-counting, just the other way."""
    army = col.create_army(conn, 'Da Boyz')
    unit = col.create_unit(conn, orks['Boyz'], 10, army_id=army)

    col.list_for_sale(conn, unit, 4)

    assert col.inventory(conn)[0]['owned_count'] == 10, 'collection screen'
    assert col.list_units(conn)[0]['model_count'] == 10, 'unit lists'
    assert [a for a in col.list_armies(conn)
            if a['id'] == army][0]['model_count'] == 10, 'army cards'
    assert col.export_inventory(conn)['datasheets'][0]['owned'] == 10, 'export'
    assert len(col.unit_models(conn, unit)) == 10, 'the unit page'
    assert col.home_summary(conn)['models'] == 10, 'the homepage'


def test_the_shortlist_narrows_the_collection(conn, orks):
    unit = col.create_unit(conn, orks['Boyz'], 10)
    col.create_unit(conn, orks['Deff Dread'], 1)

    col.list_for_sale(conn, unit, 2)

    rows = col.inventory(conn, only_for_sale=True)
    assert [r['name'] for r in rows] == ['Boyz']
    assert rows[0]['for_sale_count'] == 2


# ── Correcting a kit ─────────────────────────────────────
#
# These three moved down from tests/test_routes.py when the Kits screens were
# removed. The screens are gone; `update_kit` and `delete_kit` are not — the
# magazine seed and `instantiate_template` still write kits, and the journey
# still reads them. Deleting the route tests would have taken the only cover
# these two functions had with them.


def test_editing_a_kit_only_touches_the_fields_given(conn, orks):
    """A caller passing three fields must not blank the other seven.

    The same bug `update_unit` actually shipped: writing every column every
    time, so saving a note silently erased the name."""
    kit_id = col.create_kit(conn, 'Wrecka Krew', notes='keep me',
                            acquired_on='2026-01-02')

    col.update_kit(conn, kit_id, name='Wrecka Krew 2024')

    kit = col.get_kit(conn, kit_id)
    assert kit['name'] == 'Wrecka Krew 2024'
    assert kit['notes'] == 'keep me'
    assert kit['acquired_on'] == '2026-01-02'


def test_a_kit_cannot_be_renamed_to_nothing(conn, orks):
    """A nameless row is unfindable and unfixable."""
    kit_id = col.create_kit(conn, 'Killa Kans')

    with pytest.raises(ValueError):
        col.update_kit(conn, kit_id, name='   ')

    assert col.get_kit(conn, kit_id)['name'] == 'Killa Kans'


def test_deleting_a_kit_takes_its_units_and_models_with_it(conn, orks):
    """Deleting is the *correction* — plastic that was never there. Selling is
    `dispose_kit`, which keeps every row. Both exist because the cheap control
    must not be the one that empties the spend history."""
    kit_id = col.create_kit(conn, 'Wrecka Krew')
    col.create_unit(conn, orks['Boyz'], 10, kit_id=kit_id)

    col.delete_kit(conn, kit_id)

    assert conn.execute('SELECT COUNT(*) FROM kits').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM units').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 0


def test_a_sold_kit_leaves_the_counts_but_keeps_its_rows(conn, orks):
    kit_id = col.create_kit(conn, 'Wrecka Krew', cost_cents=5500)
    army_id = col.create_army(conn, 'Da Boyz')
    col.create_unit(conn, orks['Boyz'], 10, army_id=army_id, kit_id=kit_id)
    assert col.army_stats(conn, army_id)['model_count'] == 10

    col.dispose_kit(conn, kit_id, 'sold', price_cents=4000, note='to Dave')
    assert col.army_stats(conn, army_id)['model_count'] == 0, \
        'a sold kit must leave ownership and effort totals'
    assert conn.execute('SELECT COUNT(*) c FROM models').fetchone()['c'] == 10, \
        'but the rows stay — deleting them corrupts the spend history'
    kit = col.get_kit(conn, kit_id)
    assert (kit['status'], kit['disposed_price_cents']) == ('sold', 4000)


def test_a_listed_kit_still_counts_as_owned(conn, orks):
    """On the market is not gone — and a list depending on it is worth flagging."""
    kit_id = col.create_kit(conn, 'Killa Kans')
    army_id = col.create_army(conn, 'Da Boyz')
    col.create_unit(conn, orks['Deff Dread'], 3, army_id=army_id, kit_id=kit_id)
    col.dispose_kit(conn, kit_id, 'listed')
    assert col.army_stats(conn, army_id)['model_count'] == 3


def test_disposal_is_reversible(conn, orks):
    kit_id = col.create_kit(conn, 'Killa Kans')
    col.dispose_kit(conn, kit_id, 'sold', price_cents=1000)
    col.dispose_kit(conn, kit_id, 'owned')
    kit = col.get_kit(conn, kit_id)
    assert kit['status'] == 'owned' and kit['disposed_on'] is None


# ── Painting session picker ──────────────────────────────

def test_finished_units_drop_out_of_the_paint_picker(conn, orks, stages):
    army_id = col.create_army(conn, 'Da Boyz')
    done = col.create_unit(conn, orks['Boyz'], 2, army_id=army_id)
    todo = col.create_unit(conn, orks['Deff Dread'], 1, army_id=army_id)
    col.set_models_stage(conn, [m['id'] for m in col.unit_models(conn, done)],
                         stages['Battle ready']['id'])
    assert [u['id'] for u in col.paintable_units(conn)] == [todo]


# ── Datasheet picker ─────────────────────────────────────

def test_datasheet_search_excludes_variants(conn, orks):
    """A Legends printing must never be picked by accident in a hurry."""
    conn.execute(
        "INSERT INTO datasheets (bsdata_id, name, faction_id, effort, variant, "
        "created_at, updated_at) VALUES ('v', 'Boyz', ?, 1, 'legends', ?, ?)",
        (orks['faction_id'], db.now(), db.now()))
    hits = col.search_datasheets(conn, 'Boyz')
    assert len(hits) == 1
    assert hits[0]['id'] == orks['Boyz']


# ── Stepping back ────────────────────────────────────────
#
# Every tap in a paint session saves immediately and nothing asks "are you
# sure". That is only tolerable if undo costs one tap too — otherwise the
# screen becomes one you are careful with, and being careful is friction.

def test_a_model_steps_back_one_stage(conn, orks, stages):
    unit = col.create_unit(conn, orks['Boyz'], 1, stage_id=stages['Primed']['id'])

    assert col.retreat_unit(conn, unit) == 1

    assert conn.execute('SELECT stage_id FROM models WHERE unit_id = ?',
                        (unit,)).fetchone()['stage_id'] == stages['Base prepared']['id']


def test_retreat_moves_the_most_advanced_not_the_least(conn, orks, stages):
    """Advance moves the least advanced — "I primed six of ten" means the six
    that weren't. Undo is the mirror: it takes back the step just taken rather
    than disturbing something further back."""
    unit = col.create_unit(conn, orks['Boyz'], 1, stage_id=stages['On sprue']['id'])
    col.add_models(conn, unit, 1, stages['Painted']['id'])

    col.retreat_unit(conn, unit, count=1)

    at = [r['stage_id'] for r in conn.execute(
        'SELECT stage_id FROM models WHERE unit_id = ? ORDER BY id', (unit,))]
    assert stages['On sprue']['id'] in at, 'the sprue model was left alone'
    assert stages['Painted']['id'] not in at, 'the painted one came back a step'


def test_retreat_never_un_owns_a_model(conn, orks, stages):
    """"I have not started this" and "I do not have this" are different facts.
    Stepping back off the first owned stage would silently turn one into the
    other and drop the model out of ownership counts."""
    unit = col.create_unit(conn, orks['Boyz'], 2, stage_id=stages['On sprue']['id'])

    assert col.retreat_unit(conn, unit) == 0

    stage_ids = {r['stage_id'] for r in conn.execute(
        'SELECT stage_id FROM models WHERE unit_id = ?', (unit,))}
    assert stage_ids == {stages['On sprue']['id']}


def test_retreat_can_be_narrowed_to_one_stage(conn, orks, stages):
    unit = col.create_unit(conn, orks['Boyz'], 1, stage_id=stages['Primed']['id'])
    col.add_models(conn, unit, 1, stages['Painted']['id'])

    col.retreat_unit(conn, unit, count=1, from_stage_id=stages['Primed']['id'])

    at = sorted(r['stage_id'] for r in conn.execute(
        'SELECT stage_id FROM models WHERE unit_id = ?', (unit,)))
    assert stages['Painted']['id'] in at, 'the painted model was not touched'
    assert stages['Base prepared']['id'] in at


def test_retreat_undoes_an_advance_exactly(conn, orks, stages):
    """The property that matters: a mis-tap costs one tap to fix."""
    unit = col.create_unit(conn, orks['Boyz'], 5, stage_id=stages['Assembled']['id'])
    before = sorted(r['stage_id'] for r in conn.execute(
        'SELECT stage_id FROM models WHERE unit_id = ?', (unit,)))

    col.advance_unit(conn, unit)
    col.retreat_unit(conn, unit)

    after = sorted(r['stage_id'] for r in conn.execute(
        'SELECT stage_id FROM models WHERE unit_id = ?', (unit,)))
    assert after == before


def test_an_unbased_model_steps_back_over_the_basing_stages(conn, stages):
    """The ladder a model actually walks, in both directions."""
    faction = db.upsert_faction(conn, 'Space Marines', 'space-marines')
    rhino = conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, basing, '
        "created_at, updated_at) VALUES ('rhino', 'Rhino', ?, 8, 'unbased', ?, ?)",
        (faction, db.now(), db.now())).lastrowid
    unit = col.create_unit(conn, rhino, 1, stage_id=stages['Painted']['id'])

    col.retreat_unit(conn, unit)

    assert conn.execute('SELECT stage_id FROM models WHERE unit_id = ?',
                        (unit,)).fetchone()['stage_id'] == stages['Primed']['id']


# ── Two games, one name ──────────────────────────────────
#
# Kill Team factions are slug-prefixed by their importer so they cannot merge
# with a 40,000 faction of the same name — a Kill Team of Sisters is a
# ten-operative team, not the Adepta Sororitas army. That is right, and it
# means a picker printing the bare name offers two identical options.

def test_a_name_in_both_games_is_qualified(conn):
    db.upsert_faction(conn, 'Adepta Sororitas', 'adepta-sororitas')
    db.upsert_faction(conn, 'Adepta Sororitas', 'kt-adepta-sororitas')

    labels = {f['slug']: f['label'] for f in col.list_factions(conn)}

    assert labels['adepta-sororitas'] == 'Adepta Sororitas (Warhammer 40,000)'
    assert labels['kt-adepta-sororitas'] == 'Adepta Sororitas (Kill Team)'


def test_a_name_in_only_one_game_stays_plain(conn):
    """Qualifying every Kill Team entry would add noise to the ones that were
    never ambiguous."""
    db.upsert_faction(conn, 'Orks', 'orks')
    db.upsert_faction(conn, 'Battleclade', 'kt-battleclade')

    labels = {f['slug']: f['label'] for f in col.list_factions(conn)}

    assert labels['orks'] == 'Orks'
    assert labels['kt-battleclade'] == 'Battleclade'


def test_every_faction_knows_its_game(conn):
    db.upsert_faction(conn, 'Orks', 'orks')
    db.upsert_faction(conn, 'Wrecka Krew', 'kt-wrecka-krew')

    systems = {f['slug']: f['game_system'] for f in col.list_factions(conn)}

    assert systems['orks'] == 'wh40k'
    assert systems['kt-wrecka-krew'] == 'killteam'


def test_the_two_rows_stay_separate(conn):
    """Merging them would put Kill Team operatives in a 40,000 army."""
    a = db.upsert_faction(conn, 'Orks', 'orks')
    b = db.upsert_faction(conn, 'Orks', 'kt-orks')

    assert a != b
    assert len(col.list_factions(conn)) == 2


# ── Removing models ──────────────────────────────────────
#
# The gap Clay found: adding too many was easy and there was no way back. Note
# what this is NOT — a disposal. A kit that got sold keeps every model row and
# the spend history; this deletes rows, so it is only for plastic that was
# never there.

def test_removing_models_takes_the_ones_never_started(conn, orks, stages):
    """The whole point of the ordering. Ten Boyz, six of them built, trim to
    six: the four that go are the four still on sprue, not four of the six."""
    unit_id = col.create_unit(conn, orks['Boyz'], 10)
    col.advance_unit(conn, unit_id, count=6)          # 6 Assembled, 4 On sprue

    result = col.remove_models(conn, unit_id, 4)

    assert result == {'removed': 4, 'remaining': 6, 'unit_deleted': False}
    counts = {s['name']: s['count'] for s in col.unit_breakdown(conn, unit_id)}
    assert counts['On sprue'] == 0, 'the untouched models are the ones to go'
    assert counts['Assembled'] == 6, 'recorded work is the last thing to leave'


def test_within_a_stage_the_newest_go_first(conn, orks, stages):
    """The accidental extras are the ones just typed in. Both orderings — least
    advanced, then newest — point at the same models, which is the safety."""
    unit_id = col.create_unit(conn, orks['Boyz'], 5)
    original = [m['id'] for m in col.unit_models(conn, unit_id)]
    col.add_models(conn, unit_id, 3, stages['On sprue']['id'])

    col.remove_models(conn, unit_id, 3)

    assert [m['id'] for m in col.unit_models(conn, unit_id)] == original


def test_removing_every_model_deletes_the_unit(conn, orks, stages):
    """A unit with no models is a zero in every count that can never become
    anything else."""
    unit_id = col.create_unit(conn, orks['Boyz'], 3)

    result = col.remove_models(conn, unit_id, 3)

    assert result == {'removed': 3, 'remaining': 0, 'unit_deleted': True}
    assert col.get_unit(conn, unit_id) is None


def test_asking_for_more_than_there_are_removes_what_there_is(conn, orks, stages):
    """Rather than erroring. "Remove 50" from a unit of 3 means all of them."""
    unit_id = col.create_unit(conn, orks['Boyz'], 3)

    result = col.remove_models(conn, unit_id, 50)

    assert result['removed'] == 3 and result['unit_deleted'] is True


def test_removing_none_changes_nothing(conn, orks, stages):
    unit_id = col.create_unit(conn, orks['Boyz'], 4)

    assert col.remove_models(conn, unit_id, 0) == \
        {'removed': 0, 'remaining': 4, 'unit_deleted': False}
    assert len(col.unit_models(conn, unit_id)) == 4


def test_a_removed_models_history_goes_with_it(conn, orks, stages):
    """stage_events is ON DELETE CASCADE. Right for a model that never existed,
    and exactly why a disposal has to be a different operation."""
    unit_id = col.create_unit(conn, orks['Boyz'], 2)
    col.advance_unit(conn, unit_id)
    before = conn.execute('SELECT COUNT(*) AS n FROM stage_events').fetchone()['n']
    assert before > 0

    col.remove_models(conn, unit_id, 2)

    assert conn.execute(
        'SELECT COUNT(*) AS n FROM stage_events').fetchone()['n'] == 0


def test_removing_from_one_unit_leaves_another_alone(conn, orks, stages):
    """The LIMIT is scoped by unit_id. Getting that wrong would eat a
    neighbouring squad silently."""
    keep = col.create_unit(conn, orks['Boyz'], 5)
    trim = col.create_unit(conn, orks['Boyz'], 5)

    col.remove_models(conn, trim, 2)

    assert len(col.unit_models(conn, keep)) == 5
    assert len(col.unit_models(conn, trim)) == 3
