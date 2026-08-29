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
    """KNOWN FALSE NEGATIVE, pinned deliberately — but no longer "free".

    Where every bullet in a document is counted, a model and a weapon are the
    same shape, so this reads 20 Boyz as 1. It stays 1 because it is the only
    honest answer when the text genuinely does not say.

    **The old justification here was that it costs nothing**, and that was
    argued for the gap report, where "you need 1 Boy" is visibly odd and the
    opposite error sends Clay shopping for Warbosses he owns. It does not hold
    for `/add`, which *writes* the models: an undercount there silently records
    a collection as smaller than it is, with nothing on any screen to show for
    it. Measured on Clay's real list: 92 models would have been recorded as 20.

    A flat list did arrive on 2026-08-27 and this test was indeed the one to
    check first — but it was model-written text Clay pasted, not an export, so
    it did not make the flat case answerable either. It uses a different
    convention, where wargear carries no count, and `_uncounted_wargear` reads
    that convention off the document. Where it is absent, this case is still
    unanswerable.

    Clamping with the resolved datasheet's `min_models` remains the idea worth
    trying, and still needs a verified New Recruit export to justify. This repo
    has never seen one.
    """
    parsed = list_parse.parse(sample('synthetic_newrecruit_flat.txt'))
    assert counts(parsed) == {'Boyz': 1, 'Gretchin': 1}


def test_every_fixture_parses_without_raising():
    """Whatever ends up in that directory, including a real export dropped in
    to replace a synthetic one."""
    for name in sorted(os.listdir(FIXTURES)):
        if name.endswith('.txt'):
            list_parse.parse(sample(name))


def test_the_readme_says_which_samples_are_invented():
    """The one thing that must not rot: a synthetic sample quietly becoming
    "the format", so a parser bug looks like correct behaviour.

    This used to assert the README shouted SYNTHETIC about the whole directory,
    The wording changed twice: once when a paste was mistaken for a real export,
    and once when that was corrected. The invariant never moved — every sample
    here is invented and has to say so — so what is asserted is that the README
    labels them, not any particular sentence.
    """
    readme = sample('README.md')
    invented = [n for n in os.listdir(FIXTURES) if n.startswith('synthetic_')]

    assert invented, 'the synthetic samples are still what most of this is'
    assert 'synthetic' in readme.lower()
    assert 'real' in readme.lower(), 'and it has to say which ones are not'


def test_a_sample_without_the_prefix_is_documented():
    """The prefix is the whole labelling scheme, so nothing may sit in here
    unlabelled. A file that is neither `synthetic_` nor `unknown_` needs its
    provenance written down — which is how a model-written paste came to be
    filed as a real export for two days."""
    readme = sample('README.md')
    for name in sorted(os.listdir(FIXTURES)):
        if not name.endswith('.txt'):
            continue
        if name.startswith(('synthetic_', 'unknown_')):
            continue
        assert name in readme, f'{name} has no provenance in the README'


# ── The list Clay pasted ─────────────────────────────────
#
# Pasted on 2026-08-27 with "Here is the format", and filed as this repo's
# first real export. It is not one — he said next: "I pasted from Claude trying
# to make a list." Model-written text, believed because it arrived through a
# paste instead of a seed file.
#
# It stays, because it is a real *input*: Clay pastes model-written lists into
# this app and the parser has to read them. It is not evidence about any app's
# format, and these tests do not claim it is.

def _pasted_orks():
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'fixtures', 'lists', 'pasted_orks_2000.txt')
    with open(path) as handle:
        return handle.read()


def test_the_pasted_list_counts_its_models():
    """Twenty units, ninety-two models. Every unit read as 1 before the
    document-level wargear rule, because the list is flat from top to bottom.

    A regression fixture for what Clay actually pastes, not a specimen of any
    app's export format."""
    parsed = list_parse.parse(_pasted_orks())
    counts = {}
    for entry in parsed.entries:
        counts.setdefault(entry.raw_name, []).append(entry.model_count)

    assert len(parsed.entries) == 20
    assert sum(e.model_count for e in parsed.entries) == 92
    assert counts['Boyz'] == [10, 10]
    assert counts['Trukk'] == [1, 1, 1], 'no bullets at all is one model'
    assert counts['Ghazghkull Thraka'] == [1]


def test_a_wargear_bullet_is_not_a_model():
    """`Flash Gitz` has "5x Flash Git" and "Supa Snazz-Dakka" under it. Five
    models, and the uncounted line is a gun."""
    parsed = list_parse.parse(_pasted_orks())
    gitz = [e for e in parsed.entries if e.raw_name == 'Flash Gitz']

    assert [e.model_count for e in gitz] == [5, 5]


def test_a_character_with_only_wargear_is_one_model():
    """`Beastboss` carries "• Kaptin's Hat" and nothing else."""
    parsed = list_parse.parse(_pasted_orks())
    boss = [e for e in parsed.entries if e.raw_name == 'Beastboss']

    assert [e.model_count for e in boss] == [1]


def test_the_preamble_and_the_headings_are_not_units():
    """Five preamble lines and four section headings in this one, including
    "Priority Assets" and "DEDICATED TRANSPORTS"."""
    names = [e.raw_name for e in list_parse.parse(_pasted_orks()).entries]

    for junk in ('Da Wrecka Krew', 'Orks', 'Strike Force', 'Priority Assets',
                 'Wreckas + Shoota Boyz + Da Big Hunt', 'CHARACTERS',
                 'DEDICATED TRANSPORTS', 'Total: 1980 points'):
        assert junk not in names, f'{junk!r} is not a unit'


def test_new_recruits_flat_ambiguity_is_untouched():
    """Where every bullet is counted there is nothing to tell a model from a
    weapon, and reading 1 stays the safe answer. Changing this would make the
    fix a guess rather than a reading."""
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'fixtures', 'lists', 'synthetic_newrecruit_flat.txt')
    with open(path) as handle:
        parsed = list_parse.parse(handle.read())

    assert [e.model_count for e in parsed.entries] == [1, 1]


def test_the_signal_is_read_per_document_not_per_block():
    """"Boyz / • 10x Ork Boy" is identical in both conventions. Only the rest
    of the file says which one it is written in, so a lone block must not
    decide for itself."""
    alone = "Boyz (75 points)\n  • 10x Ork Boy\n"
    with_wargear = alone + "Flash Gitz (170 points)\n  • 5x Flash Git\n  • Supa Snazz-Dakka\n"

    assert [e.model_count for e in list_parse.parse(alone).entries] == [1]
    assert [e.model_count for e in list_parse.parse(with_wargear).entries] == [10, 5]
