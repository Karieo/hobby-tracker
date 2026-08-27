"""Games played, per list — did the thing you built actually win?

Clay: *"games played by list, win/loss and point difference 0-100."*

He plays in another app. His words: *"I'll play games in another app, it's
called Battlebase… Playing the game is a whole other thing."* So this is not a
game tracker and must not grow into one — no missions, no objectives, no
turn-by-turn anything. A list in this app is the thing a collection is built
*towards*, and whether it wins is the one fact about it that cannot be derived
from plastic on a shelf.

Both scores, and nothing derived is stored
------------------------------------------
He asked for win/loss *and* a point difference; asked which way round, he chose
to record both totals. It is one extra number typed and strictly more
information: "lost 85–90" and "lost 45–90" are two completely different
evenings, and a stored result alone cannot tell them apart.

The result and the margin both fall out of the two scores, so **neither is
stored**. A stored result is a second copy of a fact the row already carries,
and this app has already paid for one of those — `army_lists.points_total`
colliding with `SELECT l.*` put the word "None" on the list index for months.

The 0–100 range is Clay's own number
------------------------------------
From his message, not from a rulebook a model half-remembers — the same bargain
`lists.BATTLE_SIZES` makes with the two battle sizes he took off a screenshot.
It is deliberately **not** scoped by game system. Kill Team lists live in
`army_lists` too and are not scored out of 100, but writing that rule from
recall is the one change to this repo that would do real damage, so the range
stays the single number Clay gave and widens when he says so.

Tallied in Python, not in SQL
-----------------------------
`_result` decides won/lost/drew in exactly one place, and both the list page
and the list index count through it. The alternative — a `SUM(CASE WHEN ...)`
per screen — is two copies of the rule, and they drift the moment a draw is
defined differently in one of them. Clay plays perhaps weekly; a few hundred
rows over years is nothing to read and total in memory.

The average margin is signed, and that is the point
---------------------------------------------------
`−12` says the list is losing by twelve on average. An absolute average would
read `12` whether you are winning or losing every game, which answers nothing
anybody would ask.
"""

from datetime import date

import database as db

#: The top of the range Clay named: "point difference 0-100". His number, not a
#: rule this module knows — see the module docstring. Pinned as data by
#: `test_the_score_range_is_the_one_clay_named`.
SCORE_MAX = 100


def add_game(conn, list_id, your_score, their_score,
             played_on=None, opponent_faction_id=None, notes=None):
    """Record one game. Returns its id.

    `played_on` defaults to today because that is when it is being typed in,
    and a date field that has to be filled every time is friction on the one
    screen this app can least afford it on.
    """
    yours = _score(your_score, 'your score')
    theirs = _score(their_score, 'their score')
    return conn.execute(
        'INSERT INTO games (list_id, played_on, your_score, their_score, '
        'opponent_faction_id, notes, created_at) VALUES (?,?,?,?,?,?,?)',
        (list_id, played_on or str(date.today()), yours, theirs,
         _optional_int(opponent_faction_id),
         (notes or '').strip() or None, db.now())).lastrowid


def _score(value, what):
    """A score is an integer in Clay's range, and refusing is better than
    clamping: a mistyped 850 silently stored as 100 is a wrong record that
    reads as a real one."""
    try:
        score = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f'{what} must be a number')
    if not 0 <= score <= SCORE_MAX:
        raise ValueError(f'{what} must be between 0 and {SCORE_MAX}')
    return score


def _optional_int(value):
    text = str(value or '').strip()
    return int(text) if text else None


def games_for(conn, list_id):
    """One list's games, newest first, each carrying its result and margin.

    Newest first because the question this screen answers is "how is this list
    doing", and the last game is the one you remember. `/gallery` is the
    oldest-first screen, and it is the only one.
    """
    return [_decorate(dict(row)) for row in conn.execute("""
        SELECT g.*, f.name AS opponent_faction_name
          FROM games g
          LEFT JOIN factions f ON f.id = g.opponent_faction_id
         WHERE g.list_id = ?
         ORDER BY g.played_on DESC, g.id DESC
    """, (list_id,))]


def _decorate(row):
    row['result'] = _result(row['your_score'], row['their_score'])
    row['margin'] = row['your_score'] - row['their_score']
    return row


def _result(yours, theirs):
    """won / lost / drew. The single place the rule lives."""
    if yours > theirs:
        return 'won'
    if yours < theirs:
        return 'lost'
    return 'drew'


def records(conn):
    """``{list_id: record}`` for every list that has played.

    A dict rather than a per-list query, so the list index costs one read no
    matter how many lists there are. Lists with no games are simply absent —
    the caller shows nothing rather than a row of zeroes, which is the same
    call the home screen's quick piles make.
    """
    by_list = {}
    for row in conn.execute(
            'SELECT list_id, your_score, their_score FROM games'):
        by_list.setdefault(row['list_id'], []).append(row)
    return {list_id: record(rows) for list_id, rows in by_list.items()}


def record(rows):
    """``{'played', 'won', 'lost', 'drew', 'margin_avg', 'label'}``.

    Takes rows rather than a connection so the list page tallies the games it
    has already fetched instead of asking again — and so this stays a pure
    function that a test can hand three dictionaries.
    """
    tally = {'won': 0, 'lost': 0, 'drew': 0}
    margin = 0
    for row in rows:
        tally[_result(row['your_score'], row['their_score'])] += 1
        margin += row['your_score'] - row['their_score']

    played = len(rows)
    return {
        'played': played,
        **tally,
        'margin_avg': round(margin / played, 1) if played else 0.0,
        'label': _label(tally),
    }


def _label(tally):
    """"3–1", or "3–1–1" when there are draws.

    A function rather than a template branch, for the reason
    `backup_status.describe` and `lists.points_headline` are: the phrasing gets
    tested and the screen holds no logic. Draws are dropped when there are none
    because "3–1–0" invites the reader to work out what the third number is,
    every single time, to learn nothing.
    """
    parts = [tally['won'], tally['lost']]
    if tally['drew']:
        parts.append(tally['drew'])
    return '–'.join(str(n) for n in parts)


def delete_game(conn, game_id):
    """Remove one. The fix for a mistyped score.

    A deletion rather than an edit form: a game is five small fields, and
    re-entering it is quicker than building and maintaining a way to change
    one. It is a correction, not a disposal — there is no history worth
    keeping in a score that was never the score.
    """
    return conn.execute('DELETE FROM games WHERE id = ?',
                        (game_id,)).rowcount > 0
