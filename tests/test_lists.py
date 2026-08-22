"""Lists, the gap, and the wishlist.

Spec §2.6, the keystone. Everything else in the app pushes — a model moves when
Clay feels like moving it. A list pulls: it names a target, and the gap turns
"what should I work on" into an answer.

The gap's two halves lead to different evenings. Buying is a trip to a shop;
painting is a night at the desk. A single "not ready" number would hide which,
so these tests pin them apart.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collection as col
import database as db
import lists


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / 'lists.db')
    db.migrate(path)
    c = db.connect(path)
    yield c
    c.close()


@pytest.fixture
def stages(conn):
    return {s['name']: s['id'] for s in col.stage_ladder(conn)}


@pytest.fixture
def orks(conn):
    return db.upsert_faction(conn, 'Orks', 'orks')


@pytest.fixture
def sheets(conn, orks):
    made = {}
    for bsid, name, effort in (('boyz', 'Boyz', 1), ('nobz', 'Nobz', 2),
                               ('wb', 'Warboss', 3)):
        made[name] = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'min_models, max_models, created_at, updated_at) '
            'VALUES (?,?,?,?,10,20,?,?)',
            (bsid, name, orks, effort, db.now(), db.now())).lastrowid
    return made


def own(conn, stages, datasheet_id, count, stage='On sprue'):
    return col.create_unit(conn, datasheet_id, count, stage_id=stages[stage])


# ── Lists ────────────────────────────────────────────────

def test_a_list_needs_a_name(conn):
    with pytest.raises(ValueError, match='needs a name'):
        lists.create_list(conn, '   ')


def test_entries_snapshot_their_points(conn, sheets, orks):
    """A list records what Clay meant to field on a day. The manual changes
    under it, and the list should not silently change with it."""
    conn.execute(
        'INSERT INTO datasheet_points (datasheet_id, faction_id, model_count, '
        'points, effective_from) VALUES (?, ?, 10, 85, ?)',
        (sheets['Boyz'], orks, db.now()))
    lid = lists.create_list(conn, 'Saturday', faction_id=orks)

    lists.add_entry(conn, lid, sheets['Boyz'], 10)

    assert conn.execute('SELECT points_snapshot FROM list_entries').fetchone()[0] == 85


def test_points_prefer_the_faction_s_own_price(conn, sheets, orks):
    """35 names carry different points per faction. A global row is a fallback,
    never a preference."""
    other = db.upsert_faction(conn, 'Blood Angels', 'blood-angels')
    for faction, points in ((None, 230), (orks, 255)):
        conn.execute(
            'INSERT INTO datasheet_points (datasheet_id, faction_id, '
            'model_count, points, effective_from) VALUES (?, ?, 10, ?, ?)',
            (sheets['Boyz'], faction, points, db.now()))

    assert lists.points_for(conn, sheets['Boyz'], 10, orks) == 255
    assert lists.points_for(conn, sheets['Boyz'], 10, other) == 230, 'falls back'


def test_an_entry_must_point_at_a_real_datasheet(conn):
    lid = lists.create_list(conn, 'Saturday')
    with pytest.raises(ValueError, match='no datasheet'):
        lists.add_entry(conn, lid, 999, 10)


# ── The gap ──────────────────────────────────────────────

def test_owning_nothing_is_all_buy_and_no_paint(conn, sheets):
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)

    gap = lists.list_gap(conn, lid)

    assert (gap['to_buy'], gap['to_paint']) == (20, 0)
    assert not gap['fieldable']


def test_owning_them_unpainted_is_all_paint_and_no_buy(conn, sheets, stages):
    own(conn, stages, sheets['Boyz'], 20)
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)

    gap = lists.list_gap(conn, lid)

    assert (gap['to_buy'], gap['to_paint']) == (0, 20)
    assert gap['fieldable'], 'he can put them on the table, they just look bad'
    assert not gap['ready']


def test_a_partial_shortfall_splits_into_both(conn, sheets, stages):
    """Owns 12 of the 20 needed, 5 of those finished."""
    own(conn, stages, sheets['Boyz'], 7)
    own(conn, stages, sheets['Boyz'], 5, 'Battle ready')
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)

    gap = lists.list_gap(conn, lid)

    assert gap['to_buy'] == 8
    assert gap['to_paint'] == 7, 'the 12 owned, less the 5 already done'


def test_models_not_yet_bought_are_never_also_counted_as_paint(conn, sheets):
    """Counting the same missing model in both halves doubles the work on
    screen and makes the number useless."""
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)

    gap = lists.list_gap(conn, lid)

    assert gap['to_buy'] + gap['to_paint'] == 20


def test_owning_more_than_the_list_needs_is_not_a_negative_gap(conn, sheets, stages):
    own(conn, stages, sheets['Boyz'], 40, 'Battle ready')
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)

    gap = lists.list_gap(conn, lid)

    assert (gap['to_buy'], gap['to_paint']) == (0, 0)
    assert gap['ready']


def test_a_sold_kit_reopens_the_gap(conn, sheets, stages):
    """Ownership comes from the collection, so a disposal shows up here."""
    kit = col.create_kit(conn, 'Boyz box')
    col.create_unit(conn, sheets['Boyz'], 20, kit_id=kit,
                    stage_id=stages['Battle ready'])
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)
    assert lists.list_gap(conn, lid)['ready']

    col.dispose_kit(conn, kit, 'sold')

    assert lists.list_gap(conn, lid)['to_buy'] == 20


def test_wishlist_models_do_not_close_a_gap(conn, sheets, stages):
    """Wanting twenty Boyz is not owning twenty Boyz."""
    own(conn, stages, sheets['Boyz'], 20, 'Wishlist')
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)

    assert lists.list_gap(conn, lid)['to_buy'] == 20


# ── The wishlist ─────────────────────────────────────────

def test_raising_a_wishlist_creates_wanted_models(conn, sheets, stages):
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)

    added = lists.raise_wishlist(conn, lid)

    assert added == 20
    row = col.inventory(conn)[0]
    assert row['wanted_count'] == 20
    assert row['owned_count'] == 0, 'wanting is not owning'


def test_raising_twice_tops_up_rather_than_stacking(conn, sheets, stages):
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)
    lists.raise_wishlist(conn, lid)

    assert lists.raise_wishlist(conn, lid) == 0
    assert col.inventory(conn)[0]['wanted_count'] == 20


def test_buying_some_shrinks_the_next_raise(conn, sheets, stages):
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)
    lists.raise_wishlist(conn, lid)

    own(conn, stages, sheets['Boyz'], 20)          # he went and bought them

    assert lists.raise_wishlist(conn, lid) == 0
    assert lists.list_gap(conn, lid)['to_buy'] == 0


def test_the_wishlist_records_which_list_raised_it(conn, sheets):
    """What tells "seven Boyz short for Saturday" apart from a standing want."""
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)
    lists.raise_wishlist(conn, lid)

    want = lists.wishlist(conn)[0]

    assert want['wanted'] == 20
    assert want['from_lists'] == 20
    assert 'Saturday' in want['list_names']


def test_deleting_a_list_leaves_its_wants_alone(conn, sheets):
    """Clay still wants them; they are his to clear."""
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)
    lists.raise_wishlist(conn, lid)

    lists.delete_list(conn, lid)

    want = lists.wishlist(conn)[0]
    assert want['wanted'] == 20
    assert want['from_lists'] == 0, 'no longer attributed to a list that is gone'


def test_list_summaries_carry_readiness(conn, sheets, stages):
    own(conn, stages, sheets['Boyz'], 20, 'Battle ready')
    ready = lists.create_list(conn, 'Ready')
    lists.add_entry(conn, ready, sheets['Boyz'], 20)
    short = lists.create_list(conn, 'Short')
    lists.add_entry(conn, short, sheets['Nobz'], 5)

    by_name = {r['name']: r for r in lists.list_lists(conn)}

    assert by_name['Ready']['ready'] is True
    assert by_name['Short']['ready'] is False
    assert by_name['Short']['to_buy'] == 5


# ── Wanting a box from the catalogue ─────────────────────
#
# The catalogue's payback. Browsing "what exists" is only half useful if
# finding something you want leaves you to type its contents in by hand.

@pytest.fixture
def box(conn, sheets, orks):
    import scanning as scan
    return scan.create_template(
        conn, 'Orks: Trukk Boyz',
        [{'datasheet_id': sheets['Boyz'], 'model_count': 11},
         {'datasheet_id': sheets['Nobz'], 'model_count': 1}],
        faction_id=orks, year=2026)


def test_wanting_a_box_wants_its_contents(conn, box):
    added = lists.want_template(conn, box)

    assert added == 12
    wanted = {r['name']: r['wanted'] for r in lists.wishlist(conn)}
    assert wanted == {'Boyz': 11, 'Nobz': 1}


def test_a_wanted_box_says_which_box_to_buy(conn, box):
    """"11 Boyz, 1 Trukk" is a parts list. "Orks: Trukk Boyz" is a purchase."""
    lists.want_template(conn, box)

    row = next(r for r in lists.wishlist(conn) if r['name'] == 'Boyz')

    assert row['from_boxes'] == 11
    assert 'Orks: Trukk Boyz' in row['box_names']


def test_wanting_the_same_box_twice_does_not_stack(conn, box):
    lists.want_template(conn, box)

    assert lists.want_template(conn, box) == 0
    assert sum(r['wanted'] for r in lists.wishlist(conn)) == 12


def test_two_boxes_sharing_a_unit_are_both_wanted(conn, box, sheets, orks):
    """Wanting two boxes that both hold Boyz means wanting two boxes.
    Collapsing them by datasheet would silently under-order."""
    import scanning as scan
    other = scan.create_template(conn, 'Orks: Boyz',
                                 [{'datasheet_id': sheets['Boyz'],
                                   'model_count': 11}],
                                 faction_id=orks, year=2026)
    lists.want_template(conn, box)

    lists.want_template(conn, other)

    row = next(r for r in lists.wishlist(conn) if r['name'] == 'Boyz')
    assert row['wanted'] == 22


def test_a_wanted_box_is_not_an_owned_box(conn, box):
    lists.want_template(conn, box)

    assert col.inventory(conn)[0]['owned_count'] == 0
    assert conn.execute('SELECT COUNT(*) FROM kits').fetchone()[0] == 0


def test_unwanting_removes_only_what_that_box_added(conn, box, sheets, stages):
    own(conn, stages, sheets['Boyz'], 5)          # unrelated, actually owned
    lists.want_template(conn, box)

    lists.unwant_template(conn, box)

    assert lists.wishlist(conn) == []
    assert col.inventory(conn)[0]['owned_count'] == 5, 'his real models stay'


def test_unwanting_leaves_a_want_from_a_list_alone(conn, box, sheets):
    """Two reasons to want the same models. Dropping one must not drop both."""
    lid = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, lid, sheets['Boyz'], 20)
    lists.raise_wishlist(conn, lid)
    lists.want_template(conn, box)

    lists.unwant_template(conn, box)

    row = next(r for r in lists.wishlist(conn) if r['name'] == 'Boyz')
    assert row['wanted'] == 20
    assert row['from_lists'] == 20


def test_a_box_with_no_contents_cannot_be_wanted(conn):
    empty = conn.execute(
        'INSERT INTO kit_templates (name, created_at, updated_at) '
        'VALUES (?, ?, ?)', ('Mystery', db.now(), db.now())).lastrowid

    with pytest.raises(ValueError, match='nothing to want'):
        lists.want_template(conn, empty)


def test_wanting_a_box_that_does_not_exist_is_refused(conn):
    with pytest.raises(ValueError, match='no kit template'):
        lists.want_template(conn, 999)
