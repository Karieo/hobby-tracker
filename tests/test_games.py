"""Games played, per list.

Clay: "games played by list, win/loss and point difference 0-100."

The tests that carry weight here are about what is *not* stored. The result and
the margin are derived from the two scores every time they are read, so there
is no second copy to fall out of step — the failure this repo has already paid
for once, when `army_lists.points_total` collided with an aggregate of the same
name and put the word "None" on the list index.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
import games
import lists as army_lists


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / 'games.db')
    db.migrate(path)
    c = db.connect(path)
    yield c
    c.close()


@pytest.fixture
def orks(conn):
    return db.upsert_faction(conn, 'Orks', 'orks')


@pytest.fixture
def a_list(conn, orks):
    return army_lists.create_list(conn, 'Saturday Boyz', faction_id=orks)


# ── The result is read, never written ────────────────────

def test_a_higher_score_is_a_win(conn, a_list):
    games.add_game(conn, a_list, 85, 72)

    assert games.games_for(conn, a_list)[0]['result'] == 'won'


def test_a_lower_score_is_a_loss(conn, a_list):
    games.add_game(conn, a_list, 45, 90)

    assert games.games_for(conn, a_list)[0]['result'] == 'lost'


def test_equal_scores_are_a_draw(conn, a_list):
    games.add_game(conn, a_list, 60, 60)

    assert games.games_for(conn, a_list)[0]['result'] == 'drew'


def test_the_result_is_not_a_column(conn, a_list):
    """Nothing stores won/lost/drew. A stored result is a second copy of what
    the two scores already say, and this app has paid for one of those."""
    games.add_game(conn, a_list, 85, 72)

    columns = {r[1] for r in conn.execute('PRAGMA table_info(games)')}

    assert 'result' not in columns
    assert 'margin' not in columns
    assert {'your_score', 'their_score'} <= columns


# ── The margin ───────────────────────────────────────────

def test_the_margin_is_the_difference(conn, a_list):
    games.add_game(conn, a_list, 85, 72)

    assert games.games_for(conn, a_list)[0]['margin'] == 13


def test_a_loss_carries_a_negative_margin(conn, a_list):
    """Signed, so "lost by 45" and "won by 45" are not the same number."""
    games.add_game(conn, a_list, 45, 90)

    assert games.games_for(conn, a_list)[0]['margin'] == -45


def test_two_losses_of_very_different_shapes_are_told_apart(conn, a_list):
    """The whole reason both scores are recorded rather than one difference.
    A nail-biter and a hiding are both "a loss" and are not the same game."""
    games.add_game(conn, a_list, 85, 90)
    games.add_game(conn, a_list, 45, 90)

    margins = sorted(g['margin'] for g in games.games_for(conn, a_list))

    assert margins == [-45, -5]


# ── The record ───────────────────────────────────────────

def test_the_record_counts_each_outcome(conn, a_list):
    games.add_game(conn, a_list, 90, 40)
    games.add_game(conn, a_list, 80, 60)
    games.add_game(conn, a_list, 30, 70)
    games.add_game(conn, a_list, 50, 50)

    rec = games.record(games.games_for(conn, a_list))

    assert (rec['played'], rec['won'], rec['lost'], rec['drew']) == (4, 2, 1, 1)


def test_the_average_margin_is_signed(conn, a_list):
    """A list losing every game must not report a cheerful positive number."""
    games.add_game(conn, a_list, 40, 90)
    games.add_game(conn, a_list, 50, 70)

    assert games.record(games.games_for(conn, a_list))['margin_avg'] == -35.0


def test_the_label_drops_draws_when_there_are_none(conn, a_list):
    """"3–1–0" makes the reader work out what the third number is, every time,
    to learn nothing."""
    games.add_game(conn, a_list, 90, 40)
    games.add_game(conn, a_list, 30, 70)

    assert games.record(games.games_for(conn, a_list))['label'] == '1–1'


def test_the_label_shows_draws_when_there_are_some(conn, a_list):
    games.add_game(conn, a_list, 90, 40)
    games.add_game(conn, a_list, 30, 70)
    games.add_game(conn, a_list, 50, 50)

    assert games.record(games.games_for(conn, a_list))['label'] == '1–1–1'


def test_a_list_that_never_played_has_no_record_at_all(conn, a_list):
    """Absent rather than a row of zeroes — the call the home screen's quick
    piles already make."""
    assert games.records(conn) == {}


def test_records_keeps_each_list_to_itself(conn, orks):
    saturday = army_lists.create_list(conn, 'Saturday', faction_id=orks)
    sunday = army_lists.create_list(conn, 'Sunday', faction_id=orks)
    games.add_game(conn, saturday, 90, 40)
    games.add_game(conn, sunday, 20, 80)
    games.add_game(conn, sunday, 30, 70)

    recs = games.records(conn)

    assert recs[saturday]['label'] == '1–0'
    assert recs[sunday]['label'] == '0–2'
    assert recs[sunday]['margin_avg'] == -50.0


def test_the_index_and_the_list_page_agree(conn, a_list):
    """One tally, counted through one `_result`. Two SQL CASE expressions on
    two screens is how a draw ends up defined differently in each."""
    for yours, theirs in ((90, 40), (30, 70), (50, 50)):
        games.add_game(conn, a_list, yours, theirs)

    assert games.records(conn)[a_list] == games.record(
        games.games_for(conn, a_list))


# ── What a game may hold ─────────────────────────────────

def test_the_score_range_is_the_one_clay_named(conn, a_list):
    """0-100 is from Clay's own message, not from a rulebook a model recalls.
    Pinned as data for the same reason `BATTLE_SIZES` is."""
    assert games.SCORE_MAX == 100


def test_a_score_outside_the_range_is_refused_not_clamped(conn, a_list):
    """A mistyped 850 quietly stored as 100 is a wrong record that reads as a
    real one, and nothing on the screen would ever say so."""
    with pytest.raises(ValueError):
        games.add_game(conn, a_list, 850, 72)
    with pytest.raises(ValueError):
        games.add_game(conn, a_list, -1, 72)

    assert games.games_for(conn, a_list) == []


def test_a_score_that_is_not_a_number_is_refused(conn, a_list):
    with pytest.raises(ValueError):
        games.add_game(conn, a_list, 'a lot', 72)


def test_both_ends_of_the_range_are_allowed(conn, a_list):
    games.add_game(conn, a_list, 0, 100)

    assert games.games_for(conn, a_list)[0]['margin'] == -100


def test_the_date_defaults_to_today(conn, a_list):
    """Typed in after the game, so today is right nearly every time. A date
    that has to be filled every game is friction this app cannot afford."""
    from datetime import date
    games.add_game(conn, a_list, 85, 72)

    assert games.games_for(conn, a_list)[0]['played_on'] == str(date.today())


def test_an_opponent_faction_is_optional_and_named_when_given(conn, a_list, orks):
    """"Lost to Custodes three times" is a pattern; "played someone, forgot to
    ask" is a real Tuesday."""
    games.add_game(conn, a_list, 85, 72, opponent_faction_id=orks)
    games.add_game(conn, a_list, 60, 61)

    rows = games.games_for(conn, a_list)

    assert {r['opponent_faction_name'] for r in rows} == {'Orks', None}


def test_a_blank_note_is_stored_as_nothing(conn, a_list):
    games.add_game(conn, a_list, 85, 72, notes='   ')

    assert games.games_for(conn, a_list)[0]['notes'] is None


# ── Ordering and removal ─────────────────────────────────

def test_the_newest_game_comes_first(conn, a_list):
    games.add_game(conn, a_list, 90, 40, played_on='2026-01-01')
    games.add_game(conn, a_list, 30, 70, played_on='2026-08-01')

    assert [g['played_on'] for g in games.games_for(conn, a_list)] == [
        '2026-08-01', '2026-01-01']


def test_a_mistyped_game_can_be_removed(conn, a_list):
    game_id = games.add_game(conn, a_list, 850 // 10, 72)

    assert games.delete_game(conn, game_id) is True
    assert games.games_for(conn, a_list) == []


def test_deleting_a_list_takes_its_games_with_it(conn, a_list):
    """ON DELETE CASCADE. A game belongs to its list the way a stage event
    belongs to its model, and deleting a list is already a confirmed act."""
    games.add_game(conn, a_list, 85, 72)
    army_lists.delete_list(conn, a_list)

    assert conn.execute('SELECT COUNT(*) n FROM games').fetchone()['n'] == 0
