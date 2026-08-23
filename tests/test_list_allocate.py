"""Which physical models cover which entries, and what is genuinely missing.

Section 7's allocation. Every test here is one of the spec's twelve numbered
cases, and the case number is in the name so a failure says which rule broke.

The whole design exists for case 1. Counting ownership per entry — which is
what `lists.list_gap` does today — matches all three Boyz entries against the
same twenty models and reports no shortfall, and Clay discovers it the night
before a game. Models have to be *consumed* as they are assigned.
"""

import pytest

import collection as col
import database as db
import list_allocate
import lists


@pytest.fixture
def stages(conn):
    return {s['name']: s['id'] for s in col.stage_ladder(conn)}


@pytest.fixture
def world(conn):
    """Orks for the counting cases, Knights for the flexible ones.

    Armigers are the spec's own example: one sprue builds a Helverin or a
    Warglaive, and a magnetised one is whichever it is built as right now while
    still being able to be the other in two minutes.
    """
    orks = db.upsert_faction(conn, 'Orks', 'orks')
    knights = db.upsert_faction(conn, 'Imperial Knights', 'imperial-knights')
    made = {'orks': orks, 'knights': knights}
    rows = (('boyz', 'Boyz', orks, 10), ('kans', 'Killa Kans', orks, 1),
            ('warglaive', 'Armiger Warglaive', knights, 1),
            ('helverin', 'Armiger Helverin', knights, 1),
            ('moirax', 'Armiger Moirax', knights, 1))
    for bsid, name, faction, minimum in rows:
        made[bsid] = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'min_models, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?)',
            (bsid, name, faction, minimum, db.now(), db.now())).lastrowid
    return made


def own(conn, stages, datasheet_id, count, stage='Battle ready', army_id=None,
        kit_id=None):
    """Models Clay has, committed to one datasheet."""
    added = col.add_or_extend_unit(conn, datasheet_id, count,
                                   army_id=army_id, kit_id=kit_id,
                                   stage_id=stages[stage])
    return added


def sprues(conn, stages, kit_name, buildable, count, stage='On sprue',
           flexible=False, built_as=None):
    """A box that can become any of `buildable`, holding `count` models.

    Uncommitted by default — plastic in a box, not yet anything. `flexible`
    makes them magnetised: built as `built_as` right now, swappable in seconds.
    """
    kit_id = col.create_kit(conn, kit_name)
    for datasheet_id in buildable:
        conn.execute('INSERT OR IGNORE INTO kit_datasheets (kit_id, '
                     'datasheet_id) VALUES (?, ?)', (kit_id, datasheet_id))
    added = col.add_or_extend_unit(conn, built_as or buildable[0], count,
                                   kit_id=kit_id, stage_id=stages[stage])
    conn.execute('UPDATE models SET datasheet_id = ?, is_flexible = ? '
                 'WHERE id IN ({})'.format(
                     ','.join('?' * len(added['model_ids']))),
                 (built_as, 1 if flexible else 0, *added['model_ids']))
    return kit_id


def by_name(report):
    """Entries keyed by datasheet, for cases where each name appears once."""
    return {e['datasheet_name']: e for e in report['entries']}


# ── Case 1 · the double count ────────────────────────────────────────────────

def test_case_1_three_entries_do_not_share_the_same_twenty_models(
        conn, world, stages):
    """SPEC CASE 1. "A list with two 10-model Boyz units and one 20-model Boyz
    unit needs 40 Boyz. Counting per-entry against your collection will match
    all three entries against the same 20 models you own and report zero
    shortfall. You will discover this the night before a game.\""""
    own(conn, stages, world['boyz'], 20)
    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    lists.add_entry(conn, lid, world['boyz'], 10)
    lists.add_entry(conn, lid, world['boyz'], 10)
    lists.add_entry(conn, lid, world['boyz'], 20)

    report = list_allocate.allocate(conn, lid)
    assert report['short'] == 20, 'forty needed, twenty owned'
    assert sum(e['owned'] for e in report['entries']) == 20, \
        'no model may be counted twice'


def test_case_1_the_largest_requirement_is_served_first(conn, world, stages):
    """"Largest requirement first in pass 1." Twenty owned against 20 + 10 + 10
    fills the twenty-model unit whole rather than leaving three part-filled."""
    own(conn, stages, world['boyz'], 20)
    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    small = lists.add_entry(conn, lid, world['boyz'], 10)
    big = lists.add_entry(conn, lid, world['boyz'], 20)

    entries = {e['id']: e for e in list_allocate.allocate(conn, lid)['entries']}
    assert entries[big]['owned'] == 20 and entries[big]['short'] == 0
    assert entries[small]['owned'] == 0 and entries[small]['short'] == 10


# ── Case 9 · one model cannot be two things ──────────────────────────────────

def test_case_9_a_single_magnetised_model_serves_one_entry_only(
        conn, world, stages):
    """SPEC CASE 9. "List needs 1 Warglaive and 1 Helverin, you own 1
    magnetised Armiger → one row swappable, the other short. One model cannot
    serve both.\""""
    sprues(conn, stages, 'Armiger', [world['warglaive'], world['helverin']],
           1, stage='Battle ready', flexible=True, built_as=world['warglaive'])
    lid = lists.create_list(conn, 'Saturday', faction_id=world['knights'])
    lists.add_entry(conn, lid, world['warglaive'], 1)
    lists.add_entry(conn, lid, world['helverin'], 1)

    rows = by_name(list_allocate.allocate(conn, lid))
    glaive, helverin = rows['Armiger Warglaive'], rows['Armiger Helverin']
    assert glaive['owned'] == 1 and glaive['short'] == 0, \
        'it is built as a Warglaive, so that entry is simply owned'
    assert helverin['short'] == 1, 'the same plastic cannot also be a Helverin'
    assert helverin['swappable'] == 0


def test_case_9_a_magnetised_model_covers_the_other_datasheet_when_free(
        conn, world, stages):
    """The other half of the same rule: it *can* be the Helverin, as long as
    nothing else has already spent it."""
    sprues(conn, stages, 'Armiger', [world['warglaive'], world['helverin']],
           1, stage='Battle ready', flexible=True, built_as=world['warglaive'])
    lid = lists.create_list(conn, 'Saturday', faction_id=world['knights'])
    lists.add_entry(conn, lid, world['helverin'], 1)

    row = by_name(list_allocate.allocate(conn, lid))['Armiger Helverin']
    assert row['short'] == 0 and row['swappable'] == 1


# ── Case 10 · most-constrained first ─────────────────────────────────────────

def test_case_10_the_constrained_shortfall_is_served_first(conn, world, stages):
    """SPEC CASE 10. "Two shortfalls, one with a single eligible candidate and
    one with several, all drawing from the same pool → the constrained one is
    served first and neither reports a false shortfall."

    A Moirax sprue builds only a Moirax. A plain Armiger sprue builds a
    Warglaive or a Helverin. Serve the Warglaive from the plain sprue first and
    the Moirax has nothing left; serve the constrained one first and both are
    covered.
    """
    sprues(conn, stages, 'Armiger Moirax box',
           [world['warglaive'], world['helverin'], world['moirax']], 1)
    sprues(conn, stages, 'Armiger box',
           [world['warglaive'], world['helverin']], 1)
    lid = lists.create_list(conn, 'Saturday', faction_id=world['knights'])
    lists.add_entry(conn, lid, world['moirax'], 1)
    lists.add_entry(conn, lid, world['warglaive'], 1)

    report = list_allocate.allocate(conn, lid)
    rows = by_name(report)
    assert rows['Armiger Moirax']['short'] == 0, \
        'only one box in the collection can ever become a Moirax'
    assert rows['Armiger Warglaive']['short'] == 0
    assert report['short'] == 0, 'neither reports a false shortfall'


# ── Case 2 · armies keep their own models ────────────────────────────────────

def test_case_2_only_the_list_s_army_counts(conn, world, stages):
    """SPEC CASE 2. Ten Boyz in each of two armies is not twenty Boyz for a
    list that belongs to one of them."""
    speed = col.create_army(conn, 'Speed Freeks')
    goffs = col.create_army(conn, 'Goffs')
    own(conn, stages, world['boyz'], 10, army_id=speed)
    own(conn, stages, world['boyz'], 10, army_id=goffs)

    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    conn.execute('UPDATE army_lists SET army_id = ? WHERE id = ?', (speed, lid))
    lists.add_entry(conn, lid, world['boyz'], 20)

    row = by_name(list_allocate.allocate(conn, lid))['Boyz']
    assert row['owned'] == 10 and row['short'] == 10


def test_case_2_unassigned_models_still_help_that_army(conn, world, stages):
    """The toggle's default. A box on a shelf with no army named is still
    plastic Clay owns, and pretending otherwise makes the report useless."""
    speed = col.create_army(conn, 'Speed Freeks')
    own(conn, stages, world['boyz'], 10, army_id=speed)
    own(conn, stages, world['boyz'], 10)

    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    conn.execute('UPDATE army_lists SET army_id = ? WHERE id = ?', (speed, lid))
    lists.add_entry(conn, lid, world['boyz'], 20)

    row = by_name(list_allocate.allocate(conn, lid))['Boyz']
    assert row['short'] == 0


# ── Case 3 · the unassigned toggle ───────────────────────────────────────────

def test_case_3_turning_the_toggle_off_excludes_unassigned_models(
        conn, world, stages):
    """SPEC CASE 3."""
    speed = col.create_army(conn, 'Speed Freeks')
    own(conn, stages, world['boyz'], 10, army_id=speed)
    own(conn, stages, world['boyz'], 10)

    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    conn.execute('UPDATE army_lists SET army_id = ? WHERE id = ?', (speed, lid))
    lists.add_entry(conn, lid, world['boyz'], 20)

    row = by_name(list_allocate.allocate(conn, lid,
                                         include_unassigned=False))['Boyz']
    assert row['owned'] == 10 and row['short'] == 10


def test_the_toggle_still_means_something_on_a_list_with_no_army(
        conn, world, stages):
    """"Only models I have committed to an army" is a sentence with a meaning
    whether or not this list names one, so the control must not silently do
    nothing on the screen it sits on."""
    goffs = col.create_army(conn, 'Goffs')
    own(conn, stages, world['boyz'], 10, army_id=goffs)
    own(conn, stages, world['boyz'], 10)

    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    lists.add_entry(conn, lid, world['boyz'], 20)

    assert by_name(list_allocate.allocate(conn, lid))['Boyz']['short'] == 0
    off = by_name(list_allocate.allocate(conn, lid, include_unassigned=False))
    assert off['Boyz']['short'] == 10


# ── Case 8 · sprues that could become the thing ──────────────────────────────

def test_case_8_uncommitted_sprues_cover_a_shortfall_and_one_is_left_over(
        conn, world, stages):
    """SPEC CASE 8. "List needs 2 Warglaives, you own 3 uncommitted Armiger
    sprues → 0 short, 2 buildable, 1 sprue left over.\""""
    sprues(conn, stages, 'Armiger box',
           [world['warglaive'], world['helverin']], 3)
    lid = lists.create_list(conn, 'Saturday', faction_id=world['knights'])
    lists.add_entry(conn, lid, world['warglaive'], 2)

    report = list_allocate.allocate(conn, lid)
    row = by_name(report)['Armiger Warglaive']
    assert row['short'] == 0
    assert row['buildable'] == 2
    assert row['state'] == 'buildable', 'it needs an evening, not a shop'

    # "1 sprue left over" — asserted the only way that means anything, by
    # asking the leftover to do something. A third sprue is still unspent, so
    # adding a Helverin to the same list has to be covered too.
    lists.add_entry(conn, lid, world['helverin'], 1)
    after = by_name(list_allocate.allocate(conn, lid))
    assert after['Armiger Warglaive']['buildable'] == 2
    assert after['Armiger Helverin']['short'] == 0, 'the spare sprue covers it'


def test_a_sprue_is_buildable_not_owned(conn, world, stages):
    """Owned counts models that already *are* the datasheet. A sprue is not one
    yet, and folding it into `owned` would make "owned" mean two things."""
    sprues(conn, stages, 'Armiger box',
           [world['warglaive'], world['helverin']], 2)
    lid = lists.create_list(conn, 'Saturday', faction_id=world['knights'])
    lists.add_entry(conn, lid, world['warglaive'], 2)

    row = by_name(list_allocate.allocate(conn, lid))['Armiger Warglaive']
    assert row['owned'] == 0 and row['buildable'] == 2


# ── Case 11 · a magnetised model is ready for either thing ───────────────────

def test_case_11_a_magnetised_model_at_battle_ready_needs_no_hobby_time(
        conn, world, stages):
    """SPEC CASE 11. "Magnetized model at battle ready → counts toward
    battle-ready points for the datasheet it can swap to, not just the one it's
    built as." A swap is two minutes with a pair of arms, not an evening."""
    sprues(conn, stages, 'Armiger', [world['warglaive'], world['helverin']],
           1, stage='Battle ready', flexible=True, built_as=world['warglaive'])
    lid = lists.create_list(conn, 'Saturday', faction_id=world['knights'])
    lists.add_entry(conn, lid, world['helverin'], 1)

    report = list_allocate.allocate(conn, lid)
    row = by_name(report)['Armiger Helverin']
    assert row['swappable'] == 1 and row['state'] == 'swappable'
    assert report['points_ready'] == report['points_owned'], \
        'a swap costs no hobby time, so it counts as ready'


def test_a_magnetised_model_that_is_not_finished_is_only_buildable(
        conn, world, stages):
    """Flexible is about what it can become, not about how far along it is."""
    sprues(conn, stages, 'Armiger', [world['warglaive'], world['helverin']],
           1, stage='Primed', flexible=True, built_as=world['warglaive'])
    lid = lists.create_list(conn, 'Saturday', faction_id=world['knights'])
    lists.add_entry(conn, lid, world['helverin'], 1)

    report = list_allocate.allocate(conn, lid)
    row = by_name(report)['Armiger Helverin']
    assert row['buildable'] == 1 and row['swappable'] == 0
    assert report['points_ready'] == 0


def test_magnetised_and_ready_is_spent_before_a_bare_sprue(conn, world, stages):
    """"Prefer magnetized battle-ready models over uncommitted ones, since
    those need no work at all.\""""
    sprues(conn, stages, 'Armiger box',
           [world['warglaive'], world['helverin']], 1)
    sprues(conn, stages, 'Magnetised Armiger',
           [world['warglaive'], world['helverin']], 1, stage='Battle ready',
           flexible=True, built_as=world['warglaive'])
    lid = lists.create_list(conn, 'Saturday', faction_id=world['knights'])
    lists.add_entry(conn, lid, world['helverin'], 1)

    row = by_name(list_allocate.allocate(conn, lid))['Armiger Helverin']
    assert row['swappable'] == 1 and row['buildable'] == 0


# ── The summary line ─────────────────────────────────────────────────────────

def test_a_part_owned_unit_contributes_no_points(conn, world, stages):
    """"A 7-of-10 Boyz mob is not 70% of a Boyz mob on the table.\""""
    own(conn, stages, world['boyz'], 7)
    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    lists.add_entry(conn, lid, world['boyz'], 10)
    conn.execute('UPDATE list_entries SET points_snapshot = 180 '
                 'WHERE list_id = ?', (lid,))

    report = list_allocate.allocate(conn, lid)
    assert report['points_total'] == 180
    assert report['points_owned'] == 0 and report['points_ready'] == 0


def test_buildable_models_count_as_owned_but_not_as_ready(conn, world, stages):
    sprues(conn, stages, 'Armiger box',
           [world['warglaive'], world['helverin']], 1)
    lid = lists.create_list(conn, 'Saturday', faction_id=world['knights'])
    lists.add_entry(conn, lid, world['warglaive'], 1)
    conn.execute('UPDATE list_entries SET points_snapshot = 150 '
                 'WHERE list_id = ?', (lid,))

    report = list_allocate.allocate(conn, lid)
    assert report['points_owned'] == 150
    assert report['points_ready'] == 0, 'it is still a sprue'


def test_an_unresolved_entry_deflates_nothing(conn, world, stages):
    """"Unresolved entries are excluded from all totals and the summary says so
    explicitly. Never let an unresolved row quietly deflate the numbers.\""""
    own(conn, stages, world['boyz'], 10)
    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    lists.add_entry(conn, lid, world['boyz'], 10)
    conn.execute('UPDATE list_entries SET points_snapshot = 180 '
                 'WHERE list_id = ?', (lid,))
    conn.execute('INSERT INTO list_entries (list_id, position, raw_name, '
                 'model_count) VALUES (?, 9, ?, 1)', (lid, 'Warboss on Warbike'))

    report = list_allocate.allocate(conn, lid)
    assert report['unresolved'] == 1
    assert report['points_total'] == 180, 'the unknown row is not priced at 0'
    assert report['short'] == 0, 'and it is not counted as a shortfall either'
    assert report['fieldable'] is True


def test_the_report_prices_from_munitorum_not_from_the_paste(conn, world, stages):
    """§2.7's rule, confirmed by Clay for this report: a number copied out of
    someone else's app never outranks the official one."""
    own(conn, stages, world['boyz'], 10)
    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    lists.add_entry(conn, lid, world['boyz'], 10, raw_name='10x Boyz', points=999)
    conn.execute('UPDATE list_entries SET points_snapshot = 180 '
                 'WHERE list_id = ?', (lid,))

    report = list_allocate.allocate(conn, lid)
    assert report['points_total'] == 180
    assert report['entries'][0]['points'] == 999, 'still recorded, never totalled'


# ── Row states ───────────────────────────────────────────────────────────────

def test_the_six_row_states(conn, world, stages):
    """Section 7's table. The worst state wins, because a row is described by
    what Clay would have to do about it."""
    own(conn, stages, world['boyz'], 10)
    own(conn, stages, world['kans'], 1, stage='Primed')
    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    lists.add_entry(conn, lid, world['boyz'], 10)
    lists.add_entry(conn, lid, world['kans'], 1)
    lists.add_entry(conn, lid, world['warglaive'], 1)
    conn.execute('INSERT INTO list_entries (list_id, position, raw_name, '
                 'model_count) VALUES (?, 9, ?, 1)', (lid, 'Wossname'))

    states = {e['datasheet_name'] or e['raw_name']: e['state']
              for e in list_allocate.allocate(conn, lid)['entries']}
    assert states['Boyz'] == 'ready'
    assert states['Killa Kans'] == 'owned'
    assert states['Armiger Warglaive'] == 'short'
    assert states['Wossname'] == 'unresolved'


# ── The collection moves under it ────────────────────────────────────────────

def test_the_report_is_recomputed_not_stored(conn, world, stages):
    """"Paint three Meganobz, reload the list, the numbers move. That feedback
    loop is the feature.\""""
    unit = own(conn, stages, world['boyz'], 10, stage='Primed')
    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    lists.add_entry(conn, lid, world['boyz'], 10)

    assert by_name(list_allocate.allocate(conn, lid))['Boyz']['battle_ready'] == 0
    while col.advance_unit(conn, unit['unit_id']):
        pass
    assert by_name(list_allocate.allocate(conn, lid))['Boyz']['battle_ready'] == 10


def test_a_sold_box_covers_nothing(conn, world, stages):
    """Disposals are status changes rather than deletions, so the rows are
    still there — and a list that counted them would be counting plastic Clay
    posted to a stranger."""
    kit_id = col.create_kit(conn, 'Boyz box')
    own(conn, stages, world['boyz'], 10, kit_id=kit_id)
    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    lists.add_entry(conn, lid, world['boyz'], 10)

    assert by_name(list_allocate.allocate(conn, lid))['Boyz']['short'] == 0
    col.dispose_kit(conn, kit_id, 'sold', price_cents=2500)
    assert by_name(list_allocate.allocate(conn, lid))['Boyz']['short'] == 10


def test_an_empty_list_is_fieldable_rather_than_an_error(conn, world, stages):
    lid = lists.create_list(conn, 'Saturday', faction_id=world['orks'])
    report = list_allocate.allocate(conn, lid)
    assert report['entries'] == [] and report['short'] == 0
    assert report['fieldable'] is True


# ── The rest of the twelve ───────────────────────────────────────────────────
#
# Cases 4, 5, 6, 7 and 12 are about reading and resolving rather than
# allocating, and they are covered where that code lives:
#
#   4  a character with no count is one model   test_list_parse.py
#   5  an unparseable line is never dropped     test_list_parse.py
#   6  a manual resolution teaches the alias    test_list_resolve.py
#   7  empty / wargear-only / header-only       test_list_parse.py
#   12 is_flexible survives a stage advance     test_gap_schema.py
