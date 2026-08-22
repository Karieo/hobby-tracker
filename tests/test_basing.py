"""Not every model has a base.

Spec §2.5, the thing Clay flagged and could not see a way through: "base is
optional because not everything has a base — that one's gonna be interesting."

The pipeline used to make `Base prepared` and `Based` mandatory for every
model, so a Rhino either stalled at a stage it could never leave or was
advanced through one that never happened. Every progress figure is
effort-weighted, so a false advance on an effort-8 vehicle overstates the whole
collection.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collection as col
import database as db


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / 'basing.db')
    db.migrate(path)
    c = db.connect(path)
    yield c
    c.close()


@pytest.fixture
def stages(conn):
    return {s['name']: s['id'] for s in col.stage_ladder(conn)}


def datasheet(conn, bsid, name, effort=8, basing=None):
    faction = db.upsert_faction(conn, 'Space Marines', 'space-marines')
    return conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, basing, '
        'created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
        (bsid, name, faction, effort, basing, db.now(), db.now())).lastrowid


# ── The ladder a model actually walks ────────────────────

def test_the_basing_stages_are_marked_in_the_data(conn):
    """Matched by a column, not by name — renaming a stage must not silently
    detach the rule from it."""
    basing = {s['name'] for s in col.stage_ladder(conn) if s['is_basing']}
    assert basing == {'Base prepared', 'Based'}


def test_an_unbased_model_walks_a_shorter_ladder(conn):
    full = col.stages_for(conn, 'based')
    short = col.stages_for(conn, 'unbased')

    assert len(full) - len(short) == 2
    assert 'Based' not in {s['name'] for s in short}
    assert 'Battle ready' in {s['name'] for s in short}, 'it still finishes'


def test_unsaid_behaves_exactly_as_before(conn):
    """NULL means nobody has said. The rules data cannot tell us, so nothing is
    reclassified behind Clay's back."""
    assert col.stages_for(conn, None) == col.stages_for(conn, 'based')


# ── Advancing ────────────────────────────────────────────

def test_a_rhino_steps_from_primed_straight_to_painted(conn, stages):
    rhino = datasheet(conn, 'rhino', 'Rhino', basing='unbased')
    unit = col.create_unit(conn, rhino, 1, stage_id=stages['Primed'])

    col.advance_unit(conn, unit)

    now = conn.execute('SELECT stage_id FROM models WHERE unit_id = ?',
                       (unit,)).fetchone()['stage_id']
    assert now == stages['Painted']


def test_a_rhino_never_lands_on_a_basing_stage(conn, stages):
    rhino = datasheet(conn, 'rhino', 'Rhino', basing='unbased')
    unit = col.create_unit(conn, rhino, 1, stage_id=stages['On sprue'])

    seen = []
    for _ in range(10):
        col.advance_unit(conn, unit)
        seen.append(conn.execute('SELECT stage_id FROM models WHERE unit_id = ?',
                                 (unit,)).fetchone()['stage_id'])

    assert stages['Base prepared'] not in seen
    assert stages['Based'] not in seen
    assert seen[-1] == stages['Battle ready'], 'and it does reach the end'


def test_a_dreadnought_still_walks_every_stage(conn, stages):
    """Effort 8 like a Rhino, and it does have a base.

    Its keywords differ (Vehicle + Walker, against the Rhino's Vehicle alone),
    which is the signal behind the hint — but a signal checked against nine
    models is not a rule, so it stays a suggestion and this stays explicit.
    """
    dread = datasheet(conn, 'dread', 'Redemptor Dreadnought', basing='based')
    unit = col.create_unit(conn, dread, 1, stage_id=stages['On sprue'])

    seen = []
    for _ in range(10):
        col.advance_unit(conn, unit)
        seen.append(conn.execute('SELECT stage_id FROM models WHERE unit_id = ?',
                                 (unit,)).fetchone()['stage_id'])

    assert stages['Base prepared'] in seen
    assert stages['Based'] in seen
    assert seen[-1] == stages['Battle ready']


def test_an_unbased_model_reaches_battle_ready_in_fewer_steps(conn, stages):
    rhino = datasheet(conn, 'rhino', 'Rhino', basing='unbased')
    dread = datasheet(conn, 'dread', 'Dreadnought', basing='based')

    def steps(datasheet_id):
        unit = col.create_unit(conn, datasheet_id, 1, stage_id=stages['On sprue'])
        n = 0
        while col.advance_unit(conn, unit):
            n += 1
        return n

    assert steps(rhino) == steps(dread) - 2


# ── Setting it ───────────────────────────────────────────

def test_basing_is_set_not_guessed(conn):
    rhino = datasheet(conn, 'rhino', 'Rhino')
    assert conn.execute('SELECT basing FROM datasheets WHERE id = ?',
                        (rhino,)).fetchone()['basing'] is None

    col.set_basing(conn, rhino, 'unbased')

    assert conn.execute('SELECT basing FROM datasheets WHERE id = ?',
                        (rhino,)).fetchone()['basing'] == 'unbased'


def test_basing_can_be_cleared_back_to_unsaid(conn):
    rhino = datasheet(conn, 'rhino', 'Rhino', basing='unbased')
    col.set_basing(conn, rhino, None)
    assert conn.execute('SELECT basing FROM datasheets WHERE id = ?',
                        (rhino,)).fetchone()['basing'] is None


def test_a_nonsense_value_is_refused(conn):
    rhino = datasheet(conn, 'rhino', 'Rhino')
    with pytest.raises(ValueError, match='unknown basing'):
        col.set_basing(conn, rhino, 'floating')


def test_setting_basing_on_a_missing_datasheet_is_refused(conn):
    with pytest.raises(ValueError, match='no datasheet'):
        col.set_basing(conn, 999, 'unbased')


# ── The hint ─────────────────────────────────────────────

def test_the_hint_reads_the_keyword_signal(conn):
    """Vehicle without Walker suggests no base. Measured, not recalled — see
    migration 004 for the nine datasheets it was checked against."""
    import json as _json
    assert col.basing_hint(_json.dumps(['Vehicle'])) == 'unbased'
    assert col.basing_hint(_json.dumps(['Vehicle', 'Walker'])) is None
    assert col.basing_hint(_json.dumps(['Infantry'])) is None


def test_the_hint_survives_junk(conn):
    assert col.basing_hint(None) is None
    assert col.basing_hint('not json') is None
    assert col.basing_hint('') is None


def test_the_hint_is_offered_only_while_unanswered(conn, stages):
    rhino = datasheet(conn, 'rhino', 'Rhino')
    conn.execute("UPDATE datasheets SET keywords = '[\"Vehicle\"]' WHERE id = ?",
                 (rhino,))
    col.create_unit(conn, rhino, 1, stage_id=stages['On sprue'])

    assert col.inventory(conn)[0]['basing_hint'] == 'unbased'

    col.set_basing(conn, rhino, 'based')
    assert col.inventory(conn)[0]['basing_hint'] is None, \
        'once Clay has said, stop asking'


def test_no_hint_for_something_not_owned(conn):
    rhino = datasheet(conn, 'rhino', 'Rhino')
    conn.execute("UPDATE datasheets SET keywords = '[\"Vehicle\"]' WHERE id = ?",
                 (rhino,))

    rows = col.inventory(conn, query='Rhino', include_unowned=True)

    assert rows[0]['basing_hint'] is None
