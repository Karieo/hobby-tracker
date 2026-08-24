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


# ── The export endpoint ──────────────────────────────────────────────────────
#
# Spec: `GET /api/export/inventory`. A sibling of `inventory()` rather than an
# edit to it, because the collection screen depends on that function's shape
# and an external list optimiser needs three things it does not have — the
# `bsdata_id` join key, army grouping, and points.
#
# The assertion that matters most here is the reconciliation one. If
# `sum(by_stage) != owned + wishlist` something is being double-counted through
# the flexible or capability joins, and the optimiser would silently produce
# lists Clay cannot field.

import datetime  # noqa: E402
import hashlib   # noqa: E402
import json      # noqa: E402


@pytest.fixture
def knights(conn):
    """Armigers, because they are the case capability exists for: one sprue
    builds a Helverin or a Warglaive, and a magnetised one is either."""
    faction = db.upsert_faction(conn, 'Imperial Knights', 'imperial-knights')
    made = {'faction': faction}
    for bsid, name in (('helverin', 'Armiger Helverin'),
                       ('warglaive', 'Armiger Warglaive')):
        made[name] = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'min_models, max_models, created_at, updated_at) '
            'VALUES (?, ?, ?, 4, 1, 3, ?, ?)',
            (bsid, name, faction, db.now(), db.now())).lastrowid
    return made


def armiger_box(conn, knights, stages, count, stage='On sprue',
                army_id=None, committed_to=None, flexible=False):
    kit_id = col.create_kit(conn, 'Armiger box')
    for name in ('Armiger Helverin', 'Armiger Warglaive'):
        conn.execute('INSERT OR IGNORE INTO kit_datasheets (kit_id, '
                     'datasheet_id) VALUES (?, ?)', (kit_id, knights[name]))
    added = col.add_or_extend_unit(
        conn, committed_to or knights['Armiger Helverin'], count,
        army_id=army_id, kit_id=kit_id, stage_id=stages[stage])
    marks = ','.join('?' * len(added['model_ids']))
    conn.execute(f'UPDATE models SET datasheet_id = ?, is_flexible = ? '
                 f'WHERE id IN ({marks})',
                 (committed_to, 1 if flexible else 0, *added['model_ids']))
    return kit_id


def rows_by_name(export):
    return {d['name']: d for d in export['datasheets']}


def test_the_export_has_the_shape_the_optimiser_expects(conn, sheets, stages):
    army = col.create_army(conn, 'Da Boyz')
    col.add_or_extend_unit(conn, sheets['Boyz'], 10, army_id=army,
                           stage_id=stages['Battle ready'])
    export = col.export_inventory(conn, army_id=army)

    assert set(export) == {'generated_at', 'army', 'stages', 'datasheets'}
    datetime.datetime.fromisoformat(export['generated_at'])
    assert export['army']['name'] == 'Da Boyz'
    assert export['stages'][0]['name'] == 'Wishlist'
    assert export['stages'][0]['is_owned'] is False
    row = rows_by_name(export)['Boyz']
    for key in ('bsdata_id', 'name', 'faction', 'min_models', 'max_models',
                'effort', 'owned', 'battle_ready', 'assembled', 'wishlist',
                'by_stage', 'flexible', 'buildable_from_spare', 'points'):
        assert key in row, key
    assert row['bsdata_id'] == 'boyz', 'the join key, not the local integer id'


def test_the_army_filter_keeps_another_army_out(conn, sheets, stages):
    """"Ork inventory must not leak into a Knights list.\""""
    speed = col.create_army(conn, 'Speed Freeks')
    goffs = col.create_army(conn, 'Goffs')
    col.add_or_extend_unit(conn, sheets['Boyz'], 10, army_id=speed,
                           stage_id=stages['Battle ready'])
    col.add_or_extend_unit(conn, sheets['Nobz'], 5, army_id=goffs,
                           stage_id=stages['Battle ready'])

    assert set(rows_by_name(col.export_inventory(conn, army_id=speed))) == {'Boyz'}
    assert set(rows_by_name(col.export_inventory(conn, army_id=goffs))) == {'Nobz'}


def test_unassigned_models_are_off_by_default_and_reachable(conn, sheets, stages):
    """"A sealed box not yet committed to an army is real plastic." Off by
    default so an army query is clean, but "what could I field if I committed
    the unassigned stuff" is a question worth being able to ask."""
    speed = col.create_army(conn, 'Speed Freeks')
    col.add_or_extend_unit(conn, sheets['Boyz'], 10, army_id=speed,
                           stage_id=stages['Battle ready'])
    col.add_or_extend_unit(conn, sheets['Nobz'], 5, stage_id=stages['On sprue'])

    assert set(rows_by_name(col.export_inventory(conn, army_id=speed))) == {'Boyz'}
    both = col.export_inventory(conn, army_id=speed, include_unassigned=True)
    assert set(rows_by_name(both)) == {'Boyz', 'Nobz'}


def test_a_disposed_kit_is_not_in_the_export(conn, sheets, stages):
    """The invariant: a sold box keeps its rows and stops counting."""
    kit_id = col.create_kit(conn, 'Boyz box')
    col.add_or_extend_unit(conn, sheets['Boyz'], 10, kit_id=kit_id,
                           stage_id=stages['Battle ready'])
    assert rows_by_name(col.export_inventory(conn,
                                             include_unassigned=True))['Boyz']['owned'] == 10
    col.dispose_kit(conn, kit_id, 'sold', price_cents=2500)
    assert col.export_inventory(conn, include_unassigned=True)['datasheets'] == []


def test_wishlist_models_never_count_as_owned(conn, sheets, stages):
    """"A model Clay wants is not a model he has.\""""
    col.add_or_extend_unit(conn, sheets['Boyz'], 10,
                           stage_id=stages['Battle ready'])
    col.add_or_extend_unit(conn, sheets['Boyz'], 7, stage_id=stages['Wishlist'])

    row = rows_by_name(col.export_inventory(conn,
                                            include_unassigned=True))['Boyz']
    assert row['owned'] == 10 and row['wishlist'] == 7
    assert row['by_stage']['Wishlist'] == 7


def test_assembled_is_everything_past_the_sprue(conn, sheets, stages):
    """"The gap between assembled and battle_ready is the paint queue, and it's
    the whole reason both are exposed.\""""
    col.add_or_extend_unit(conn, sheets['Boyz'], 3, stage_id=stages['On sprue'])
    col.add_or_extend_unit(conn, sheets['Nobz'], 2, stage_id=stages['Primed'])
    col.add_or_extend_unit(conn, sheets['Gretchin'], 1,
                           stage_id=stages['Battle ready'])

    rows = rows_by_name(col.export_inventory(conn, include_unassigned=True))
    assert rows['Boyz']['assembled'] == 0
    assert rows['Nobz']['assembled'] == 2 and rows['Nobz']['battle_ready'] == 0
    assert rows['Gretchin']['assembled'] == 1
    assert rows['Gretchin']['battle_ready'] == 1


def test_the_counts_reconcile_for_every_row(conn, sheets, knights, stages):
    """THE ASSERTION THAT CATCHES A DOUBLE COUNT.

    "If it ever fails, something is being double-counted through the flexible
    or capability joins, and the optimizer would silently produce lists Clay
    can't field."
    """
    col.add_or_extend_unit(conn, sheets['Boyz'], 10, stage_id=stages['Primed'])
    col.add_or_extend_unit(conn, sheets['Boyz'], 4, stage_id=stages['Wishlist'])
    col.add_or_extend_unit(conn, sheets['Nobz'], 5,
                           stage_id=stages['Battle ready'])
    armiger_box(conn, knights, stages, 2)
    armiger_box(conn, knights, stages, 1, stage='Battle ready',
                committed_to=knights['Armiger Warglaive'], flexible=True)

    export = col.export_inventory(conn, include_unassigned=True)
    assert export['datasheets'], 'nothing to reconcile is not a pass'
    for row in export['datasheets']:
        assert sum(row['by_stage'].values()) == row['owned'] + row['wishlist'], \
            f"{row['name']} does not reconcile: {row}"


def test_a_magnetised_model_shows_against_every_datasheet_it_could_be(
        conn, knights, stages):
    """One magnetised Armiger built as a Warglaive is a Helverin Clay can field
    in two minutes. It appears against both, and the consumer deduplicates by
    model — the spec is explicit that silently picking one is the wrong fix."""
    armiger_box(conn, knights, stages, 1, stage='Battle ready',
                committed_to=knights['Armiger Warglaive'], flexible=True)
    rows = rows_by_name(col.export_inventory(conn, include_unassigned=True))
    assert rows['Armiger Warglaive']['flexible'] >= 1
    assert rows['Armiger Helverin']['flexible'] >= 1
    assert rows['Armiger Warglaive']['owned'] == 1
    assert rows['Armiger Helverin']['owned'] == 0, 'it is not one right now'


def test_spare_plastic_is_reported_apart_from_ownership(conn, knights, stages):
    """"This is what makes 'you already have the plastic' possible." Distinct
    from owned, because it is not one yet."""
    armiger_box(conn, knights, stages, 3)
    rows = rows_by_name(col.export_inventory(conn, include_unassigned=True))
    assert rows['Armiger Helverin']['owned'] == 0
    assert rows['Armiger Helverin']['buildable_from_spare'] == 3
    assert rows['Armiger Warglaive']['buildable_from_spare'] == 3


def test_capability_can_be_turned_off(conn, knights, stages):
    armiger_box(conn, knights, stages, 3)
    export = col.export_inventory(conn, include_unassigned=True,
                                  include_capability=False)
    for row in export['datasheets']:
        assert 'buildable_from_spare' not in row


def test_a_datasheet_with_nothing_at_all_is_omitted(conn, sheets, stages):
    """"This is an inventory, not a catalogue dump.\""""
    col.add_or_extend_unit(conn, sheets['Boyz'], 1, stage_id=stages['On sprue'])
    names = set(rows_by_name(col.export_inventory(conn, include_unassigned=True)))
    assert names == {'Boyz'}
    assert 'Gretchin' not in names


def test_every_tier_is_emitted_uncollapsed(conn, sheets, stages):
    """"Requisition Thresholds are exactly the thing a list optimizer has to
    reason about, and flattening here would hide the third-copy surcharge.\""""
    col.add_or_extend_unit(conn, sheets['Boyz'], 10, stage_id=stages['On sprue'])
    for model_count, points, lo, hi in ((10, 90, 1, 2), (10, 100, 3, None),
                                        (20, 180, 1, 2)):
        conn.execute('INSERT INTO datasheet_points (datasheet_id, model_count, '
                     'points, tier_min, tier_max, effective_from) '
                     'VALUES (?, ?, ?, ?, ?, ?)',
                     (sheets['Boyz'], model_count, points, lo, hi, '2026-01-01'))

    points = rows_by_name(col.export_inventory(
        conn, include_unassigned=True))['Boyz']['points']
    assert len(points) == 3
    assert {(p['model_count'], p['points'], p['tier_min'], p['tier_max'])
            for p in points} == {(10, 90, 1, 2), (10, 100, 3, None),
                                 (20, 180, 1, 2)}


def test_a_faction_priced_datasheet_follows_the_army(conn, sheets, stages):
    """One Repulsor Executioner datasheet costs a Black Templar 255 and a Blood
    Angel 230. With an army naming a faction, only that price goes out."""
    orks = conn.execute("SELECT id FROM factions WHERE slug = 'orks'").fetchone()[0]
    other = db.upsert_faction(conn, 'Blood Angels', 'blood-angels')
    army = col.create_army(conn, 'Da Boyz', primary_faction_id=orks)
    col.add_or_extend_unit(conn, sheets['Boyz'], 10, army_id=army,
                           stage_id=stages['On sprue'])
    for faction_id, points in ((orks, 90), (other, 120), (None, 80)):
        conn.execute('INSERT INTO datasheet_points (datasheet_id, faction_id, '
                     'model_count, points, tier_min, effective_from) '
                     'VALUES (?, ?, 10, ?, 1, ?)',
                     (sheets['Boyz'], faction_id, points, '2026-01-01'))

    scoped = rows_by_name(col.export_inventory(conn, army_id=army))['Boyz']
    assert sorted(p['points'] for p in scoped['points']) == [80, 90], \
        'the other faction\'s price is not this army\'s'

    everything = rows_by_name(col.export_inventory(
        conn, include_unassigned=True))['Boyz']
    labelled = {p.get('faction') for p in everything['points']}
    assert 'Blood Angels' in labelled and 'Orks' in labelled, \
        'with no army to scope by, every price goes out labelled'


# ── Narrowing it down ────────────────────────────────────
#
# Clay: "Need to be able to filter by faction, points, name and anything else
# you can think of." Name and faction were half-built — the route already read
# faction_id and passed it to the template, and the template never rendered a
# control for it, so the only reachable filters were name and game system.

@pytest.fixture
def priced(conn, sheets):
    """Boyz at two unit sizes, Nobz at one, and a rival faction's cheap hero."""
    for datasheet, size, points in (('Boyz', 10, 90), ('Boyz', 20, 180),
                                    ('Nobz', 5, 75)):
        conn.execute(
            'INSERT INTO datasheet_points (datasheet_id, model_count, points, '
            "effective_from, source_note) VALUES (?, ?, ?, '2026-01-01', 'MFM')",
            (sheets[datasheet], size, points))
    militarum = db.upsert_faction(conn, 'Astra Militarum', 'astra-militarum')
    nork = conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
        'created_at, updated_at) VALUES (?,?,?,1,?,?)',
        ('nork', 'Nork Deddog', militarum, db.now(), db.now())).lastrowid
    conn.execute(
        'INSERT INTO datasheet_points (datasheet_id, model_count, points, '
        "effective_from, source_note) VALUES (?, 1, 65, '2026-01-01', 'MFM')",
        (nork,))
    return {'militarum': militarum, 'nork': nork}


def names(rows):
    return [r['name'] for r in rows]


def test_faction_narrows_a_name_that_matches_two(conn, sheets, priced, stages):
    """Searching "Ork" returned Nork Deddog — an Astra Militarum hero whose
    name happens to contain the substring. This is the screenshot Clay sent."""
    everything = col.inventory(conn, query='ork', include_unowned=True)
    assert 'Nork Deddog' in names(everything)

    orks_only = col.inventory(conn, query='ork', include_unowned=True,
                              faction_id=sheets['_faction'])
    assert 'Nork Deddog' not in names(orks_only)


def test_a_datasheet_reports_the_range_of_its_prices(conn, sheets, priced, stages):
    """Ten Boyz and twenty Boyz are 90 and 180 — one datasheet, two prices, and
    a single number would have to lie about one of them."""
    col.create_unit(conn, sheets['Boyz'], 10)
    row = next(r for r in col.inventory(conn) if r['name'] == 'Boyz')
    assert (row['points_low'], row['points_high']) == (90, 180)


def test_points_are_scoped_to_the_datasheets_own_faction(conn, sheets, priced):
    """One Repulsor Executioner costs a Black Templar 255 and a Blood Angel
    230. An unscoped MIN answers with whichever faction sorted first."""
    other = db.upsert_faction(conn, 'Blood Angels', 'blood-angels')
    conn.execute(
        'INSERT INTO datasheet_points (datasheet_id, faction_id, model_count, '
        "points, effective_from, source_note) VALUES (?, ?, 10, 5, '2026-01-01', 'MFM')",
        (sheets['Boyz'], other))

    row = next(r for r in col.inventory(conn, include_unowned=True)
               if r['name'] == 'Boyz')

    assert row['points_low'] == 90, "another faction's price is not Clay's"


def test_a_points_range_matches_any_of_a_datasheets_prices(conn, sheets, priced):
    """"Under 100" finds Boyz on the ten-model price without pretending the
    twenty-model one is cheap."""
    cheap = col.inventory(conn, include_unowned=True, points_max=100)
    assert 'Boyz' in names(cheap) and 'Nobz' in names(cheap)

    dear = col.inventory(conn, include_unowned=True, points_min=150)
    assert names(dear) == ['Boyz'], 'only the twenty-model price is that high'


def test_unpriced_datasheets_drop_out_of_a_points_filter(conn, sheets, priced):
    """Gretchin has no points row. Asking a question about points must not
    answer with rows that have none — which SQLite gives for free, since a
    comparison against NULL is not true. Pinned because it is behaviour the
    filter leans on rather than states."""
    assert 'Gretchin' not in names(
        col.inventory(conn, include_unowned=True, points_max=1000))


def test_filtering_by_the_stage_models_are_at(conn, sheets, stages, priced):
    """"What is still on sprue" is the question this app exists for."""
    boyz = col.create_unit(conn, sheets['Boyz'], 10)
    col.advance_unit(conn, boyz, count=4)             # 4 Assembled, 6 On sprue
    col.create_unit(conn, sheets['Nobz'], 5)          # all On sprue

    assert set(names(col.inventory(conn, stage_id=stages['On sprue']))) == \
        {'Boyz', 'Nobz'}
    assert names(col.inventory(conn, stage_id=stages['Assembled'])) == ['Boyz']
    assert names(col.inventory(conn, stage_id=stages['Painted'])) == []


def test_the_wishlist_is_its_own_view(conn, sheets, stages, priced):
    col.create_unit(conn, sheets['Boyz'], 10)
    col.create_unit(conn, sheets['Nobz'], 5, stage_id=stages['Wishlist'])

    assert names(col.inventory(conn, only_wanted=True)) == ['Nobz']


def test_sorting_by_price(conn, sheets, priced):
    cheapest = col.inventory(conn, include_unowned=True, sort='points')
    assert names(cheapest)[:3] == ['Nork Deddog', 'Nobz', 'Boyz']

    priciest = col.inventory(conn, include_unowned=True, sort='expensive')
    assert names(priciest)[0] == 'Boyz', 'twenty Boyz is the dearest thing here'


def test_sorting_by_what_is_least_finished(conn, sheets, stages, priced):
    """"What should I paint next" — the most models with the least done."""
    col.create_unit(conn, sheets['Nobz'], 3)
    boyz = col.create_unit(conn, sheets['Boyz'], 10)
    for _ in range(10):
        col.advance_unit(conn, boyz)                  # all the way to done

    assert names(col.inventory(conn, sort='unfinished'))[0] == 'Nobz'


def test_an_unknown_sort_falls_back_rather_than_reaching_sql(conn, sheets, priced):
    """The value arrives from a query string. INVENTORY_SORTS is a dict of
    known keys for exactly this reason — nothing from the URL is interpolated
    into the statement."""
    col.create_unit(conn, sheets['Boyz'], 10)

    assert col.inventory(conn, sort='; DROP TABLE models;--') == \
        col.inventory(conn, sort='name')
    assert conn.execute('SELECT COUNT(*) AS n FROM models').fetchone()['n'] == 10


def test_filters_combine(conn, sheets, stages, priced):
    """Each one narrows the last. Any that quietly replaced another would make
    the screen lie about what it is showing."""
    boyz = col.create_unit(conn, sheets['Boyz'], 10)
    col.advance_unit(conn, boyz, count=10)
    col.create_unit(conn, sheets['Nobz'], 5)

    rows = col.inventory(conn, query='o', faction_id=sheets['_faction'],
                         stage_id=stages['Assembled'], points_max=100)

    assert names(rows) == ['Boyz']
