"""The inventory, and the own-it check that shares its query.

Spec §2.1 and §2.3. Two questions, one screen: "how many of these do I have
and what state are they in", and — standing in a shop with a box in hand —
"do I own this already?"

The second is why `include_unowned` exists. A screen that can only list what
you own cannot tell you that you own none, which is the answer that saves
money.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collection as col
import database as db


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / 'inv.db')
    db.migrate(path)
    c = db.connect(path)
    yield c
    c.close()


@pytest.fixture
def stages(conn):
    return {s['name']: s['id'] for s in col.stage_ladder(conn)}


@pytest.fixture
def sheets(conn):
    faction = db.upsert_faction(conn, 'Orks', 'orks')
    made = {}
    for bsid, name, effort in (('boyz', 'Boyz', 1), ('nobz', 'Nobz', 2),
                               ('grot', 'Gretchin', 1)):
        made[name] = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'min_models, max_models, created_at, updated_at) '
            'VALUES (?,?,?,?,10,20,?,?)',
            (bsid, name, faction, effort, db.now(), db.now())).lastrowid
    made['_faction'] = faction
    return made


# ── The inventory ────────────────────────────────────────

def test_counts_are_grouped_by_datasheet_not_by_box(conn, sheets, stages):
    """Two boxes of Boyz is one row saying twenty, not two rows saying ten."""
    for _ in range(2):
        kit = col.create_kit(conn, 'Boyz box')
        col.create_unit(conn, sheets['Boyz'], 10, kit_id=kit,
                        stage_id=stages['On sprue'])

    rows = col.inventory(conn)

    assert len(rows) == 1
    assert rows[0]['owned_count'] == 20
    assert rows[0]['kit_count'] == 2


def test_built_means_past_the_first_owned_stage(conn, sheets, stages):
    col.create_unit(conn, sheets['Boyz'], 10, stage_id=stages['On sprue'])
    col.create_unit(conn, sheets['Boyz'], 6, stage_id=stages['Assembled'])
    col.create_unit(conn, sheets['Boyz'], 4, stage_id=stages['Battle ready'])

    row = col.inventory(conn)[0]

    assert row['owned_count'] == 20
    assert row['built_count'] == 10, 'assembled and battle ready, not on sprue'
    assert row['done_count'] == 4


def test_a_sold_box_leaves_the_counts_but_keeps_its_rows(conn, sheets, stages):
    """The disposal invariant, from the inventory's side."""
    kept = col.create_kit(conn, 'Kept')
    sold = col.create_kit(conn, 'Sold')
    col.create_unit(conn, sheets['Boyz'], 10, kit_id=kept, stage_id=stages['Painted'])
    col.create_unit(conn, sheets['Boyz'], 10, kit_id=sold, stage_id=stages['Painted'])

    col.dispose_kit(conn, sold, 'sold')

    assert col.inventory(conn)[0]['owned_count'] == 10
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 20


def test_wishlist_models_are_wanted_not_owned(conn, sheets, stages):
    """Wishlist is the one stage with is_owned = 0. Merging it into the owned
    count would report models Clay does not have as models on the shelf."""
    col.create_unit(conn, sheets['Boyz'], 10, stage_id=stages['On sprue'])
    col.create_unit(conn, sheets['Boyz'], 3, stage_id=stages['Wishlist'])

    row = col.inventory(conn)[0]

    assert row['owned_count'] == 10
    assert row['wanted_count'] == 3


def test_sealed_boxes_are_counted_separately_from_stages(conn, sheets, stages):
    """box_state is not a model stage. Both boxes hold models on sprue; only
    one carries a resale premium."""
    sealed = col.create_kit(conn, 'Sealed', box_state='sealed')
    opened = col.create_kit(conn, 'Opened', box_state='opened')
    for kit in (sealed, opened):
        col.create_unit(conn, sheets['Boyz'], 10, kit_id=kit,
                        stage_id=stages['On sprue'])

    row = col.inventory(conn)[0]

    assert row['owned_count'] == 20
    assert row['sealed_boxes'] == 1


def test_the_bare_inventory_lists_only_what_is_in_the_collection(conn, sheets,
                                                                stages):
    """Without a query this must not become a 2,895-row catalogue dump."""
    col.create_unit(conn, sheets['Boyz'], 10, stage_id=stages['On sprue'])

    assert [r['name'] for r in col.inventory(conn)] == ['Boyz']


# ── The own-it check ─────────────────────────────────────

def test_searching_answers_for_something_owned_none_of(conn, sheets, stages):
    """The whole point of §2.1. A screen that can only list what you own
    cannot tell you that you own none."""
    col.create_unit(conn, sheets['Boyz'], 10, stage_id=stages['On sprue'])

    rows = col.inventory(conn, query='Gretchin', include_unowned=True)

    assert len(rows) == 1
    assert rows[0]['name'] == 'Gretchin'
    assert rows[0]['owns_any'] is False
    assert rows[0]['owned_count'] == 0


def test_owned_results_sort_above_unowned(conn, sheets, stages):
    col.create_unit(conn, sheets['Nobz'], 5, stage_id=stages['On sprue'])

    rows = col.inventory(conn, query='z', include_unowned=True)

    assert [r['name'] for r in rows] == ['Nobz', 'Boyz']
    assert rows[0]['owns_any'] and not rows[1]['owns_any']


def test_owned_summary_answers_for_a_datasheet_owned_none_of(conn, sheets):
    """inventory() walks from datasheets, but a caller asking about one
    datasheet should not have to know that."""
    summary = col.owned_summary(conn, sheets['Gretchin'])

    assert summary['owns_any'] is False
    assert summary['owned_count'] == 0
    assert summary['name'] == 'Gretchin'


def test_owned_summary_carries_the_breakdown(conn, sheets, stages):
    sealed = col.create_kit(conn, 'Sealed', box_state='sealed')
    col.create_unit(conn, sheets['Boyz'], 10, kit_id=sealed,
                    stage_id=stages['On sprue'])
    col.create_unit(conn, sheets['Boyz'], 10, stage_id=stages['Painted'])

    summary = col.owned_summary(conn, sheets['Boyz'])

    assert summary['owns_any'] is True
    assert (summary['owned_count'], summary['built_count']) == (20, 10)
    assert summary['sealed_boxes'] == 1


def test_a_missing_datasheet_has_no_summary(conn):
    assert col.owned_summary(conn, 999) is None


def test_deprecated_40k_printings_stay_out_of_the_inventory(conn, sheets, stages):
    """Clay does not own a [Legends] Vyper, he owns a Vyper."""
    legends = conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, variant, '
        "created_at, updated_at) VALUES ('boyzL', 'Boyz', ?, 1, 'legends', ?, ?)",
        (sheets['_faction'], db.now(), db.now())).lastrowid
    col.create_unit(conn, legends, 10, stage_id=stages['On sprue'])
    col.create_unit(conn, sheets['Boyz'], 10, stage_id=stages['On sprue'])

    rows = col.inventory(conn, query='Boyz', include_unowned=True)

    assert len(rows) == 1
    assert rows[0]['variant'] is None
