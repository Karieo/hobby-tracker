"""What to part with — and, far more importantly, what not to.

Spec §8. Every other screen in this app is wrong for an afternoon if it gets a
number wrong. This one recommends selling plastic, so being wrong costs Clay
models he cannot buy back at the price he paid. Nearly every test in here is
really asking the same question: **can this screen ever propose something a
game needs?**
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collection as col
import database as db
import kit_templates as kt
import lists as army_lists
import sale


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / 'sale.db')
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
    made = {}
    for bsid, name, effort in (('boyz', 'Boyz', 1), ('nobz', 'Nobz', 2),
                               ('trukk', 'Trukk', 4), ('gork', 'Gorkanaut', 8)):
        made[name] = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'created_at, updated_at) VALUES (?,?,?,?,?,?)',
            (bsid, name, orks, effort, db.now(), db.now())).lastrowid
    return made


def own(conn, stages, datasheet_id, count, stage='On sprue'):
    return col.create_unit(conn, datasheet_id, count, stage_id=stages[stage])


def wants(conn, name, sheets, **counts):
    list_id = army_lists.create_list(conn, name)
    for unit, n in counts.items():
        army_lists.add_entry(conn, list_id, sheets[unit], n)
    return list_id


def row_for(result, name):
    for row in result['surplus']:
        if row['name'] == name:
            return row
    return None


# ── The one thing it must never do ───────────────────────

def test_a_datasheet_a_list_needs_is_never_proposed(conn, stages, sheets):
    """The whole point of the screen. Owning exactly what Saturday asks for is
    not surplus, it is your army."""
    own(conn, stages, sheets['Boyz'], 20)
    wants(conn, 'Saturday', sheets, Boyz=20)

    assert row_for(sale.candidates(conn), 'Boyz') is None


def test_only_the_amount_over_what_a_list_needs_is_proposed(conn, stages, sheets):
    own(conn, stages, sheets['Boyz'], 30)
    wants(conn, 'Saturday', sheets, Boyz=20)

    row = row_for(sale.candidates(conn), 'Boyz')

    assert row['owned'] == 30 and row['needed'] == 20
    assert row['surplus'] == 10


def test_needed_is_the_biggest_list_not_the_sum_of_them(conn, stages, sheets):
    """The same rule the wishlist deduplicates on, and for the same reason
    `list_allocate` gives every list the whole collection: models are not
    allocated between lists. Three lists wanting twenty Boyz need twenty — you
    play one game at a time.

    Summing is the dangerous direction: it inflates what looks needed, hides
    real surplus, and makes the screen recommend nothing at all — a quiet
    failure nobody notices.
    """
    own(conn, stages, sheets['Boyz'], 30)
    for day in ('Saturday', 'Sunday', 'Monday'):
        wants(conn, day, sheets, Boyz=20)

    row = row_for(sale.candidates(conn), 'Boyz')

    assert row['needed'] == 20, 'not 60'
    assert row['surplus'] == 10


def test_one_list_naming_a_unit_twice_wants_both_rows(conn, stages, sheets):
    """SUM inside, MAX outside. Two ten-Boy mobs in one list is twenty Boyz on
    the table, and `list_allocate` already refuses to let one squad satisfy
    both entries."""
    own(conn, stages, sheets['Boyz'], 30)
    list_id = army_lists.create_list(conn, 'Saturday')
    army_lists.add_entry(conn, list_id, sheets['Boyz'], 10)
    army_lists.add_entry(conn, list_id, sheets['Boyz'], 10)

    assert row_for(sale.candidates(conn), 'Boyz')['needed'] == 20


def test_an_unresolved_list_row_makes_the_answer_optimistic_and_says_so(
        conn, stages, sheets):
    """A row with no datasheet could be asking for anything on this screen.
    The screen has to lead with that rather than quietly presenting a number
    that might be selling Clay's army."""
    own(conn, stages, sheets['Boyz'], 30)
    list_id = army_lists.create_list(conn, 'Saturday')
    conn.execute(
        'INSERT INTO list_entries (list_id, position, raw_name, model_count) '
        'VALUES (?, 1, ?, 10)', (list_id, 'Sum Fing Orky'))

    assert sale.candidates(conn)['unresolved'] == 1


def test_a_clean_collection_reports_nothing_unresolved(conn, stages, sheets):
    own(conn, stages, sheets['Boyz'], 30)
    wants(conn, 'Saturday', sheets, Boyz=20)

    assert sale.candidates(conn)['unresolved'] == 0


# ── Ownership, counted the way every other surface counts it ──

def test_a_disposed_model_is_not_yours_to_sell_again(conn, stages, sheets):
    unit = own(conn, stages, sheets['Boyz'], 30)
    conn.execute("UPDATE models SET disposed_on = '2026-01-01' "
                 ' WHERE unit_id = ? AND id IN (SELECT id FROM models '
                 '   WHERE unit_id = ? LIMIT 10)', (unit, unit))

    assert row_for(sale.candidates(conn), 'Boyz')['owned'] == 20


def test_models_in_a_sold_box_are_not_proposed(conn, stages, sheets):
    """`_ACTIVE_UNIT`, the same fragment the collection uses. A kit marked sold
    takes its models out of ownership."""
    unit = own(conn, stages, sheets['Boyz'], 30)
    kit = conn.execute(
        "INSERT INTO kits (name, box_state, status, created_at, updated_at) "
        "VALUES ('Gone', 'opened', 'sold', ?, ?)",
        (db.now(), db.now())).lastrowid
    conn.execute('UPDATE units SET kit_id = ? WHERE id = ?', (kit, unit))

    assert row_for(sale.candidates(conn), 'Boyz') is None


def test_a_model_already_on_the_shortlist_is_not_proposed_twice(conn, stages,
                                                                sheets):
    """It still counts as owned — it is still on the shelf — but the screen
    stops suggesting it. One that keeps proposing what you already decided is
    one you stop reading."""
    unit = own(conn, stages, sheets['Boyz'], 30)
    col.list_for_sale(conn, unit, 10)

    row = row_for(sale.candidates(conn), 'Boyz')

    assert row['owned'] == 30, 'still on the shelf'
    assert row['listed'] == 10
    assert row['to_propose'] == 20, 'the surplus, less what is already listed'


def test_models_inside_a_sealed_box_are_not_proposed_loose(conn, stages, sheets):
    """The two sections would otherwise offer the same plastic twice — once as
    a box worth selling shut, and once as models that cannot leave it without
    destroying exactly what makes the box worth selling.

    They still count as owned, because they are: they sit on the shelf and
    they satisfy a list. They are simply not this section's to propose.
    """
    unit = own(conn, stages, sheets['Gorkanaut'], 2)
    kit = sealed(conn, 'Gorkanaut box', sheets, [('Gorkanaut', 2)])
    conn.execute('UPDATE units SET kit_id = ? WHERE id = ?', (kit, unit))

    result = sale.candidates(conn)

    assert [b['name'] for b in result['boxes']] == ['Gorkanaut box']
    assert row_for(result, 'Gorkanaut') is None, 'the box already offers these'
    assert sale.totals(result)['models'] == 0


def test_only_the_sealed_part_of_a_surplus_is_held_out(conn, stages, sheets):
    """Owning ten loose and two sealed means ten to propose, not twelve and
    not zero."""
    loose = own(conn, stages, sheets['Boyz'], 10)
    boxed = own(conn, stages, sheets['Boyz'], 2)
    kit = sealed(conn, 'Boyz box', sheets, [('Nobz', 5)])
    conn.execute('UPDATE units SET kit_id = ? WHERE id = ?', (kit, boxed))

    row = row_for(sale.candidates(conn), 'Boyz')

    assert row['owned'] == 12, 'all twelve are his'
    assert row['sealed'] == 2
    assert row['to_propose'] == 10
    assert loose  # the loose unit is the one the row links to


def test_a_wishlist_model_is_not_something_you_own(conn, stages, sheets):
    """Wanted and not had. Proposing to sell it would be absurd."""
    own(conn, stages, sheets['Boyz'], 5, stage='Wishlist')

    assert row_for(sale.candidates(conn), 'Boyz') is None


# ── The order, which is the usable part ──────────────────

def test_most_plastic_freed_weights_by_effort(conn, stages, sheets):
    """A surplus Gorkanaut is worth more shelf than a surplus mob of Boyz, and
    this app weights by effort everywhere else for exactly that reason."""
    own(conn, stages, sheets['Boyz'], 6)          # 6 × effort 1 = 6
    own(conn, stages, sheets['Gorkanaut'], 1)     # 1 × effort 8 = 8

    names = [r['name'] for r in sale.candidates(conn, sort='space')['surplus']]

    assert names[0] == 'Gorkanaut'


def test_nothing_invested_puts_untouched_sprues_first(conn, stages, sheets):
    """The cheapest thing to let go of is the box you never opened."""
    own(conn, stages, sheets['Nobz'], 5, stage='Battle ready')
    own(conn, stages, sheets['Boyz'], 5, stage='On sprue')

    rows = sale.candidates(conn, sort='untouched')['surplus']

    assert rows[0]['name'] == 'Boyz'
    assert rows[0]['unstarted'] == 5


def test_an_undated_kit_sorts_last_under_longest_owned(conn, stages, sheets):
    """Most kits carry no acquisition date, and letting unknown masquerade as
    oldest would bury the box actually bought three years ago."""
    dated = own(conn, stages, sheets['Boyz'], 5)
    kit = conn.execute(
        "INSERT INTO kits (name, acquired_on, box_state, status, created_at, "
        "updated_at) VALUES ('Old', '2023-01-01', 'opened', 'owned', ?, ?)",
        (db.now(), db.now())).lastrowid
    conn.execute('UPDATE units SET kit_id = ? WHERE id = ?', (kit, dated))
    own(conn, stages, sheets['Nobz'], 5)          # no kit, so no date

    rows = sale.candidates(conn, sort='oldest')['surplus']

    assert [r['name'] for r in rows] == ['Boyz', 'Nobz']


def test_an_unknown_sort_falls_back_rather_than_failing(conn, stages, sheets):
    own(conn, stages, sheets['Boyz'], 5)

    assert sale.candidates(conn, sort='nonsense')['surplus']


# ── Sealed boxes ─────────────────────────────────────────

def sealed(conn, name, sheets, contents, **fields):
    template = kt.create_template(
        conn, f'{name} template',
        [{'datasheet_id': sheets[n], 'model_count': c} for n, c in contents])
    kit = conn.execute(
        'INSERT INTO kits (name, kit_template_id, box_state, status, '
        'acquired_on, cost_cents, created_at, updated_at) '
        "VALUES (?, ?, 'sealed', 'owned', ?, ?, ?, ?)",
        (name, template, fields.get('acquired_on'), fields.get('cost_cents'),
         db.now(), db.now())).lastrowid
    for unit, _ in contents:
        conn.execute('INSERT INTO kit_datasheets (kit_id, datasheet_id) '
                     'VALUES (?, ?)', (kit, sheets[unit]))
    return kit


def test_a_sealed_box_nothing_wants_is_a_candidate(conn, stages, sheets):
    """The case where the box really is the unit of action: `box_state` exists
    because a sealed box carries a resale premium an opened one does not."""
    sealed(conn, 'Combat Patrol', sheets, [('Boyz', 20), ('Trukk', 1)],
           acquired_on='2024-03-01', cost_cents=11500)

    result = sale.candidates(conn)

    assert [b['name'] for b in result['boxes']] == ['Combat Patrol']
    assert result['boxes'][0]['cost_cents'] == 11500
    assert result['boxes'][0]['acquired_on'] == '2024-03-01'


def test_a_sealed_box_a_list_wants_is_held_back_and_named(conn, stages, sheets):
    """Named, not dropped. "Nothing sealed worth selling" and "four sealed
    boxes, every one spoken for" are different facts and only one means stop
    looking."""
    sealed(conn, 'Combat Patrol', sheets, [('Boyz', 20), ('Trukk', 1)])
    wants(conn, 'Saturday', sheets, Trukk=1)

    result = sale.candidates(conn)

    assert result['boxes'] == []
    assert [b['name'] for b in result['held_back']] == ['Combat Patrol']
    assert result['held_back'][0]['wanted_by_a_list'] == ['Trukk']


def test_one_wanted_unit_holds_the_whole_box_back(conn, stages, sheets):
    """A Combat Patrol is sold shut. One unit inside it that a list asks for is
    enough to make selling the box the wrong move, however little of the box
    that unit is."""
    sealed(conn, 'Combat Patrol', sheets,
           [('Boyz', 20), ('Nobz', 5), ('Trukk', 1)])
    wants(conn, 'Saturday', sheets, Trukk=1)

    assert sale.candidates(conn)['boxes'] == []


def test_an_opened_box_is_not_a_sealed_candidate(conn, stages, sheets):
    """Opening is irreversible and the premium goes with it. What is left is a
    surplus-models question, which is the other section."""
    kit = sealed(conn, 'Combat Patrol', sheets, [('Boyz', 20)])
    conn.execute("UPDATE kits SET box_state = 'opened' WHERE id = ?", (kit,))

    assert sale.candidates(conn)['boxes'] == []


def test_a_sold_box_is_not_offered_for_sale_again(conn, stages, sheets):
    kit = sealed(conn, 'Combat Patrol', sheets, [('Boyz', 20)])
    conn.execute("UPDATE kits SET status = 'sold' WHERE id = ?", (kit,))

    assert sale.candidates(conn)['boxes'] == []


def test_the_totals_count_what_is_left_to_propose(conn, stages, sheets):
    own(conn, stages, sheets['Boyz'], 30)
    wants(conn, 'Saturday', sheets, Boyz=20)
    sealed(conn, 'Combat Patrol', sheets, [('Gorkanaut', 1)])

    result = sale.candidates(conn)
    totals = sale.totals(result)

    assert totals == {'boxes': 1, 'datasheets': 1, 'models': 10}


def test_an_empty_collection_proposes_nothing(conn, stages, sheets):
    result = sale.candidates(conn)

    assert result['surplus'] == [] and result['boxes'] == []
    assert sale.totals(result) == {'boxes': 0, 'datasheets': 0, 'models': 0}
