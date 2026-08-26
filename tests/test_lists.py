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


def test_the_index_reports_the_points_it_computed(conn, sheets, orks):
    """`army_lists` has a `points_total` column of its own — what a pasted
    export declared — so `SELECT l.*` returned two columns of that name and
    `dict(row)` kept the stored one. The aggregate was computed and thrown
    away, and every list on the index read "None" where its points belong.

    Found by reading the rendered screen, not by a test: "None / 1500 pts".
    """
    conn.execute(
        'INSERT INTO datasheet_points (datasheet_id, model_count, points, '
        'effective_from) VALUES (?, 10, 100, ?)', (sheets['Boyz'], '2026-01-01'))
    list_id = lists.create_list(conn, 'Saturday', faction_id=orks,
                                points_limit=2000, points_total=999)
    lists.add_entry(conn, list_id, sheets['Boyz'], 10)

    row = lists.list_lists(conn)[0]

    assert row['points_total'] == 100, "this app's own figure, not the paste's"
    assert row['declared_points'] == 999, "the export's claim, kept beside it"


def test_a_list_with_no_priced_entries_reports_zero_not_none(conn):
    """Zero is a number Clay can read. None renders as the word "None"."""
    lists.create_list(conn, 'Empty', points_limit=1000)

    assert lists.list_lists(conn)[0]['points_total'] == 0


# ── Battle sizes ─────────────────────────────────────────

def test_a_limit_that_matches_a_battle_size_is_named(conn):
    """Clay: "There are only 2 list battle sizes for list." The number is what
    is stored; the name is derived on the way out, so there is no second copy
    of the same fact to keep in step."""
    assert lists.battle_size(1000) == 'Incursion'
    assert lists.battle_size(2000) == 'Strike Force'


def test_a_limit_that_matches_nothing_is_not_invented(conn):
    """A list made before the picker existed can carry any number, and the
    screens fall back to showing the figure. Guessing a name for 1500 would put
    a size on screen that the game does not have."""
    assert lists.battle_size(1500) is None
    assert lists.battle_size(None) is None
    assert lists.battle_size(0) is None


def test_a_list_carries_its_battle_size_name(conn):
    list_id = lists.create_list(conn, 'Saturday', points_limit=2000)

    assert lists.get_list(conn, list_id)['battle_size'] == 'Strike Force'


def test_a_list_with_an_odd_limit_still_reads(conn):
    """The picker gaining two options must not make an existing list
    unreadable."""
    list_id = lists.create_list(conn, 'Old one', points_limit=1500)

    row = lists.get_list(conn, list_id)

    assert row['points_limit'] == 1500
    assert row['battle_size'] is None


def test_the_index_names_the_size_too(conn):
    lists.create_list(conn, 'Saturday', points_limit=1000)

    assert lists.list_lists(conn)[0]['battle_size'] == 'Incursion'


def test_the_two_sizes_are_the_ones_the_game_offers(conn):
    """Pinned as data rather than trusted as a comment. These came off a
    screenshot of the 40,000 app's own picker; a model editing this file later
    from memory is exactly what the test is here to stop."""
    assert lists.BATTLE_SIZES == (('Incursion', 1000), ('Strike Force', 2000))


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


def test_deleting_a_list_does_not_eject_models_another_list_needs(conn, sheets):
    """The bug this guards is the over-buying that `wishlist_claims` exists to
    stop, arriving through the delete door.

    `wishlist_source_list_id` marks *the pool*, and clearing it for every model
    the deleted list happened to raise first drops models a live list is still
    waiting on. Measured before the fix: Saturday raises 20, Sunday claims the
    same 20, deleting Saturday left the pool empty with Sunday's claims still
    standing, and the next raise took the wishlist to 40.
    """
    saturday = lists.create_list(conn, 'Saturday')
    sunday = lists.create_list(conn, 'Sunday')
    for list_id in (saturday, sunday):
        lists.add_entry(conn, list_id, sheets['Boyz'], 20)
        lists.raise_wishlist(conn, list_id)

    lists.delete_list(conn, saturday)

    pooled = conn.execute(
        'SELECT COUNT(*) FROM models '
        ' WHERE wishlist_source_list_id IS NOT NULL').fetchone()[0]
    assert pooled == 20, 'Sunday is still waiting on these — they stay pooled'

    monday = lists.create_list(conn, 'Monday')
    lists.add_entry(conn, monday, sheets['Boyz'], 20)
    lists.raise_wishlist(conn, monday)

    assert lists.wishlist(conn)[0]['wanted'] == 20, \
        'three lists wanting the same twenty is still twenty to buy'


def test_the_pool_marker_never_names_a_list_that_is_gone(conn, sheets):
    """`models.wishlist_source_list_id` is a plain REFERENCES with no ON
    DELETE, so a surviving reference does not dangle — it restricts the delete
    outright. Re-pointing has to land on a real row or on NULL."""
    saturday = lists.create_list(conn, 'Saturday')
    sunday = lists.create_list(conn, 'Sunday')
    for list_id in (saturday, sunday):
        lists.add_entry(conn, list_id, sheets['Boyz'], 20)
        lists.raise_wishlist(conn, list_id)

    lists.delete_list(conn, saturday)

    sources = {r[0] for r in conn.execute(
        'SELECT DISTINCT wishlist_source_list_id FROM models '
        ' WHERE wishlist_source_list_id IS NOT NULL')}
    assert sources == {sunday}
    assert not conn.execute('PRAGMA foreign_key_check').fetchall()


def test_the_last_list_to_go_hands_its_models_over_as_standing_wants(conn, sheets):
    """The other half, and the older behaviour: once no list is waiting on
    them, they stop being a list's shortfall and become a thing Clay simply
    wants. He keeps them either way."""
    saturday = lists.create_list(conn, 'Saturday')
    sunday = lists.create_list(conn, 'Sunday')
    for list_id in (saturday, sunday):
        lists.add_entry(conn, list_id, sheets['Boyz'], 20)
        lists.raise_wishlist(conn, list_id)

    lists.delete_list(conn, saturday)
    lists.delete_list(conn, sunday)

    want = lists.wishlist(conn)[0]
    assert want['wanted'] == 20, 'still his'
    assert want['from_lists'] == 0
    pooled = conn.execute(
        'SELECT COUNT(*) FROM models '
        ' WHERE wishlist_source_list_id IS NOT NULL').fetchone()[0]
    assert pooled == 0, 'no list is asking any more'


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
# A template's payback. Saying what is in a box once is only half useful if
# wanting it later leaves you to type its contents in again.

@pytest.fixture
def box(conn, sheets, orks):
    import kit_templates as scan
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
    import kit_templates as scan
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


def test_two_lists_short_of_the_same_unit_share_one_line(conn, sheets, stages):
    """The wishlist is read on the collection too, where a datasheet wanted by
    two lists used to stack two identical lines with nothing to tell them
    apart. One line, and it still knows both lists raised it."""
    saturday = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, saturday, sheets['Boyz'], 10)
    lists.raise_wishlist(conn, saturday)
    sunday = lists.create_list(conn, 'Sunday')
    lists.add_entry(conn, sunday, sheets['Boyz'], 20)
    lists.raise_wishlist(conn, sunday)

    units = conn.execute(
        'SELECT COUNT(*) FROM units WHERE datasheet_id = ?',
        (sheets['Boyz'],)).fetchone()[0]
    assert units == 1
    row = next(r for r in lists.wishlist(conn) if r['name'] == 'Boyz')
    assert 'Saturday' in row['list_names'] and 'Sunday' in row['list_names']
    # 20, not 30. Deduplicated on the maximum, per the original spec §7: the
    # same twenty Boyz field either game, one at a time. This asserted 30 for
    # months, with a comment saying it pinned the behaviour rather than
    # blessing it — ten models of over-buying on the one screen whose whole job
    # is saying what to buy.
    assert row['wanted'] == 20


def test_the_shared_line_is_the_same_whichever_list_was_raised_first(
        conn, sheets, stages):
    """The reverse of the test above, and the reason claims are a table rather
    than a column. With one `wishlist_source_list_id` per model, whichever list
    ran first owned all of them and the other was invisible on the line while
    still waiting on those exact models."""
    sunday = lists.create_list(conn, 'Sunday')
    lists.add_entry(conn, sunday, sheets['Boyz'], 20)
    lists.raise_wishlist(conn, sunday)
    saturday = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, saturday, sheets['Boyz'], 10)
    lists.raise_wishlist(conn, saturday)

    row = next(r for r in lists.wishlist(conn) if r['name'] == 'Boyz')
    assert row['wanted'] == 20
    assert 'Saturday' in row['list_names'] and 'Sunday' in row['list_names']


def test_a_standing_want_is_not_swallowed_by_a_list(conn, sheets, stages):
    """Five Boyz Clay wishlisted himself and ten a list is short of are
    different facts. Deduplicating is across *lists*; collapsing his own want
    into a list's shortfall would quietly under-order."""
    col.add_or_extend_unit(conn, sheets['Boyz'], 5,
                           stage_id=db.wishlist_stage(conn)['id'])
    saturday = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, saturday, sheets['Boyz'], 10)
    lists.raise_wishlist(conn, saturday)

    row = next(r for r in lists.wishlist(conn) if r['name'] == 'Boyz')
    assert row['wanted'] == 15
    assert row['from_lists'] == 10


def test_a_list_that_shrinks_releases_its_claim_without_buying_twice(
        conn, sheets, stages):
    """The pool is keyed on having been raised by a list, not on a live claim.
    Keyed on the claim, a list cutting its needs would eject those models from
    the pool and the next raise would read it short and buy the same plastic
    again — the bug, one release later."""
    sunday = lists.create_list(conn, 'Sunday')
    entry = lists.add_entry(conn, sunday, sheets['Boyz'], 20)
    lists.raise_wishlist(conn, sunday)
    conn.execute('UPDATE list_entries SET model_count = 5 WHERE id = ?', (entry,))
    lists.raise_wishlist(conn, sunday)

    row = next(r for r in lists.wishlist(conn) if r['name'] == 'Boyz')
    assert (row['wanted'], row['from_lists']) == (20, 5), \
        'the rows stay — Clay was told to buy them — but only five are claimed'

    saturday = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, saturday, sheets['Boyz'], 10)
    lists.raise_wishlist(conn, saturday)

    row = next(r for r in lists.wishlist(conn) if r['name'] == 'Boyz')
    assert row['wanted'] == 20, 'the released models are reused, never rebought'


def test_dropping_one_list_leaves_the_shared_line_standing(conn, sheets, stages):
    """The danger of sharing a line: a stamp applied by unit rather than by
    model would relabel the other list's models, and deleting one list would
    take both."""
    saturday = lists.create_list(conn, 'Saturday')
    lists.add_entry(conn, saturday, sheets['Boyz'], 10)
    lists.raise_wishlist(conn, saturday)
    sunday = lists.create_list(conn, 'Sunday')
    lists.add_entry(conn, sunday, sheets['Boyz'], 20)
    lists.raise_wishlist(conn, sunday)

    stamped = dict(conn.execute("""
        SELECT wishlist_source_list_id, COUNT(*) FROM models
         GROUP BY wishlist_source_list_id""").fetchall())
    assert stamped == {saturday: 10, sunday: 10}, \
        'each list paid for the rows it added and no others — twenty in total'

    # Which lists *need* them is the other question, and it overlaps: Sunday
    # waits on all twenty, Saturday on ten of the same twenty.
    claimed = dict(conn.execute("""
        SELECT list_id, COUNT(*) FROM wishlist_claims
         GROUP BY list_id""").fetchall())
    assert claimed == {saturday: 10, sunday: 20}

    lists.delete_list(conn, sunday)

    row = next(r for r in lists.wishlist(conn) if r['name'] == 'Boyz')
    assert row['wanted'] == 20, 'Clay still wants them; they are his to clear'
    assert row['from_lists'] == 10 and row['list_names'] == 'Saturday'


def test_entries_are_numbered_in_the_order_they_were_added(conn, sheets):
    """Migration 008 numbered every existing entry by the order it was added.
    A writer that then left new ones at the column default would make position
    true of old rows and false of new ones — the worst possible state for a
    column the report is about to order by."""
    lid = lists.create_list(conn, 'Saturday')
    ids = [lists.add_entry(conn, lid, sheets['Boyz'], 20),
           lists.add_entry(conn, lid, sheets['Nobz'], 5),
           lists.add_entry(conn, lid, sheets['Boyz'], 10)]
    rows = conn.execute('SELECT id, position FROM list_entries '
                        ' WHERE list_id = ? ORDER BY position', (lid,)).fetchall()
    assert [r['id'] for r in rows] == ids
    assert [r['position'] for r in rows] == [0, 1, 2]


def test_two_lists_number_their_entries_independently(conn, sheets):
    saturday = lists.create_list(conn, 'Saturday')
    sunday = lists.create_list(conn, 'Sunday')
    lists.add_entry(conn, saturday, sheets['Boyz'], 20)
    second = lists.add_entry(conn, sunday, sheets['Boyz'], 20)
    row = conn.execute('SELECT position FROM list_entries WHERE id = ?',
                       (second,)).fetchone()
    assert row['position'] == 0, 'position is per list, not global'


def test_an_entry_from_the_builder_is_marked_manually_resolved(conn, sheets):
    """It could not have been created without a datasheet Clay chose, which is
    the same thing migration 008 said about every entry that predated it."""
    lid = lists.create_list(conn, 'Saturday')
    entry = lists.add_entry(conn, lid, sheets['Boyz'], 20)
    row = conn.execute('SELECT raw_name, points, resolved_by FROM list_entries'
                       ' WHERE id = ?', (entry,)).fetchone()
    assert row['resolved_by'] == 'manual'
    assert row['raw_name'] is None and row['points'] is None, \
        'there was no pasted text to disagree with'


def test_a_pasted_entry_keeps_the_line_and_the_points_it_claimed(conn, sheets):
    """Recorded beside the app's own snapshot, never instead of it — §2.7
    settled that this app prices a list from the Munitorum manual."""
    lid = lists.create_list(conn, 'Saturday')
    entry = lists.add_entry(conn, lid, sheets['Boyz'], 20,
                            raw_name='20x Boyz [180pts]', points=180)
    row = conn.execute('SELECT raw_name, points, points_snapshot FROM '
                       'list_entries WHERE id = ?', (entry,)).fetchone()
    assert row['raw_name'] == '20x Boyz [180pts]'
    assert row['points'] == 180
    assert row['points'] != row['points_snapshot'] or row['points_snapshot'] is None


def test_a_box_with_no_contents_cannot_be_wanted(conn):
    empty = conn.execute(
        'INSERT INTO kit_templates (name, created_at, updated_at) '
        'VALUES (?, ?, ?)', ('Mystery', db.now(), db.now())).lastrowid

    with pytest.raises(ValueError, match='nothing to want'):
        lists.want_template(conn, empty)


def test_wanting_a_box_that_does_not_exist_is_refused(conn):
    with pytest.raises(ValueError, match='no kit template'):
        lists.want_template(conn, 999)
