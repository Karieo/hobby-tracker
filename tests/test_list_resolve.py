"""Attaching a datasheet to a pasted name, and refusing to when unsure.

Section 7's resolution step. The tests that matter most here are the ones that
assert something *does not* resolve. A wrong confident match puts a unit in the
report Clay never wrote, quietly absorbs the models he owns of the wrong thing,
and still comes out looking like an answer — which is worse than a row with a
picker on it, because the row is visible and costs one tap.
"""

import pytest

import database as db
import list_parse
import list_resolve as resolve
from list_parse import ParsedEntry


@pytest.fixture
def sheets(conn):
    """Names chosen for the collisions they cause, not for flavour.

    Skarbrand really is two datasheets in the imported data — Chaos Daemons and
    World Eaters — and 66 names are like it. Intercessor and Interceptor really
    are one letter apart. Boyz really does have a minimum of ten.
    """
    orks = db.upsert_faction(conn, 'Orks', 'orks')
    marines = db.upsert_faction(conn, 'Space Marines', 'space-marines')
    daemons = db.upsert_faction(conn, 'Chaos Daemons', 'chaos-daemons')
    eaters = db.upsert_faction(conn, 'World Eaters', 'world-eaters')

    made = {}
    rows = (
        ('boyz', 'Boyz', orks, 10, 20),
        ('warboss', 'Warboss', orks, 1, 1),
        ('warbikers', 'Warbikers', orks, 3, 9),
        ('kans', 'Killa Kans', orks, 1, 3),
        ('intercessor', 'Intercessor Squad', marines, 5, 10),
        ('interceptor', 'Interceptor Squad', marines, 5, 10),
        ('skarbrand-cd', 'Skarbrand', daemons, 1, 1),
        ('skarbrand-we', 'Skarbrand', eaters, 1, 1),
    )
    for bsid, name, faction, lo, hi in rows:
        made[bsid] = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'min_models, max_models, created_at, updated_at) '
            'VALUES (?, ?, ?, 1, ?, ?, ?, ?)',
            (bsid, name, faction, lo, hi, db.now(), db.now())).lastrowid
    made['orks'] = orks
    made['marines'] = marines
    made['daemons'] = daemons
    return made


def entry(name, count=1, position=0):
    return ParsedEntry(name, count, None, position)


def one(conn, name, count=1, **kw):
    return resolve.resolve_entries(conn, [entry(name, count)], **kw)[0]


# ── The fold ─────────────────────────────────────────────────────────────────

def test_case_punctuation_and_spacing_do_not_matter():
    assert resolve.normalise('Killa  Kans!') == resolve.normalise('killa kans')
    assert resolve.normalise("Ghazghkull’s Boyz") == resolve.normalise(
        "Ghazghkull's  boyz")


def test_a_trailing_parenthetical_comes_off():
    """It has to be stripped before folding — `norm` turns "Boyz (Legends)"
    into "boyz legends", which matches nothing and cannot be recovered."""
    assert resolve.normalise('Boyz (Legends)') == 'boyz'
    assert resolve.normalise('Boyz [Legends]') == 'boyz'


def test_a_parenthetical_inside_the_name_stays():
    assert resolve.normalise('Warboss (on foot) with klaw') != 'warboss'


# ── Similarity ───────────────────────────────────────────────────────────────

def test_word_order_is_free():
    assert resolve.similarity('intercessor squad', 'squad intercessor') == 100


def test_a_one_letter_typo_still_scores_a_match():
    assert resolve.similarity('killa kans', 'killa kanz') >= resolve.MATCH_SCORE


def test_a_longer_name_is_not_a_perfect_match_for_the_short_one_inside_it():
    """THE REGRESSION THIS MODULE ALMOST SHIPPED WITH.

    Section 7 asks for rapidfuzz's `token_set_ratio`, which compares shared
    words against what each name adds — so any strict subset scores 100. Built
    that way and run against the real Ork datasheets, "Warboss on Warbike"
    resolved to Warboss at 100 with no runner-up close enough to trip the
    margin rule: a wrong confident match on the very example Section 7 uses to
    explain why the alias table has to exist.

    Sorting the words instead scores it 56.
    """
    assert resolve.similarity('warboss on warbike', 'warboss') < resolve.MATCH_SCORE


# ── Order: alias, exact, fuzzy, null ─────────────────────────────────────────

def test_an_alias_short_circuits_everything(conn, sheets):
    """It outranks exact on purpose. An alias is Clay's own answer about this
    spelling, and nothing the app works out beats what he said."""
    resolve.learn_alias(conn, 'Boyz', sheets['warboss'])
    got = one(conn, 'Boyz')
    assert got.datasheet_id == sheets['warboss']
    assert got.resolved_by == 'alias' and got.score == 100


def test_an_exact_name_resolves_without_scoring_anything(conn, sheets):
    got = one(conn, 'killa  KANS')
    assert got.datasheet_id == sheets['kans']
    assert got.resolved_by == 'exact'


def test_a_typo_resolves_by_fuzzy(conn, sheets):
    got = one(conn, 'Killa Kanz', count=3)
    assert got.datasheet_id == sheets['kans']
    assert got.resolved_by == 'fuzzy'
    assert got.score >= resolve.MATCH_SCORE


def test_a_name_that_means_nothing_resolves_to_nothing(conn, sheets):
    got = one(conn, 'right heres what im bringing saturday')
    assert got.datasheet_id is None and got.resolved_by is None
    assert got.why


def test_an_empty_name_is_not_an_error(conn, sheets):
    assert one(conn, '   ').datasheet_id is None


# ── The margin rule ──────────────────────────────────────────────────────────

def test_two_names_scoring_alike_resolve_to_neither(conn, sheets):
    """Intercessor and Interceptor Squad are one letter apart. Whichever scores
    higher, the other is right behind it, and picking the leader would be a
    coin toss dressed as an answer."""
    got = one(conn, 'Intercesor Squad', count=5, faction_id=sheets['marines'])
    assert got.datasheet_id is None
    assert 'score alike' in got.why
    offered = [c['name'] for c in got.candidates]
    assert 'Intercessor Squad' in offered and 'Interceptor Squad' in offered


def test_a_clear_leader_does_resolve(conn, sheets):
    """The margin rule must not refuse everything — a typo with one plausible
    reading is exactly what fuzzy matching is for."""
    got = one(conn, 'Warbiker', faction_id=sheets['orks'])
    assert got.datasheet_id == sheets['warbikers']
    assert got.resolved_by == 'fuzzy'


def test_a_score_below_the_threshold_never_matches_however_far_ahead(conn, sheets):
    got = one(conn, 'Zzzzzzz Qqqqqq', faction_id=sheets['orks'])
    assert got.datasheet_id is None


# ── Faction scoping ──────────────────────────────────────────────────────────

def test_one_name_two_factions_resolves_to_neither(conn, sheets):
    """66 names in the imported data are like this. Picking the one that sorted
    first is the silent wrong answer the whole module exists to refuse."""
    got = one(conn, 'Skarbrand')
    assert got.datasheet_id is None
    assert 'Chaos Daemons' in got.why and 'World Eaters' in got.why


def test_the_list_s_faction_breaks_the_tie(conn, sheets):
    got = one(conn, 'Skarbrand', faction_id=sheets['daemons'])
    assert got.datasheet_id == sheets['skarbrand-cd']
    assert got.resolved_by == 'exact'


def test_scoping_keeps_a_typo_inside_the_faction(conn, sheets):
    """An Ork list saying "Intercesor Squad" must not fuzzy its way into a
    Space Marine datasheet."""
    got = one(conn, 'Intercesor Squad', count=5, faction_id=sheets['orks'])
    assert got.datasheet_id is None


def test_the_picker_widens_when_the_faction_has_nothing_close(conn, sheets):
    """Auto-matching stays scoped; only what Clay is shown widens. Offering
    four unrelated Ork units for "Intercesor Squad" is worse than offering
    none, and the right answer is one tap away in another faction."""
    got = one(conn, 'Intercesor Squad', count=5, faction_id=sheets['orks'])
    assert 'Intercessor Squad' in [c['name'] for c in got.candidates]


def test_a_hopeless_name_offers_nothing_rather_than_noise(conn, sheets):
    got = one(conn, 'Zzzzzzz Qqqqqq')
    assert got.candidates == ()


def test_the_list_s_faction_is_read_from_the_list(conn, sheets):
    import lists
    lid = lists.create_list(conn, 'Saturday', faction_id=sheets['daemons'])
    assert resolve.list_faction(conn, lid) == sheets['daemons']


def test_an_army_s_faction_stands_in_when_the_list_has_none(conn, sheets):
    import collection as col
    import lists
    army = col.create_army(conn, 'Khorne', primary_faction_id=sheets['daemons'])
    lid = lists.create_list(conn, 'Saturday')
    conn.execute('UPDATE army_lists SET army_id = ? WHERE id = ?', (army, lid))
    assert resolve.list_faction(conn, lid) == sheets['daemons']


# ── The minimum-unit-size clamp ──────────────────────────────────────────────

def test_a_count_below_the_legal_minimum_comes_up_to_it(conn, sheets):
    """The original spec: "default to minimum unit size where not [present]".
    It is also the net under `list_parse`'s known false negative — a flat New
    Recruit block reads 20 Boyz as 1, and Boyz have a minimum of ten."""
    got = one(conn, 'Boyz', count=1)
    assert got.model_count == 10
    assert got.parsed_count == 1, 'what the text said is kept, not overwritten'


def test_a_count_above_the_minimum_is_left_alone(conn, sheets):
    got = one(conn, 'Boyz', count=20)
    assert got.model_count == 20 and got.parsed_count == 20


def test_the_clamp_never_reduces_a_count(conn, sheets):
    """Above the legal maximum is Clay's problem to see, not the parser's to
    hide — §9's validation is where an illegal list gets called illegal."""
    got = one(conn, 'Killa Kans', count=9)
    assert got.model_count == 9


def test_an_unresolved_line_is_not_clamped_to_anything(conn, sheets):
    got = one(conn, 'Warboss on Warbike', count=1)
    assert got.datasheet_id is None
    assert got.model_count == got.parsed_count == 1


# ── Manual resolution, and the write-back that makes it worth doing ──────────

def test_resolving_by_hand_teaches_the_alias(conn, sheets):
    """"If you have to re-answer 'which datasheet is Warboss on Warbike?' every
    time you paste a list, you'll stop pasting lists.\""""
    import lists
    lid = lists.create_list(conn, 'Saturday', faction_id=sheets['orks'])
    eid = conn.execute(
        'INSERT INTO list_entries (list_id, position, raw_name, model_count) '
        'VALUES (?, 0, ?, 1)', (lid, 'Warboss on Warbike')).lastrowid

    assert one(conn, 'Warboss on Warbike').datasheet_id is None
    resolve.resolve_entry(conn, eid, sheets['warboss'])

    again = one(conn, 'Warboss on Warbike')
    assert again.datasheet_id == sheets['warboss']
    assert again.resolved_by == 'alias', 'the second paste costs no taps'


def test_resolving_by_hand_marks_the_entry(conn, sheets):
    import lists
    lid = lists.create_list(conn, 'Saturday')
    eid = conn.execute(
        'INSERT INTO list_entries (list_id, position, raw_name, model_count) '
        'VALUES (?, 0, ?, 1)', (lid, 'Sum ov da Boyz')).lastrowid
    resolve.resolve_entry(conn, eid, sheets['boyz'])
    row = conn.execute('SELECT datasheet_id, model_count, resolved_by FROM '
                       'list_entries WHERE id = ?', (eid,)).fetchone()
    assert row['datasheet_id'] == sheets['boyz']
    assert row['resolved_by'] == 'manual'
    assert row['model_count'] == 10, 'the minimum applies to a hand pick too'


def test_a_correction_replaces_the_alias_rather_than_failing(conn, sheets):
    """`alias` is UNIQUE. Clay's second answer about a spelling is a better one
    than his first, and an integrity error here would strand him on it."""
    resolve.learn_alias(conn, 'Da Big Boss', sheets['boyz'])
    resolve.learn_alias(conn, 'Da Big Boss', sheets['warboss'])
    assert one(conn, 'Da Big Boss').datasheet_id == sheets['warboss']
    n = conn.execute('SELECT COUNT(*) FROM datasheet_aliases').fetchone()[0]
    assert n == 1


def test_an_alias_is_learned_folded_so_the_spelling_can_vary(conn, sheets):
    resolve.learn_alias(conn, 'Warboss on Warbike', sheets['warboss'])
    assert one(conn, 'WARBOSS  ON   WARBIKE!').datasheet_id == sheets['warboss']


def test_resolving_an_entry_that_does_not_exist_is_refused(conn, sheets):
    with pytest.raises(ValueError):
        resolve.resolve_entry(conn, 9999, sheets['boyz'])


def test_resolving_to_a_datasheet_that_does_not_exist_is_refused(conn, sheets):
    import lists
    lid = lists.create_list(conn, 'Saturday')
    eid = conn.execute('INSERT INTO list_entries (list_id, position, '
                       'model_count) VALUES (?, 0, 1)', (lid,)).lastrowid
    with pytest.raises(ValueError):
        resolve.resolve_entry(conn, eid, 9999)


# ── End to end, on the parser's own output ───────────────────────────────────

def test_a_parsed_export_resolves_line_for_line(conn, sheets):
    parsed = list_parse.parse("""BATTLELINE

20x Boyz [180pts]
1x Warboss [65pts]
3x Killa Kans [140pts]
""")
    got = resolve.resolve_entries(conn, parsed.entries, faction_id=sheets['orks'])
    assert [r.datasheet_name for r in got] == ['Boyz', 'Warboss', 'Killa Kans']
    assert [r.model_count for r in got] == [20, 1, 3]
    assert [r.position for r in got] == [0, 1, 2]
    assert all(r.resolved_by == 'exact' for r in got)


def test_nothing_is_lost_between_parsing_and_resolving(conn, sheets):
    """The parser's contract is that no line vanishes. Resolution must not
    quietly drop the ones it could not identify either."""
    parsed = list_parse.parse('20x Boyz\nsomething nobody can read\n3x Killa Kans')
    got = resolve.resolve_entries(conn, parsed.entries, faction_id=sheets['orks'])
    assert len(got) == len(parsed.entries) == 3
    assert got[1].datasheet_id is None
    assert got[1].raw_name == 'something nobody can read'
