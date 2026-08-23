"""Reading an export, and never losing a line doing it.

Section 7's parser. The failure this file exists to catch is the quiet one: a
unit that was in the paste and is not in the report. Clay does not find that on
screen — he finds it at a table, without the unit.

Every fixture in `tests/fixtures/lists/` is **synthetic**. Every candidate host
for a real New Recruit or GW app export is refused by egress policy, so the
formats are implemented from their documented shape. `tests/fixtures/lists/
README.md` says which ones to replace first and why; these tests read whatever
is in that directory, so dropping a real export in place of a synthetic one
runs it immediately.
"""

import os

import pytest

import list_parse

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'fixtures', 'lists')


def sample(name):
    with open(os.path.join(FIXTURES, name), encoding='utf-8') as fh:
        return fh.read()


def names(parsed):
    return [e.raw_name for e in parsed.entries]


def counts(parsed):
    return {e.raw_name: e.model_count for e in parsed.entries}


# ── Which handler reads it ───────────────────────────────────────────────────

@pytest.mark.parametrize('fixture', [
    'synthetic_newrecruit_orks.txt',
    'synthetic_newrecruit_marines.txt',
    'synthetic_newrecruit_flat.txt',
])
def test_new_recruit_is_recognised(fixture):
    assert list_parse.parse(sample(fixture)).source_format == list_parse.NEWRECRUIT


@pytest.mark.parametrize('fixture', [
    'synthetic_gwapp_orks.txt',
    'synthetic_gwapp_marines.txt',
    'synthetic_gwapp_minimal.txt',
])
def test_the_gw_app_is_recognised(fixture):
    assert list_parse.parse(sample(fixture)).source_format == list_parse.GW_APP


@pytest.mark.parametrize('fixture', [
    'unknown_chat_message.txt',
    'unknown_retyped_sheet.txt',
])
def test_anything_else_falls_through_to_permissive(fixture):
    assert list_parse.parse(sample(fixture)).source_format == list_parse.UNKNOWN


def test_a_format_is_recognised_without_its_header():
    """Exports arrive pasted out of a chat message with the first lines lost.
    Detecting a format from a banner that is no longer there would drop a
    readable list onto the permissive handler for no reason."""
    full = sample('synthetic_gwapp_orks.txt')
    beheaded = '\n'.join(full.splitlines()[4:])
    assert list_parse.parse(beheaded).source_format == list_parse.GW_APP


# ── What it reads ────────────────────────────────────────────────────────────

def test_a_new_recruit_list_reads_end_to_end():
    parsed = list_parse.parse(sample('synthetic_newrecruit_orks.txt'))
    assert names(parsed) == ['Warboss', 'Painboss', 'Boyz', 'Boyz',
                             'Killa Kans', 'Deffkoptas']
    assert counts(parsed) == {'Warboss': 1, 'Painboss': 1, 'Boyz': 20,
                              'Killa Kans': 3, 'Deffkoptas': 3}
    assert [e.points for e in parsed.entries] == [65, 60, 180, 180, 140, 100]


def test_a_gw_app_list_reads_end_to_end():
    parsed = list_parse.parse(sample('synthetic_gwapp_orks.txt'))
    assert names(parsed) == ['Warboss', 'Painboss', 'Boyz', 'Boyz', 'Gretchin',
                             'Killa Kans', 'Deffkoptas', 'Trukk']
    assert counts(parsed)['Boyz'] == 20
    assert counts(parsed)['Trukk'] == 1


def test_the_same_unit_twice_stays_two_entries():
    """The whole reason allocation exists. Two 20-model Boyz mobs need forty
    Boyz, and collapsing them into one entry here would hide that before the
    allocator ever saw it."""
    parsed = list_parse.parse(sample('synthetic_newrecruit_orks.txt'))
    assert names(parsed).count('Boyz') == 2


def test_position_is_the_order_it_was_pasted_in():
    parsed = list_parse.parse(sample('synthetic_gwapp_orks.txt'))
    assert [e.position for e in parsed.entries] == list(range(len(parsed.entries)))


def test_wargear_is_discarded():
    """Section 3 excluded loadout tracking. A choppa is not a unit."""
    parsed = list_parse.parse(sample('synthetic_newrecruit_orks.txt'))
    assert not any('Choppa' in n or 'klaw' in n for n in names(parsed))
    assert parsed.discarded > 0, 'and it says how much it threw away'


def test_the_preamble_is_not_four_unknown_units():
    """Every export opens with a name, a faction, a battle size and a
    detachment. Reporting those as unresolved datasheets on every paste teaches
    Clay to skim past the unresolved rows — the one thing here he must never
    learn to ignore."""
    for fixture in ('synthetic_newrecruit_orks.txt', 'synthetic_gwapp_orks.txt',
                    'synthetic_gwapp_minimal.txt'):
        got = names(list_parse.parse(sample(fixture)))
        for scaffolding in ('Orks', 'Strike Force', 'Waaagh! Tribe',
                            'Da Green Tide', 'Patrol'):
            assert scaffolding not in got, f'{scaffolding} survived {fixture}'


def test_the_declared_total_is_read_but_kept_separate():
    """Shown beside our own figure, never used as it. §2.7 settled that this
    app prices a list from the Munitorum manual it imported."""
    parsed = list_parse.parse(sample('synthetic_newrecruit_orks.txt'))
    assert parsed.points_total == 2000
    assert sum(e.points for e in parsed.entries) != 2000, \
        'the entries do not add up to the declared total, and that is normal'


# ── Spec case 4: a character carries no count ────────────────────────────────

def test_a_character_with_no_count_is_one_model():
    parsed = list_parse.parse(sample('synthetic_gwapp_orks.txt'))
    assert counts(parsed)['Warboss'] == 1


# ── Spec case 5: an unparseable line is never dropped ────────────────────────

def test_a_line_that_means_nothing_still_becomes_a_row():
    parsed = list_parse.parse(sample('unknown_chat_message.txt'))
    assert 'right heres what im bringing saturday' in names(parsed)
    assert 'thats about 1200 i think' in names(parsed)
    assert counts(parsed)['Boyz'] == 20, 'and the real units still read'


def test_nothing_in_a_loose_paste_goes_missing():
    """Line for line: a permissive paste has no scaffolding to discard, so
    every non-blank line has to come out the other side as something."""
    text = sample('unknown_chat_message.txt')
    parsed = list_parse.parse(text)
    assert len(parsed.entries) == len([l for l in text.splitlines() if l.strip()])
    assert parsed.discarded == 0


def test_a_retyped_sheet_reads_its_unbracketed_points():
    """"Warboss - 65", "Boyz x20 - 180" — the same annotation the collection
    paste already reads, so it is read with the same pattern."""
    parsed = list_parse.parse(sample('unknown_retyped_sheet.txt'))
    by_name = {e.raw_name: e for e in parsed.entries}
    assert by_name['Warboss'].points == 65
    assert by_name['Boyz'].model_count == 20 and by_name['Boyz'].points == 180
    assert by_name['Squighog Boyz'].model_count == 6


# ── Spec case 7: nothing to read is not a crash ──────────────────────────────

def test_an_empty_paste_is_empty_rather_than_an_error():
    for text in ('', '   \n\n  \n', None):
        parsed = list_parse.parse(text)
        assert parsed.entries == []
        assert parsed.points_total is None


def test_a_wargear_only_paste_says_what_it_threw_away():
    """Zero units is the right answer, and an empty table with no explanation
    is the wrong way to give it."""
    parsed = list_parse.parse(sample('unknown_wargear_only.txt'))
    assert parsed.entries == []
    assert parsed.discarded == 4


def test_a_header_with_no_entries_reads_as_no_entries():
    parsed = list_parse.parse('Da Green Tide (2000 Points)\n\nOrks\n\nCHARACTERS\n')
    assert parsed.entries == []
    assert parsed.points_total == 2000


# ── The model-count rule, including where it is known to be wrong ────────────

def test_nesting_is_what_separates_a_model_from_a_weapon():
    """`1x Boss Nob` and `1x Power klaw` are the same shape. What tells them
    apart is that wargear hangs off a model, so a block that nests has models
    at the top and weapons underneath."""
    parsed = list_parse.parse("""BATTLELINE

Boyz (180 Points)
  • 19x Ork Boy
     ◦ 19x Choppa
  • 1x Boss Nob
     ◦ 1x Power klaw
""")
    assert counts(parsed) == {'Boyz': 20}


def test_a_single_model_character_does_not_count_its_wargear():
    """The failure that would send Clay shopping: three bullets under a
    Warboss are his squig and his weapons, not three Warbosses."""
    parsed = list_parse.parse("""CHARACTERS

Warboss (65 Points)
  • 1x Attack squig
  • 1x Kombi-weapon
  • 1x Power klaw
""")
    assert counts(parsed) == {'Warboss': 1}


def test_a_flat_multi_model_block_undercounts_and_that_is_the_safe_direction():
    """KNOWN FALSE NEGATIVE, pinned deliberately.

    Models listed with no wargear under them are indistinguishable from one
    model's kit, so this reads 20 Boyz as 1. It is wrong, and it is wrong in
    the direction that costs nothing: "you need 1 Boy" is visibly odd, while
    "you need 3 Warbosses" sends him to a shop for two he already has.

    Replace `synthetic_newrecruit_flat.txt` with a real export and this test is
    the one to check first — if New Recruit really does write flat blocks, the
    fix is to clamp the count with the resolved datasheet's `min_models`, which
    the rules data already carries.
    """
    parsed = list_parse.parse(sample('synthetic_newrecruit_flat.txt'))
    assert counts(parsed) == {'Boyz': 1, 'Gretchin': 1}


def test_every_fixture_parses_without_raising():
    """Whatever ends up in that directory, including a real export dropped in
    to replace a synthetic one."""
    for name in sorted(os.listdir(FIXTURES)):
        if name.endswith('.txt'):
            list_parse.parse(sample(name))


def test_the_fixtures_say_they_are_synthetic():
    """The one thing that must not rot: a synthetic sample quietly becoming
    'the format', so a parser bug looks like correct behaviour."""
    readme = sample('README.md')
    assert 'SYNTHETIC' in readme
    assert any(n.startswith('synthetic_') for n in os.listdir(FIXTURES))
