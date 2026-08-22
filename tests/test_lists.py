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
