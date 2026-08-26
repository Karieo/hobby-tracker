"""Three states, and the third is the one that matters.

Spec §9: "points against the limit, legal unit sizes, faction consistency, and
the three-state badge that refuses to show a false green."

Before this the list page printed the total and the limit beside each other and
never compared them, so a 2,050-point list for a 2,000-point game looked
exactly like a legal one. The trap in fixing that is the green tick: a badge
saying "legal" while a third of the checks could not run is worse than no badge,
because it gets believed. Most of what is pinned here is the refusals.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
import list_validate
import lists


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / 'validate.db')
    db.migrate(path)
    c = db.connect(path)
    yield c
    c.close()


@pytest.fixture
def orks(conn):
    return db.upsert_faction(conn, 'Orks', 'orks')


def sheet(conn, name, faction_id, low=10, high=20, system='wh40k'):
    return conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, min_models, '
        'max_models, game_system, created_at, updated_at) '
        'VALUES (?,?,?,?,?,?,?,?)',
        (name.lower(), name, faction_id, low, high, system,
         db.now(), db.now())).lastrowid


def a_list(conn, limit=None, faction_id=None):
    return lists.create_list(conn, 'Saturday', faction_id=faction_id,
                             points_limit=limit)


def entry(conn, list_id, datasheet_id, count, points=None):
    eid = lists.add_entry(conn, list_id, datasheet_id, count)
    conn.execute('UPDATE list_entries SET points_snapshot = ? WHERE id = ?',
                 (points, eid))
    return eid


# ── Points against the limit ─────────────────────────────

def test_over_the_limit_is_a_problem(conn, orks):
    """The failure this exists to stop: a list you only discover is illegal
    with the models on the table."""
    lid = a_list(conn, limit=2000)
    entry(conn, lid, sheet(conn, 'Boyz', orks), 20, points=2050)

    got = list_validate.validate(conn, lid)

    assert got['state'] == 'problem'
    assert got['points']['over'] == 50
    assert '50 over' in got['problems'][0]['message']


def test_under_the_limit_with_everything_priced_is_clean(conn, orks):
    lid = a_list(conn, limit=2000, faction_id=orks)
    entry(conn, lid, sheet(conn, 'Boyz', orks), 20, points=180)

    got = list_validate.validate(conn, lid)

    assert got['state'] == 'ok', got
    assert got['problems'] == []


def test_no_limit_cannot_be_checked_and_says_so(conn, orks):
    """Not a pass. Nothing was compared, so nothing may be claimed."""
    lid = a_list(conn, faction_id=orks)
    entry(conn, lid, sheet(conn, 'Boyz', orks), 20, points=180)

    got = list_validate.validate(conn, lid)

    assert got['state'] == 'review'
    assert any(r['kind'] == 'points' for r in got['review'])


def test_an_unpriced_entry_keeps_a_list_under_the_limit_out_of_the_green(
        conn, orks):
    """The missing number could take it over, so under-the-limit is not an
    answer yet — it is the absence of one."""
    lid = a_list(conn, limit=2000, faction_id=orks)
    entry(conn, lid, sheet(conn, 'Boyz', orks), 20, points=180)
    entry(conn, lid, sheet(conn, 'Nobz', orks, low=5, high=10), 5)

    got = list_validate.validate(conn, lid)

    assert got['state'] == 'review'
    assert 'could be higher' in [r['message'] for r in got['review']][0]


def test_over_the_limit_stays_a_problem_even_with_something_unpriced(
        conn, orks):
    """Unpriced entries can only add. A list already over is over whatever
    they turn out to be, so this must not soften to `review`."""
    lid = a_list(conn, limit=100, faction_id=orks)
    entry(conn, lid, sheet(conn, 'Boyz', orks), 20, points=180)
    entry(conn, lid, sheet(conn, 'Nobz', orks, low=5, high=10), 5)

    got = list_validate.validate(conn, lid)

    assert got['state'] == 'problem'


def test_an_unresolved_row_keeps_the_list_out_of_the_green(conn, orks):
    """It has no datasheet, so it is in none of the totals — which is exactly
    why the total cannot be trusted as final."""
    lid = a_list(conn, limit=2000, faction_id=orks)
    entry(conn, lid, sheet(conn, 'Boyz', orks), 20, points=180)
    # Straight to SQL: `add_entry` refuses a null datasheet, and rightly —
    # unresolved rows only ever arrive through a paste the parser could not
    # match, which is the state being reproduced here.
    conn.execute(
        'INSERT INTO list_entries (list_id, position, raw_name, model_count) '
        'VALUES (?, ?, ?, ?)', (lid, 99, 'Sum Fing Unresolved', 5))

    got = list_validate.validate(conn, lid)

    assert got['state'] == 'review'


# ── Unit sizes ───────────────────────────────────────────

def test_a_unit_below_its_minimum_is_a_problem(conn, orks):
    lid = a_list(conn, limit=2000, faction_id=orks)
    entry(conn, lid, sheet(conn, 'Boyz', orks, low=10, high=20), 7, points=90)

    got = list_validate.validate(conn, lid)

    assert got['state'] == 'problem'
    assert 'allows 10–20' in got['problems'][0]['message']


def test_a_unit_above_its_maximum_is_a_problem(conn, orks):
    lid = a_list(conn, limit=2000, faction_id=orks)
    entry(conn, lid, sheet(conn, 'Boyz', orks, low=10, high=20), 25, points=90)

    got = list_validate.validate(conn, lid)

    assert got['state'] == 'problem'


def test_a_datasheet_with_no_size_cannot_be_checked(conn, orks):
    """415 of the 1,445 imported 40,000 datasheets carry no unit size. Passing
    those silently is how the badge becomes a lie."""
    lid = a_list(conn, limit=2000, faction_id=orks)
    entry(conn, lid, sheet(conn, 'Boyz', orks, low=None, high=None), 7,
          points=90)

    got = list_validate.validate(conn, lid)

    assert got['state'] == 'review'
    assert got['problems'] == []
    assert any(r['kind'] == 'size' for r in got['review'])


def test_a_kill_team_operative_is_not_size_checked(conn, orks):
    """An operative is one model by construction, so a size check against it
    would flag every entry of more than one."""
    lid = a_list(conn, limit=2000, faction_id=orks)
    entry(conn, lid, sheet(conn, 'Kommando', orks, low=1, high=1,
                           system='killteam'), 5, points=90)

    got = list_validate.validate(conn, lid)

    assert got['problems'] == []


# ── Faction ──────────────────────────────────────────────

def test_another_faction_is_worth_a_look_never_a_fault(conn, orks):
    """Allied detachments and Imperial Agents are legal and look exactly like
    a mistake from here. Calling this illegal would be the app being
    confidently wrong about the one thing it cannot see."""
    marines = db.upsert_faction(conn, 'Space Marines', 'space-marines')
    lid = a_list(conn, limit=2000, faction_id=orks)
    entry(conn, lid, sheet(conn, 'Boyz', orks), 20, points=180)
    entry(conn, lid, sheet(conn, 'Intercessors', marines, low=5, high=10), 5,
          points=100)

    got = list_validate.validate(conn, lid)

    assert got['state'] == 'review'
    assert got['problems'] == []
    note = next(r for r in got['review'] if r['kind'] == 'faction')
    assert 'Intercessors' in note['message']
    assert 'Space Marines' in note['message']


def test_a_list_with_no_faction_cannot_be_checked_against_one(conn, orks):
    lid = a_list(conn, limit=2000)
    entry(conn, lid, sheet(conn, 'Boyz', orks), 20, points=180)

    got = list_validate.validate(conn, lid)

    assert got['state'] == 'review'
    assert any(r['kind'] == 'faction' for r in got['review'])


# ── The shape of the answer ──────────────────────────────

def test_a_problem_outranks_a_review(conn, orks):
    """One definite fault decides the badge. A list that is over the limit is
    not "worth a look" because something else was also unclear."""
    lid = a_list(conn, limit=100)          # no faction: a review on its own
    entry(conn, lid, sheet(conn, 'Boyz', orks), 20, points=180)

    got = list_validate.validate(conn, lid)

    assert got['state'] == 'problem'
    assert got['review'], 'the review notes are still reported alongside'


def test_an_empty_list_is_not_green(conn, orks):
    """Nothing was checked, so nothing passed."""
    got = list_validate.validate(conn, a_list(conn))

    assert got['state'] == 'review'


def test_a_missing_list_raises(conn):
    with pytest.raises(ValueError, match='no list'):
        list_validate.validate(conn, 9999)


def test_a_kill_team_list_is_not_nagged_for_a_battle_size(conn):
    """Battle sizes are 40,000's, and the picker offers its two. A Kill Team
    list has no limit it could choose, so "Set one on the list" points at a
    door that does not exist — the same reason `_check_sizes` scopes itself to
    SIZED_SYSTEM. A check worth printing has to be actionable."""
    faction = db.upsert_faction(conn, 'Kommandos', 'kt-kommandos')
    operative = sheet(conn, 'Kommando Boy', faction, low=1, high=1,
                      system='killteam')
    list_id = a_list(conn, faction_id=faction)
    entry(conn, list_id, operative, 1)

    messages = [n['message'] for n in list_validate.validate(conn, list_id)['review']]

    assert not any('points limit' in m for m in messages), messages


def test_a_40k_list_with_no_limit_still_is(conn, orks):
    list_id = a_list(conn, faction_id=orks)
    entry(conn, list_id, sheet(conn, 'Boyz', orks), 10)

    messages = [n['message'] for n in list_validate.validate(conn, list_id)['review']]

    assert any('points limit' in m for m in messages), messages


def test_an_unresolved_row_keeps_the_limit_question_open(conn, orks):
    """It might resolve to a 40,000 unit. Guessing otherwise would silently
    stop checking a list that needs it."""
    list_id = a_list(conn, faction_id=orks)
    conn.execute('INSERT INTO list_entries (list_id, position, raw_name, '
                 'model_count) VALUES (?, 1, ?, 10)', (list_id, 'Sum Fing'))

    messages = [n['message'] for n in list_validate.validate(conn, list_id)['review']]

    assert any('points limit' in m for m in messages), messages


def test_an_empty_list_is_still_asked(conn):
    """Nothing says which game it is yet, and a list about to be filled with
    40,000 entries should be told."""
    list_id = a_list(conn)

    messages = [n['message'] for n in list_validate.validate(conn, list_id)['review']]

    assert any('points limit' in m for m in messages), messages
