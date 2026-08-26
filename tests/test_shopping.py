"""Turning "20 Boyz short" into "buy two Boyz boxes".

Spec §7. The wishlist answers in datasheets; a shop sells boxes. What matters
most in here is that the plan never *understates* — not the models it leaves
uncovered, not the spare it arrives with, and above all not the price. A
shopping total that reads low is the one failure that costs Clay money at a
till, so every test that touches money is really testing that it refuses to
guess.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collection as col
import database as db
import kit_templates as kt
import lists as army_lists
import shopping


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / 'shopping.db')
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
    for bsid, name in (('boyz', 'Boyz'), ('nobz', 'Nobz'),
                       ('trukk', 'Trukk'), ('wb', 'Warboss')):
        made[name] = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'created_at, updated_at) VALUES (?,?,?,1,?,?)',
            (bsid, name, orks, db.now(), db.now())).lastrowid
    return made


def want(conn, stages, datasheet_id, count):
    """Put models on the wishlist — the unowned end of the stage ladder."""
    return col.create_unit(conn, datasheet_id, count,
                           stage_id=stages['Wishlist'])


def box(conn, name, contents, sheets, rrp_cents=None, faction_id=None):
    return kt.create_template(
        conn, name,
        [{'datasheet_id': sheets[n], 'model_count': c} for n, c in contents],
        rrp_cents=rrp_cents, faction_id=faction_id)


def line_for(result, name):
    for line in result['lines']:
        if line['box']['name'] == name:
            return line
    return None


# ── The cover ────────────────────────────────────────────

def test_a_box_of_ten_covers_a_want_of_seven(conn, stages, sheets):
    """Games Workshop does not sell seven Boyz. The whole point of the screen
    is that it answers in things you can actually put on a counter."""
    want(conn, stages, sheets['Boyz'], 7)
    box(conn, 'Boyz', [('Boyz', 10)], sheets)

    best = shopping.plan(conn)['best']

    assert best['boxes'] == 1
    assert line_for(best, 'Boyz')['qty'] == 1


def test_twenty_boyz_against_a_box_of_ten_buys_two(conn, stages, sheets):
    """One box at a time, looping — which is how a want bigger than a box gets
    answered without the code reasoning about multiples."""
    want(conn, stages, sheets['Boyz'], 20)
    box(conn, 'Boyz', [('Boyz', 10)], sheets)

    best = shopping.plan(conn)['best']

    assert len(best['lines']) == 1, 'the same box twice is one line'
    assert line_for(best, 'Boyz')['qty'] == 2
    assert not best['uncovered']


def test_the_overage_is_carried_and_totalled(conn, stages, sheets):
    """Three spare Boyz are a real thing that arrives in a real box. A plan
    that covers the list in fewer boxes by over-buying is not obviously
    better, and nothing else on the screen would say so."""
    want(conn, stages, sheets['Boyz'], 7)
    box(conn, 'Boyz', [('Boyz', 10)], sheets)

    best = shopping.plan(conn)['best']

    assert line_for(best, 'Boyz')['spare'] == 3
    assert best['spare'] == 3


def test_a_bundle_beats_two_boxes_when_it_covers_more(conn, stages, sheets):
    want(conn, stages, sheets['Boyz'], 10)
    want(conn, stages, sheets['Trukk'], 1)
    box(conn, 'Boyz', [('Boyz', 10)], sheets)
    box(conn, 'Trukk', [('Trukk', 1)], sheets)
    box(conn, 'Combat Patrol', [('Boyz', 10), ('Trukk', 1)], sheets)

    best = shopping.plan(conn)['best']

    assert best['boxes'] == 1
    assert line_for(best, 'Combat Patrol') is not None


def test_a_line_says_what_it_is_for(conn, stages, sheets):
    """"Combat Patrol" on its own leaves Clay to work out what it was covering.
    The line names the models it contributes."""
    want(conn, stages, sheets['Boyz'], 10)
    want(conn, stages, sheets['Trukk'], 1)
    box(conn, 'Combat Patrol', [('Boyz', 10), ('Trukk', 1)], sheets)

    line = line_for(shopping.plan(conn)['best'], 'Combat Patrol')

    assert {n['name']: n['n'] for n in line['names']} == {'Boyz': 10, 'Trukk': 1}


def test_a_smaller_overage_wins_an_otherwise_even_tie(conn, stages, sheets):
    """Two boxes covering the same seven Boyz are not equally good if one
    arrives with three spare and the other with thirteen."""
    want(conn, stages, sheets['Boyz'], 7)
    box(conn, 'Big box', [('Boyz', 20)], sheets)
    box(conn, 'Small box', [('Boyz', 10)], sheets)

    best = shopping.plan(conn)['best']

    assert line_for(best, 'Small box') is not None
    assert line_for(best, 'Big box') is None


def test_the_same_wishlist_always_produces_the_same_plan(conn, stages, sheets):
    """A recommendation that reshuffled between two loads is one Clay can
    neither trust nor check against what he bought yesterday."""
    want(conn, stages, sheets['Boyz'], 10)
    box(conn, 'B box', [('Boyz', 10)], sheets)
    box(conn, 'A box', [('Boyz', 10)], sheets)

    first = shopping.plan(conn)['best']
    second = shopping.plan(conn)['best']

    assert [line['box']['name'] for line in first['lines']] == \
           [line['box']['name'] for line in second['lines']]


# ── Never drop a line ────────────────────────────────────

def test_a_want_no_box_contains_is_reported_not_dropped(conn, stages, sheets):
    """The invariant this repo keeps everywhere: a silently dropped line is a
    shortfall Clay discovers at the till. If nothing in the catalogue holds a
    Warboss, the screen has to say so."""
    want(conn, stages, sheets['Boyz'], 10)
    want(conn, stages, sheets['Warboss'], 1)
    box(conn, 'Boyz', [('Boyz', 10)], sheets)

    result = shopping.plan(conn)

    assert [u['name'] for u in result['uncovered']] == ['Warboss']
    assert result['uncovered'][0]['short'] == 1


def test_a_partly_covered_want_reports_only_the_remainder(conn, stages, sheets):
    want(conn, stages, sheets['Boyz'], 25)
    box(conn, 'Boyz', [('Boyz', 10)], sheets)
    # Nothing else exists, so two boxes cover 20 and five stay short.
    result = shopping.plan(conn)

    assert line_for(result['best'], 'Boyz')['qty'] == 3, \
        'a third box is bought rather than leaving five short'
    assert not result['uncovered']


def test_an_empty_wishlist_plans_nothing(conn, stages, sheets):
    box(conn, 'Boyz', [('Boyz', 10)], sheets)

    result = shopping.plan(conn)

    assert result['best']['lines'] == []
    assert result['uncovered'] == []
    assert result['saving'] is None


def test_an_empty_catalogue_reports_everything_uncovered(conn, stages, sheets):
    """The state Clay is actually in today: a wishlist and almost no defined
    boxes. It must read as "define these boxes", never as an empty plan that
    looks like there is nothing to buy."""
    want(conn, stages, sheets['Boyz'], 10)

    result = shopping.plan(conn)

    assert result['best']['lines'] == []
    assert [u['name'] for u in result['uncovered']] == ['Boyz']


# ── Money, and refusing to guess about it ────────────────

def test_a_fully_priced_plan_totals(conn, stages, sheets):
    want(conn, stages, sheets['Boyz'], 20)
    box(conn, 'Boyz', [('Boyz', 10)], sheets, rrp_cents=3750)

    best = shopping.plan(conn)['best']

    assert best['state'] == shopping.PRICED
    assert best['cents'] == 7500


def test_one_unpriced_box_makes_the_whole_total_a_floor(conn, stages, sheets):
    """The failure worth engineering against: a total that quietly leaves out
    the boxes it has no price for reads *lower* than the truth, which is the
    one direction a shopping total must never be wrong in."""
    want(conn, stages, sheets['Boyz'], 10)
    want(conn, stages, sheets['Trukk'], 1)
    box(conn, 'Boyz', [('Boyz', 10)], sheets, rrp_cents=3750)
    box(conn, 'Trukk', [('Trukk', 1)], sheets)          # no price

    best = shopping.plan(conn)['best']

    assert best['state'] == shopping.PARTIAL
    assert best['cents'] == 3750, 'the figure is what is known, shown as a floor'


def test_a_plan_with_no_prices_at_all_shows_no_figure(conn, stages, sheets):
    """Zero would be a lie in the most expensive direction available."""
    want(conn, stages, sheets['Boyz'], 10)
    box(conn, 'Boyz', [('Boyz', 10)], sheets)

    best = shopping.plan(conn)['best']

    assert best['state'] == shopping.UNPRICED
    assert best['cents'] is None


def test_a_known_price_wins_a_tie_over_an_unknown_one(conn, stages, sheets):
    """Not an optimisation — an unpriced box turns the whole total into a
    floor, so where the choice is otherwise even the plan prefers the one it
    can stand behind."""
    want(conn, stages, sheets['Boyz'], 10)
    box(conn, 'A unpriced', [('Boyz', 10)], sheets)
    box(conn, 'B priced', [('Boyz', 10)], sheets, rrp_cents=3750)

    best = shopping.plan(conn)['best']

    assert line_for(best, 'B priced') is not None
    assert best['state'] == shopping.PRICED


def test_the_cheaper_of_two_priced_boxes_wins(conn, stages, sheets):
    want(conn, stages, sheets['Boyz'], 10)
    box(conn, 'Dear', [('Boyz', 10)], sheets, rrp_cents=5000)
    box(conn, 'Cheap', [('Boyz', 10)], sheets, rrp_cents=3750)

    assert line_for(shopping.plan(conn)['best'], 'Cheap') is not None


# ── Bundle against à la carte ────────────────────────────

def test_the_alacarte_side_uses_only_single_unit_boxes(conn, stages, sheets):
    want(conn, stages, sheets['Boyz'], 10)
    want(conn, stages, sheets['Trukk'], 1)
    box(conn, 'Combat Patrol', [('Boyz', 10), ('Trukk', 1)], sheets, rrp_cents=9000)
    box(conn, 'Boyz', [('Boyz', 10)], sheets, rrp_cents=3750)
    box(conn, 'Trukk', [('Trukk', 1)], sheets, rrp_cents=3500)

    result = shopping.plan(conn)

    assert line_for(result['best'], 'Combat Patrol') is not None
    assert {line['box']['name'] for line in result['alacarte']['lines']} == \
        {'Boyz', 'Trukk'}


def test_a_saving_is_the_difference_between_the_two_totals(conn, stages, sheets):
    want(conn, stages, sheets['Boyz'], 10)
    want(conn, stages, sheets['Trukk'], 1)
    box(conn, 'Combat Patrol', [('Boyz', 10), ('Trukk', 1)], sheets, rrp_cents=6000)
    box(conn, 'Boyz', [('Boyz', 10)], sheets, rrp_cents=3750)
    box(conn, 'Trukk', [('Trukk', 1)], sheets, rrp_cents=3500)

    result = shopping.plan(conn)

    assert result['best']['cents'] == 6000
    assert result['alacarte']['cents'] == 7250
    assert result['saving'] == 1250


def test_a_bundle_that_costs_more_reports_a_negative_saving(conn, stages, sheets):
    """A comparison that only ever flatters the bundle is not a comparison. If
    the singles are cheaper, that is the thing worth knowing."""
    want(conn, stages, sheets['Boyz'], 10)
    want(conn, stages, sheets['Trukk'], 1)
    box(conn, 'Combat Patrol', [('Boyz', 10), ('Trukk', 1)], sheets, rrp_cents=9000)
    box(conn, 'Boyz', [('Boyz', 10)], sheets, rrp_cents=3750)
    box(conn, 'Trukk', [('Trukk', 1)], sheets, rrp_cents=3500)

    assert shopping.plan(conn)['saving'] == -1750


def test_no_saving_is_claimed_when_either_side_is_only_a_floor(conn, stages, sheets):
    """Subtracting one floor from another gives a number that bounds nothing —
    a figure that looks like money and means nothing."""
    want(conn, stages, sheets['Boyz'], 10)
    want(conn, stages, sheets['Trukk'], 1)
    box(conn, 'Combat Patrol', [('Boyz', 10), ('Trukk', 1)], sheets, rrp_cents=6000)
    box(conn, 'Boyz', [('Boyz', 10)], sheets, rrp_cents=3750)
    box(conn, 'Trukk', [('Trukk', 1)], sheets)          # no price

    result = shopping.plan(conn)

    assert result['alacarte']['state'] == shopping.PARTIAL
    assert result['saving'] is None


def test_no_saving_is_claimed_when_the_singles_cannot_cover_it(conn, stages, sheets):
    """If no one sells a Trukk on its own, the à la carte total is not cheaper
    — it is impossible, and quietly costing less by leaving the Trukk out would
    recommend against the only box that works."""
    want(conn, stages, sheets['Boyz'], 10)
    want(conn, stages, sheets['Trukk'], 1)
    box(conn, 'Combat Patrol', [('Boyz', 10), ('Trukk', 1)], sheets, rrp_cents=6000)
    box(conn, 'Boyz', [('Boyz', 10)], sheets, rrp_cents=3750)

    result = shopping.plan(conn)

    assert [u['name'] for u in result['alacarte']['uncovered']] == ['Trukk']
    assert result['saving'] is None


# ── Agreement with the rest of the app ───────────────────

def test_two_lists_wanting_the_same_boyz_buy_one_set_of_boxes(conn, stages, sheets):
    """The wishlist deduplicates across lists on the maximum, not the sum, and
    this reads through `lists.wishlist` precisely so it inherits that. A second
    query here would be a second chance to buy thirty Boyz for two games that
    need twenty."""
    saturday = army_lists.create_list(conn, 'Saturday')
    sunday = army_lists.create_list(conn, 'Sunday')
    for list_id, n in ((saturday, 10), (sunday, 20)):
        army_lists.add_entry(conn, list_id, sheets['Boyz'], n)
        army_lists.raise_wishlist(conn, list_id)
    box(conn, 'Boyz', [('Boyz', 10)], sheets)

    result = shopping.plan(conn)

    assert sum(w['wanted'] for w in result['wanted']) == 20
    assert line_for(result['best'], 'Boyz')['qty'] == 2


def test_owned_models_are_not_bought_again(conn, stages, sheets):
    """The wishlist is what is wanted *and not owned*. Ten Boyz on the shelf
    must not appear in a plan to buy Boyz."""
    col.create_unit(conn, sheets['Boyz'], 10, stage_id=stages['On sprue'])
    box(conn, 'Boyz', [('Boyz', 10)], sheets)

    result = shopping.plan(conn)

    assert result['wanted'] == []
    assert result['best']['lines'] == []


def test_a_template_with_no_contents_is_not_a_box(conn, stages, sheets):
    """`create_template` refuses to make one, but a row could predate that rule
    or have had its last unit removed. A box in the plan holding nothing is a
    trip to a shop for no models.

    Asserted on `_boxes` directly. Going through `plan` proves nothing here:
    an empty box covers nothing, so the cover would skip it whether or not
    this filter existed, and the test would pass with the filter deleted.
    """
    empty = box(conn, 'Empty', [('Boyz', 1)], sheets)
    conn.execute('DELETE FROM kit_template_units WHERE kit_template_id = ?',
                 (empty,))
    box(conn, 'Real', [('Boyz', 10)], sheets)

    assert [b['name'] for b in shopping._boxes(conn)] == ['Real']


def test_a_box_that_covers_nothing_does_not_hang_the_plan(conn, stages, sheets):
    """The loop terminates because every pass reduces what is outstanding. A
    box contributing zero would break that and spin forever, so the failure
    mode here is not a wrong plan but a page that never loads."""
    want(conn, stages, sheets['Boyz'], 10)
    box(conn, 'Nobz', [('Nobz', 5)], sheets)          # nothing Clay wants

    result = shopping.plan(conn)

    assert result['best']['lines'] == []
    assert [u['name'] for u in result['uncovered']] == ['Boyz']


def test_a_datasheet_listed_twice_in_one_box_is_summed(conn, stages, sheets):
    """Nothing stops a template naming the same datasheet on two rows. The last
    row winning would undercount the box and buy a second copy of it."""
    want(conn, stages, sheets['Boyz'], 20)
    template = box(conn, 'Boyz', [('Boyz', 10)], sheets)
    conn.execute('INSERT INTO kit_template_units (kit_template_id, '
                 'datasheet_id, model_count) VALUES (?, ?, 10)',
                 (template, sheets['Boyz']))

    best = shopping.plan(conn)['best']

    assert line_for(best, 'Boyz')['qty'] == 1, 'one box already holds twenty'
