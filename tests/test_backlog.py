"""The backlog: a big push or a quick win.

Spec §5.5. What is pinned here is mostly the *ordering*, because the ordering
is the feature — "what should I start tonight" is a question about which of
these is the shortest evening, and a screen that answers it by counting
miniatures gets it wrong. Ten Boyz on sprue and ten Boyz that need only basing
are both "ten models left"; they are nothing like the same job.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backlog as bl
import collection as col
import database as db


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / 'backlog.db')
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


def sheet(conn, name, faction_id, effort=1, basing=None):
    return conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, basing, '
        'min_models, max_models, game_system, created_at, updated_at) '
        "VALUES (?,?,?,?,?,1,30,'wh40k',?,?)",
        (name.lower(), name, faction_id, effort, basing,
         db.now(), db.now())).lastrowid


def unit(conn, datasheet_id, count, stage_id, **kw):
    return col.create_unit(conn, datasheet_id, count, stage_id=stage_id, **kw)


def by_name(rows):
    return [r['display_name'] for r in rows]


# ── What counts as backlog ───────────────────────────────

def test_a_finished_unit_is_not_in_the_backlog(conn, orks, stages):
    unit(conn, sheet(conn, 'Boyz', orks), 10, stages['Battle ready'])

    assert bl.backlog(conn) == []


def test_a_wishlist_unit_is_not_in_the_backlog(conn, orks, stages):
    """It is not on the shelf. A screen that answers "what can I work on
    tonight" with things Clay does not own is worse than an empty one."""
    unit(conn, sheet(conn, 'Boyz', orks), 10, stages['Wishlist'])

    assert bl.backlog(conn) == []


def test_a_partly_finished_unit_counts_only_what_is_left(conn, orks, stages):
    boyz = sheet(conn, 'Boyz', orks)
    uid = unit(conn, boyz, 10, stages['On sprue'])
    models = [r['id'] for r in conn.execute(
        'SELECT id FROM models WHERE unit_id = ? LIMIT 4', (uid,))]
    for mid in models:
        conn.execute('UPDATE models SET stage_id = ? WHERE id = ?',
                     (stages['Battle ready'], mid))

    row = bl.backlog(conn)[0]

    assert row['models_left'] == 6, 'the four that are done are not left'


# ── How much work is left ────────────────────────────────

def test_an_unstarted_unit_is_worth_its_whole_effort(conn, orks, stages):
    unit(conn, sheet(conn, 'Boyz', orks, effort=1), 10, stages['On sprue'])

    assert bl.backlog(conn)[0]['effort_left'] == 10.0


def test_a_nearly_finished_unit_is_worth_almost_nothing(conn, orks, stages):
    """Ten Boyz needing only the final check is a sixth of the mob's effort —
    the number that makes it a quick win rather than "ten models left"."""
    unit(conn, sheet(conn, 'Boyz', orks, effort=1), 10, stages['Based'])

    row = bl.backlog(conn)[0]

    assert row['models_left'] == 10, 'still ten miniatures'
    assert row['effort_left'] == 1.7, 'but a sixth of the work'


def test_one_unstarted_knight_outranks_a_nearly_finished_mob(
        conn, orks, stages):
    """The entire reason this is effort-weighted. Counting models would put
    ten nearly-done Boyz above a Knight that has not been touched."""
    unit(conn, sheet(conn, 'Boyz', orks, effort=1), 10, stages['Based'],
         nickname='Based mob')
    unit(conn, sheet(conn, 'Knight', orks, effort=8), 1, stages['On sprue'],
         nickname='Knight')

    assert by_name(bl.backlog(conn, sort='push')) == ['Knight', 'Based mob']


def test_a_model_with_no_base_walks_a_shorter_ladder(conn, orks, stages):
    """A Rhino has no base, so it is four steps from done and not six. Using
    the full ladder would leave every vehicle looking permanently unfinished."""
    based = sheet(conn, 'Deff Dread', orks, effort=4, basing='based')
    unbased = sheet(conn, 'Trukk', orks, effort=4, basing='unbased')
    unit(conn, based, 1, stages['Assembled'], nickname='Dread')
    unit(conn, unbased, 1, stages['Assembled'], nickname='Trukk')

    rows = {r['display_name']: r['effort_left'] for r in bl.backlog(conn)}

    # Dread: 5 of 6 steps ahead. Trukk: 3 of 4 — Base prepared and Based are
    # not on its ladder at all.
    assert rows['Dread'] == 3.3
    assert rows['Trukk'] == 3.0


def test_basing_nobody_has_answered_keeps_the_full_ladder(conn, orks, stages):
    """None means unanswered, not "no base". Dropping the basing stages on a
    guess would overstate progress — migration 004's whole point."""
    unit(conn, sheet(conn, 'Boyz', orks, effort=6, basing=None), 1,
         stages['Assembled'])

    assert bl.backlog(conn)[0]['effort_left'] == 5.0, '5 of 6 steps ahead'


# ── The orderings ────────────────────────────────────────

def test_big_push_puts_the_longest_evening_first(conn, orks, stages):
    boyz = sheet(conn, 'Boyz', orks, effort=1)
    unit(conn, boyz, 2, stages['On sprue'], nickname='Small')
    unit(conn, boyz, 20, stages['On sprue'], nickname='Large')

    assert by_name(bl.backlog(conn, sort='push')) == ['Large', 'Small']


def test_quick_win_is_the_same_list_the_other_way_up(conn, orks, stages):
    boyz = sheet(conn, 'Boyz', orks, effort=1)
    unit(conn, boyz, 2, stages['On sprue'], nickname='Small')
    unit(conn, boyz, 20, stages['On sprue'], nickname='Large')

    assert by_name(bl.backlog(conn, sort='quick')) == ['Small', 'Large']


def test_longest_waiting_sorts_by_when_the_box_arrived(conn, orks, stages):
    boyz = sheet(conn, 'Boyz', orks)
    old = col.create_kit(conn, 'Old box', acquired_on='2021-03-01')
    new = col.create_kit(conn, 'New box', acquired_on='2026-01-01')
    unit(conn, boyz, 5, stages['On sprue'], kit_id=new, nickname='Newer')
    unit(conn, boyz, 5, stages['On sprue'], kit_id=old, nickname='Older')

    assert by_name(bl.backlog(conn, sort='oldest')) == ['Older', 'Newer']


def test_a_box_with_no_date_sorts_last_not_first(conn, orks, stages):
    """Most kits have no acquisition date — the field is filled at the till and
    the shelf predates the app. Letting unknown masquerade as oldest would bury
    the box Clay actually has had for two years."""
    boyz = sheet(conn, 'Boyz', orks)
    dated = col.create_kit(conn, 'Old box', acquired_on='2021-03-01')
    unit(conn, boyz, 5, stages['On sprue'], kit_id=dated, nickname='Dated')
    unit(conn, boyz, 5, stages['On sprue'], nickname='Undated')

    assert by_name(bl.backlog(conn, sort='oldest')) == ['Dated', 'Undated']


def test_by_army_puts_unassigned_last(conn, orks, stages):
    """Unassigned is the bucket, not an army."""
    boyz = sheet(conn, 'Boyz', orks)
    army = col.create_army(conn, 'Waaagh', orks)
    unit(conn, boyz, 5, stages['On sprue'], army_id=army, nickname='In army')
    unit(conn, boyz, 5, stages['On sprue'], nickname='Loose')

    assert by_name(bl.backlog(conn, sort='army')) == ['In army', 'Loose']


def test_an_unknown_sort_falls_back_rather_than_failing(conn, orks, stages):
    """It arrives from a query string, so it is whatever someone typed."""
    unit(conn, sheet(conn, 'Boyz', orks), 10, stages['On sprue'])

    assert bl.backlog(conn, sort='nonsense') == bl.backlog(conn, sort='push')


def test_narrowing_to_an_army_leaves_the_others_out(conn, orks, stages):
    boyz = sheet(conn, 'Boyz', orks)
    army = col.create_army(conn, 'Waaagh', orks)
    unit(conn, boyz, 5, stages['On sprue'], army_id=army, nickname='Mine')
    unit(conn, boyz, 5, stages['On sprue'], nickname='Other')

    assert by_name(bl.backlog(conn, army_id=army)) == ['Mine']


# ── The summary line ─────────────────────────────────────

def test_the_totals_carry_the_raw_count_beside_the_weighted_one(
        conn, orks, stages):
    """`CLAUDE.md`: raw counts show alongside, never instead. A percentage of
    models is meaningless when a Knight and a Termagant are both "1 model"."""
    unit(conn, sheet(conn, 'Boyz', orks, effort=1), 10, stages['On sprue'])
    unit(conn, sheet(conn, 'Knight', orks, effort=8), 1, stages['On sprue'])

    got = bl.totals(bl.backlog(conn))

    assert got == {'units': 2, 'models_left': 11, 'effort_left': 18.0}
