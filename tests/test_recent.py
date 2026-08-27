"""What you got done lately — spec §5.1, read back out of `stage_events`.

The tests that matter most here are the ones about what must *not* count.
Arrivals, purchases and undone mis-taps all leave rows in the same table as
real work, and every one of them inflates the number in the direction that
makes it worthless: a home screen that congratulates Clay for typing in a
collection he already owned is one he stops believing.
"""

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collection as col
import database as db
import recent


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / 'recent.db')
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
    """Boyz walk the full ladder; a Trukk is a vehicle and skips basing."""
    made = {}
    for bsid, name, effort, basing in (('boyz', 'Boyz', 1, 'based'),
                                       ('knight', 'Knight', 8, 'based'),
                                       ('trukk', 'Trukk', 3, 'unbased')):
        made[name] = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'basing, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
            (bsid, name, orks, effort, basing, db.now(), db.now())).lastrowid
    return made


def unit(conn, stages, datasheet_id, count, at='On sprue'):
    return col.create_unit(conn, datasheet_id, count, stage_id=stages[at])


def backdate(conn, unit_id, days):
    """Move a unit's whole history back, as if it happened `days` ago.

    Rewriting the stamps beats faking the clock: these are the rows the screen
    actually reads, and a test that stubbed `date.today` would pass against a
    query that ignored `changed_at` entirely.
    """
    when = str(date.today() - timedelta(days=days))
    conn.execute(
        'UPDATE stage_events SET changed_at = ? WHERE model_id IN '
        '(SELECT id FROM models WHERE unit_id = ?)', (when, unit_id))


def mark(conn):
    """The last event written, so the next few can be dated on their own."""
    return conn.execute(
        'SELECT COALESCE(MAX(id), 0) AS n FROM stage_events').fetchone()['n']


def stamp_since(conn, event_id, days):
    """Date everything written after `event_id` as `days` days ago.

    `backdate` moves a unit's whole history together, which cannot express the
    thing that matters here: the same model advancing into one stage on two
    different days, with a retreat between them that cancels neither.
    """
    when = str(date.today() - timedelta(days=days))
    conn.execute('UPDATE stage_events SET changed_at = ? WHERE id > ?',
                 (when, event_id))


# ── What counts as work ──────────────────────────────────

def test_ten_boyz_advanced_one_stage_is_a_sixth_of_them(conn, stages, sheets):
    """The ladder is seven owned stages, so six steps from sprue to done.
    Ten Boyz of effort 1 moving one step is 10 × 1/6."""
    u = unit(conn, stages, sheets['Boyz'], 10)
    col.advance_unit(conn, u)

    assert recent.summary(conn)['effort_done'] == pytest.approx(1.7, abs=0.05)


def test_carrying_a_mob_all_the_way_spends_its_whole_effort(conn, stages, sheets):
    """Ten Boyz from sprue to battle ready is 10.0 — the same number
    `/backlog` would have shown as still to spend the day before."""
    u = unit(conn, stages, sheets['Boyz'], 10)
    for _ in range(6):
        col.advance_unit(conn, u)

    result = recent.summary(conn)

    assert result['effort_done'] == pytest.approx(10.0, abs=0.05)
    assert result['finished'] == 10


def test_a_knight_outweighs_a_mob_of_boyz(conn, stages, sheets):
    """The whole reason this app weights by effort. One Knight finished is
    worth more of an evening than one Boy, and the number has to say so."""
    knight = unit(conn, stages, sheets['Knight'], 1)
    boy = unit(conn, stages, sheets['Boyz'], 1)
    for u in (knight, boy):
        for _ in range(6):
            col.advance_unit(conn, u)

    result = recent.summary(conn)

    assert result['finished'] == 2, 'two models either way'
    assert result['finished_effort'] == pytest.approx(9.0, abs=0.05)


def test_a_vehicle_walks_its_own_shorter_ladder(conn, stages, sheets):
    """The same move is worth more to a model with fewer steps to walk.

    Assembled → Primed skips 'Base prepared'. For Boyz that is two of the six
    steps they walk; for a Trukk, which has no base, 'Base prepared' is not on
    its ladder at all, so it is one step of four. Charging every vehicle the
    full ladder is what would make them look permanently unfinished — the fix
    `backlog._steps_by_basing` already makes, mirrored here.

    Asserted on `_fraction`, because that is where the claim lives. Going
    through `summary` would multiply by two different efforts and round to one
    decimal, so a passing comparison would say nothing about the ladder.
    """
    ladder = col.stage_ladder(conn)
    walks = recent._walks(conn, ladder)
    assembled, primed = stages['Assembled'], stages['Primed']
    at = {s['id']: s['position'] for s in ladder}

    assert recent._fraction(at[assembled], at[primed], walks['unbased']) == 1 / 4
    assert recent._fraction(at[assembled], at[primed], walks['based']) == 2 / 6


def test_the_shorter_ladder_reaches_the_screen(conn, stages, sheets):
    """And the same move, through the real function, on a real Trukk."""
    trukk = unit(conn, stages, sheets['Trukk'], 1, at='Assembled')
    col.set_models_stage(conn, _models(conn, trukk), stages['Primed'])

    # Effort 3, one step of four: 0.75, shown to one decimal.
    assert recent.summary(conn)['effort_done'] == 0.8


def _models(conn, unit_id):
    return [r['id'] for r in
            conn.execute('SELECT id FROM models WHERE unit_id = ?', (unit_id,))]


# ── What must not count ──────────────────────────────────

def test_typing_in_a_painted_collection_is_not_a_month_of_work(conn, stages, sheets):
    """The one that matters most. `/add` takes a stage word so onboarding can
    be honest about where things are; if arrivals counted, Clay's first week
    would report hundreds of models finished and the number would be worthless
    exactly when it is new."""
    unit(conn, stages, sheets['Boyz'], 40, at='Battle ready')

    result = recent.summary(conn)

    assert result['finished'] == 0
    assert result['effort_done'] == 0.0
    assert result['models_touched'] == 0


def test_buying_something_crosses_no_step(conn, stages, sheets):
    """Wishlist → On sprue is a purchase. The walk starts at On sprue, the
    same line `backlog._work_left` draws when it refuses to call a wishlist
    model backlog."""
    u = unit(conn, stages, sheets['Boyz'], 10, at='Wishlist')
    col.advance_unit(conn, u)

    result = recent.summary(conn)

    assert result['effort_done'] == 0.0
    assert result['models_touched'] == 10, 'they did move — it just was not work'


def test_a_mistap_undone_the_same_day_leaves_nothing_behind(conn, stages, sheets):
    """`retreat_unit` exists for wet hands. An advance and its undo must not
    leave "10 finished" standing on the home screen."""
    u = unit(conn, stages, sheets['Boyz'], 10, at='Based')
    col.advance_unit(conn, u)
    col.retreat_unit(conn, u)

    result = recent.summary(conn)

    assert result['finished'] == 0
    assert result['effort_done'] == 0.0


def test_a_strip_back_weeks_later_keeps_the_work_it_undid(conn, stages, sheets):
    """Same day is what makes a retreat a correction. Stripping a squad in
    March to redo it properly is a real thing that happened, and the evenings
    that painted it are not deleted by regretting them."""
    u = unit(conn, stages, sheets['Boyz'], 10, at='Painted')
    col.advance_unit(conn, u)                  # → Based
    backdate(conn, u, 10)
    col.retreat_unit(conn, u)                  # today, ten days later

    assert recent.summary(conn)['effort_done'] > 0


def test_a_month_can_never_go_negative(conn, stages, sheets):
    """Nothing here subtracts. A screen that punished Clay for redoing a unit
    he was unhappy with would be arguing with the hobby."""
    u = unit(conn, stages, sheets['Boyz'], 10, at='Painted')
    backdate(conn, u, 20)
    for _ in range(3):
        col.retreat_unit(conn, u)

    result = recent.summary(conn)

    assert result['effort_done'] == 0.0
    assert result['finished'] == 0


# ── The window ───────────────────────────────────────────

def test_work_older_than_the_window_is_not_lately(conn, stages, sheets):
    u = unit(conn, stages, sheets['Boyz'], 10)
    col.advance_unit(conn, u)
    backdate(conn, u, 60)

    assert recent.summary(conn)['effort_done'] == 0.0


def test_the_window_edge_is_inclusive_and_lands_where_it_should(conn, stages, sheets):
    """Both sides of the boundary, because only the pair pins it.

    "Last 30 days" means today and the 29 before it. Asserting that 29 days ago
    counts proves nothing on its own — it passes just as happily against a
    window a day too wide, which is the off-by-one worth catching, since it
    silently drops or gains a day of work on every read.
    """
    inside = unit(conn, stages, sheets['Boyz'], 10)
    col.advance_unit(conn, inside)
    backdate(conn, inside, recent.WINDOW_DAYS - 1)
    assert recent.summary(conn)['effort_done'] > 0, 'the last day in is in'

    conn.execute('DELETE FROM stage_events')
    outside = unit(conn, stages, sheets['Boyz'], 10)
    col.advance_unit(conn, outside)
    backdate(conn, outside, recent.WINDOW_DAYS)
    assert recent.summary(conn)['effort_done'] == 0.0, 'the first day out is out'


def test_the_window_is_askable_for_other_lengths(conn, stages, sheets):
    u = unit(conn, stages, sheets['Boyz'], 10)
    col.advance_unit(conn, u)
    backdate(conn, u, 10)

    assert recent.summary(conn, days=7)['effort_done'] == 0.0
    assert recent.summary(conn, days=30)['effort_done'] > 0


# ── The breakdown ────────────────────────────────────────

def test_the_stages_reached_are_named_in_ladder_order(conn, stages, sheets):
    u = unit(conn, stages, sheets['Boyz'], 10)
    for _ in range(3):
        col.advance_unit(conn, u)

    names = [s['name'] for s in recent.summary(conn)['by_stage']]

    assert names == ['Assembled', 'Base prepared', 'Primed']


def test_the_breakdown_does_not_repeat_the_headline(conn, stages, sheets):
    """`by_stage` stops short of Battle ready, because `finished` is that
    number already. A screen reading "10 finished · … · 10 battle ready" says
    the same thing twice, and two copies of one number are two to keep in
    step."""
    u = unit(conn, stages, sheets['Boyz'], 10)
    for _ in range(6):
        col.advance_unit(conn, u)

    result = recent.summary(conn)

    assert result['finished'] == 10
    assert [s['name'] for s in result['by_stage']] == [
        'Assembled', 'Base prepared', 'Primed', 'Painted', 'Based']


def test_a_model_repainted_inside_the_window_is_one_model_painted(conn, stages, sheets):
    """`by_stage` counts distinct models, not events.

    Painted ten days ago, stripped five days ago, painted again yesterday —
    three separate days, so no retreat cancels anything and *two* advances into
    Painted survive. They are one model, and reaching a stage twice does not
    mean two models got there. Milestones, the same reasoning that makes
    `finished` a set.
    """
    u = unit(conn, stages, sheets['Boyz'], 1, at='Primed')

    at = mark(conn)
    col.advance_unit(conn, u)                  # → Painted
    stamp_since(conn, at, 10)

    at = mark(conn)
    col.retreat_unit(conn, u)                  # stripped back
    stamp_since(conn, at, 5)

    at = mark(conn)
    col.advance_unit(conn, u)                  # → Painted again
    stamp_since(conn, at, 1)

    result = recent.summary(conn)
    painted = [s for s in result['by_stage'] if s['name'] == 'Painted']

    assert painted[0]['n'] == 1, 'one model, painted twice'
    assert result['models_touched'] == 1


def test_finishing_counts_models_not_arrivals_at_the_end(conn, stages, sheets):
    """`finished` is distinct models that *reached* Battle ready, which is not
    the same as models sitting there — most of a collection arrives already
    painted and never crosses the line inside any window."""
    unit(conn, stages, sheets['Boyz'], 30, at='Battle ready')
    walked = unit(conn, stages, sheets['Boyz'], 2, at='Based')
    col.advance_unit(conn, walked)

    assert recent.summary(conn)['finished'] == 2


def test_nothing_at_all_reads_as_a_quiet_month(conn, stages, sheets):
    unit(conn, stages, sheets['Boyz'], 10)

    result = recent.summary(conn)

    assert result['models_touched'] == 0
    assert result['by_stage'] == []
    assert result['finished'] == 0


# ── History, not ownership ───────────────────────────────

def test_selling_a_squad_does_not_unpaint_it(conn, stages, sheets):
    """The one counting surface that does not filter disposals, deliberately.
    Painting twenty Boyz in March and selling them in April does not un-paint
    them — the evenings happened, and `journey.py` keeps sold kits in the story
    for the same reason.

    If this test is ever "fixed" to match the ownership surfaces, the module
    docstring has to change first.
    """
    u = unit(conn, stages, sheets['Boyz'], 10, at='Based')
    col.advance_unit(conn, u)
    conn.execute("UPDATE models SET disposed_on = '2026-08-01' "
                 ' WHERE unit_id = ?', (u,))

    assert recent.summary(conn)['finished'] == 10


def test_a_correction_takes_its_history_with_it(conn, stages, sheets):
    """`remove_models` deletes rows outright — plastic that was never there —
    and `stage_events` is ON DELETE CASCADE. Nothing in this module has to
    know about that, and this pins that it stays true."""
    u = unit(conn, stages, sheets['Boyz'], 10)
    col.advance_unit(conn, u)
    col.remove_models(conn, u, 10)

    assert recent.summary(conn)['effort_done'] == 0.0
