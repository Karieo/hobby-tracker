"""The whole hobby life, in the order it happened.

Clay: "I want it to be a journey of my whole hobby life across all models."

The photo log was only the visible part. `stage_events` has been append-only
since the first commit precisely so this could exist, and nothing read it until
now — so most of what is tested here is that reading it says something true.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collection as col
import database as db
import journey
import photos

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 40


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, 'PHOTO_DIR', str(tmp_path / 'photos'))
    path = str(tmp_path / 'journey.db')
    db.migrate(path)
    c = db.connect(path)
    yield c
    c.close()


@pytest.fixture
def boyz(conn):
    faction = db.upsert_faction(conn, 'Orks', 'orks')
    return conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
        'created_at, updated_at) VALUES (?,?,?,1,?,?)',
        ('boyz', 'Boyz', faction, db.now(), db.now())).lastrowid


def kinds(entries):
    return [e['kind'] for e in entries]


# ── Everything, merged ───────────────────────────────────

def test_a_journey_is_more_than_photographs(conn, boyz):
    """The ask. Photos were the visible part; the stage ladder is where most of
    a hobby life actually is."""
    kit = col.create_kit(conn, 'Combat Patrol: Orks', acquired_on='2026-01-05')
    unit = col.create_unit(conn, boyz, 10, kit_id=kit)
    col.advance_unit(conn, unit)
    photos.add(conn, unit, JPEG, taken_on='2026-02-01')

    assert set(kinds(journey.events(conn))) == {'kit', 'stage', 'photo'}


def test_it_reads_forwards(conn, boyz):
    """Oldest first, and the opposite of every other screen. The rest of the
    app answers about now; this one is the distance travelled."""
    unit = col.create_unit(conn, boyz, 2)
    photos.add(conn, unit, JPEG, taken_on='2026-01-01')
    photos.add(conn, unit, JPEG, taken_on='2026-06-01')

    dates = [e['on'] for e in journey.events(conn)]

    assert dates == sorted(dates)


def test_twenty_models_advancing_is_one_entry(conn, boyz):
    """There is a stage_events row per model. Twenty identical lines is a
    screen nobody scrolls; the count is the interesting part anyway."""
    unit = col.create_unit(conn, boyz, 20)
    col.advance_unit(conn, unit)

    moves = [e for e in journey.events(conn)
             if e['kind'] == 'stage' and not e['arrived']]

    assert len(moves) == 1
    assert moves[0]['count'] == 20 and moves[0]['stage_name'] == 'Assembled'


def test_models_arriving_is_its_own_kind_of_entry(conn, boyz):
    """`add_models` writes a from-nothing event so "a model that never moves
    has no record of when it arrived". That is the start of the story."""
    col.create_unit(conn, boyz, 5)

    arrivals = [e for e in journey.events(conn) if e.get('arrived')]

    assert len(arrivals) == 1 and arrivals[0]['count'] == 5


def test_a_correction_takes_its_mistake_with_it(conn, boyz):
    """A −1 writes an event too, and hiding only the retreat was not enough:
    the advance it undid stayed on the page forever. A mis-tap and its undo
    happen in one sitting, so a same-day retreat cancels the advance."""
    unit = col.create_unit(conn, boyz, 10)
    col.advance_unit(conn, unit)
    col.retreat_unit(conn, unit, count=10)

    moves = [e for e in journey.events(conn)
             if e['kind'] == 'stage' and not e['arrived']]

    assert moves == [], 'the mis-tap and its undo both leave'


def test_a_partial_correction_leaves_what_really_moved(conn, boyz):
    """Advance ten, walk four back: six Boyz really did get assembled."""
    unit = col.create_unit(conn, boyz, 10)
    col.advance_unit(conn, unit)
    col.retreat_unit(conn, unit, count=4)

    moves = [e for e in journey.events(conn)
             if e['kind'] == 'stage' and not e['arrived']]

    assert len(moves) == 1
    assert moves[0]['count'] == 6, 'ten advanced, four came back'


def test_stripping_a_squad_months_later_is_not_a_correction(conn, boyz):
    """Same day is what makes it a mis-tap. Stripping a squad back in March to
    redo it properly is a real thing that happened, and January's evening of
    assembly still happened too."""
    unit = col.create_unit(conn, boyz, 10)
    col.advance_unit(conn, unit)
    conn.execute("UPDATE stage_events SET changed_at = '2026-01-04T19:00:00'")
    col.retreat_unit(conn, unit, count=10)

    moves = [e for e in journey.events(conn)
             if e['kind'] == 'stage' and not e['arrived']]

    assert len(moves) == 1
    assert moves[0]['count'] == 10 and moves[0]['on'] == '2026-01-04'


def test_an_arrival_is_never_cancelled(conn, boyz):
    """Models do not always arrive at the bottom of the ladder: paste-import
    lands a shelf of painted Boyz at Painted, and that arrival event carries
    the stage a −1 walks straight back out of. The netting must not read that
    as "they were never here" — a retreat never un-owns a model, so an arrival
    is not an advance and cannot be cancelled by one."""
    painted = conn.execute(
        "SELECT id FROM stages WHERE name = 'Painted'").fetchone()['id']
    unit = col.create_unit(conn, boyz, 10, stage_id=painted)
    col.retreat_unit(conn, unit, count=10)

    arrivals = [e for e in journey.events(conn) if e.get('arrived')]

    assert len(arrivals) == 1, 'ten Boyz arrived, painted'
    assert arrivals[0]['count'] == 10


def test_the_retreat_is_still_in_the_table(conn, boyz):
    """This filters a view. Losing history would be a different thing, and the
    append-only table exists so that never happens."""
    unit = col.create_unit(conn, boyz, 10)
    col.advance_unit(conn, unit)
    col.retreat_unit(conn, unit, count=10)

    assert conn.execute(
        'SELECT COUNT(*) AS n FROM stage_events').fetchone()['n'] == 30


def test_buying_and_selling_a_box_both_land(conn, boyz):
    """A disposal is a status change rather than a deletion, so "sold the Land
    Raider in March" is part of the story."""
    kit = col.create_kit(conn, 'Land Raider', acquired_on='2026-01-05',
                         cost_cents=9000)
    col.dispose_kit(conn, kit, 'sold', disposed_on='2026-03-09',
                    price_cents=6000)

    entries = journey.events(conn)

    assert kinds(entries) == ['kit', 'gone']
    assert entries[1]['status'] == 'sold'


def test_a_kit_with_no_dates_stays_out(conn, boyz):
    """An undated box has nowhere to sit on a timeline."""
    col.create_kit(conn, 'Some box')
    assert [e for e in journey.events(conn) if e['kind'] == 'kit'] == []


def test_the_stage_entry_says_which_unit_and_army(conn, boyz):
    """"20 × something → painted" with no name is not a memory."""
    army = col.create_army(conn, 'Da Boyz')
    col.create_unit(conn, boyz, 3, army_id=army)

    entry = next(e for e in journey.events(conn) if e['kind'] == 'stage')

    assert entry['unit_name'] == 'Boyz' and entry['army_name'] == 'Da Boyz'


def test_a_nickname_wins_over_the_datasheet(conn, boyz):
    unit = col.create_unit(conn, boyz, 2)
    col.update_unit(conn, unit, nickname='Da Hard Boyz')

    entry = next(e for e in journey.events(conn) if e['kind'] == 'stage')

    assert entry['unit_name'] == 'Da Hard Boyz'


def test_battle_ready_is_flagged(conn, boyz):
    """The terminal stage is the one worth marking differently on the spine."""
    unit = col.create_unit(conn, boyz, 1)
    for _ in range(10):
        col.advance_unit(conn, unit)

    assert any(e.get('is_terminal') for e in journey.events(conn))


# ── The scrubber's frames ────────────────────────────────

def test_the_scrubber_gets_only_showable_pictures(conn, boyz):
    """A scrubber that lands on "the picture is missing" is a broken control
    rather than an honest one — which is why this is its own query and not a
    filter over the merged stream."""
    unit = col.create_unit(conn, boyz, 1)
    saved = photos.add(conn, unit, JPEG)
    photos.add(conn, unit, JPEG)
    os.unlink(os.path.join(photos.PHOTO_DIR, saved['filename']))

    assert len(journey.pictures(conn)) == 1
    assert len([e for e in journey.events(conn) if e['kind'] == 'photo']) == 2, \
        'the stream still says the picture was taken'


# ── Bounds ───────────────────────────────────────────────

def test_the_span_covers_everything_not_just_photos(conn, boyz):
    """It is the header's "2025-11-02 to …", so it has to mean the whole
    journey. Creating the unit writes an arrival dated today, and a span that
    ignored it would claim the story stopped when the last photo was taken."""
    from datetime import date
    unit = col.create_unit(conn, boyz, 1)          # arrives today
    photos.add(conn, unit, JPEG, taken_on='2025-11-02')

    first, last = journey.span(journey.events(conn))

    assert first == '2025-11-02', 'the backdated photo is the start'
    assert last == date.today().isoformat(), 'and the arrival is the end'


def test_an_empty_journey_has_no_span(conn):
    assert journey.span(journey.events(conn)) is None


def test_the_limit_is_honoured(conn, boyz):
    unit = col.create_unit(conn, boyz, 1)
    for day in range(1, 10):
        photos.add(conn, unit, JPEG, taken_on=f'2026-08-{day:02d}')

    assert len(journey.events(conn, limit=4)) == 4
