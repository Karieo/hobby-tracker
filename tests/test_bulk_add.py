"""Paste a shelf in, one line per unit.

Scanning is no door at all for models already built and painted — there is
nothing left to scan, and those are the ones most likely missing from the app.
This is the door for them.

The rules under test: forgiving about shape, unforgiving about names. A line
either resolves to a real datasheet or comes back for a decision. Spec §12 —
never invent a datasheet, never drop a line.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bulk_add
import collection as col
import database as db


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / 'bulk.db')
    db.migrate(path)
    c = db.connect(path)
    yield c
    c.close()


@pytest.fixture
def stages(conn):
    return {s['name']: s['id'] for s in col.stage_ladder(conn)}


@pytest.fixture
def sheets(conn):
    orks = db.upsert_faction(conn, 'Orks', 'orks')
    marines = db.upsert_faction(conn, 'Space Marines', 'space-marines')
    ids = {}
    for name, faction, system in (('Boyz', orks, 'wh40k'),
                                  ('Nobz', orks, 'wh40k'),
                                  ('Trukk', orks, 'wh40k'),
                                  ("Ghazghkull Thraka", orks, 'wh40k'),
                                  ('Boyz', orks, 'killteam'),
                                  ('Rhino', marines, 'wh40k')):
        ids.setdefault(f'{name}:{system}', conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'game_system, min_models, max_models, created_at, updated_at) '
            'VALUES (?,?,?,1,?,10,20,?,?)',
            (f'{system}:{name}', name, faction, system, db.now(), db.now())
        ).lastrowid)
    return ids


# ── Reading what people actually write ───────────────────

@pytest.mark.parametrize('line, name, count', [
    ('20 Boyz', 'Boyz', 20),
    ('Boyz x20', 'Boyz', 20),
    ('Boyz X20', 'Boyz', 20),
    ('20x Boyz', 'Boyz', 20),
    ('20 x Boyz', 'Boyz', 20),
    ('Trukk', 'Trukk', 1),
    ('  3  Nobz  ', 'Nobz', 3),
    ('- 5 Nobz', 'Nobz', 5),
    ('• Trukk', 'Trukk', 1),
])
def test_counts_lead_or_trail_or_are_absent(line, name, count):
    row = bulk_add.parse_lines(line)[0]
    assert (row['name'], row['count']) == (name, count)


@pytest.mark.parametrize('line, stage', [
    ('20 Boyz built', 'built'),
    ('Trukk primed', 'primed'),
    ('Nobz painted', 'painted'),
    ('Rhino done', 'done'),
    ('5 Nobz sealed', 'sealed'),
])
def test_a_trailing_stage_word_is_read(line, stage):
    assert bulk_add.parse_lines(line)[0]['stage_word'] == stage


def test_a_line_with_no_stage_word_says_so(sheets):
    assert bulk_add.parse_lines('20 Boyz')[0]['stage_word'] is None


def test_only_the_last_word_can_be_a_stage():
    """A stage word inside a name must not eat part of the name."""
    row = bulk_add.parse_lines('Ghazghkull Thraka')[0]
    assert row['name'] == 'Ghazghkull Thraka'


def test_blank_lines_and_bullets_are_not_errors():
    rows = bulk_add.parse_lines('20 Boyz\n\n   \n- Trukk\n')
    assert [r['name'] for r in rows] == ['Boyz', 'Trukk']


def test_a_count_of_zero_is_treated_as_one():
    assert bulk_add.parse_lines('0 Boyz')[0]['count'] == 1


# ── Matching: exact, or a decision ───────────────────────

def test_an_exact_name_matches(conn, sheets):
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('20 Trukk built'))
    assert rows[0]['datasheet_id'] == sheets['Trukk:wh40k']
    assert rows[0]['why'] is None


def test_case_and_punctuation_do_not_matter(conn, sheets):
    """The same fold the rules-data importer uses."""
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('3   trukk'))
    assert rows[0]['datasheet_id'] == sheets['Trukk:wh40k']


def test_a_name_in_two_game_systems_is_never_picked_silently(conn, sheets):
    """Boyz exists in 40k and in Kill Team. Choosing one because it sorted
    first is exactly the silent corruption the importers refuse to do."""
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('20 Boyz'))

    assert rows[0]['datasheet_id'] is None
    assert 'more than one datasheet' in rows[0]['why']
    assert len(rows[0]['candidates']) == 2


def test_scoping_to_a_game_system_resolves_it(conn, sheets):
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('20 Boyz'),
                                game_system='wh40k')
    assert rows[0]['datasheet_id'] == sheets['Boyz:wh40k']


def test_a_scope_that_would_match_nothing_falls_back(conn, sheets):
    """A Kill Team name in a 40k-scoped paste still resolves rather than
    failing for a reason Clay cannot see."""
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('Rhino'),
                                game_system='killteam')
    assert rows[0]['datasheet_id'] == sheets['Rhino:wh40k']


def test_a_typo_still_offers_candidates(conn, sheets):
    """The case that actually needs the picker. A SQL LIKE finds nothing for
    an extra letter, and an empty picker on a line Clay must resolve is a dead
    end."""
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('20 Boyzz'))

    assert rows[0]['datasheet_id'] is None
    assert rows[0]['why'] == 'no datasheet with this name'
    assert any(c['name'] == 'Boyz' for c in rows[0]['candidates'])


def test_half_remembering_a_long_name_offers_candidates(conn, sheets):
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('Ghazghkull'))
    assert any(c['name'] == 'Ghazghkull Thraka'
               for c in rows[0]['candidates'] or [])


def test_a_name_resembling_nothing_offers_an_empty_picker(conn, sheets):
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('Zzzqqq'))
    assert rows[0]['candidates'] == []


def test_nothing_is_dropped(conn, sheets):
    """Every pasted line comes back, matched or not."""
    rows = bulk_add.match_lines(
        conn, bulk_add.parse_lines('20 Boyzz\nTrukk\n5 Nothing Real'))
    assert len(rows) == 3


# ── Committing ───────────────────────────────────────────

def test_committing_creates_a_unit_per_line(conn, sheets, stages):
    rows = bulk_add.match_lines(
        conn, bulk_add.parse_lines('20 Trukk built\n5 Nobz'), game_system='wh40k')

    created = bulk_add.commit(conn, rows)

    assert len(created) == 2
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 25


def test_a_stage_word_decides_the_stage(conn, sheets, stages):
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('Trukk primed'))
    bulk_add.commit(conn, rows)

    at = conn.execute('SELECT stage_id FROM models').fetchone()['stage_id']
    assert at == stages['Primed']


def test_a_line_with_no_stage_word_takes_the_batch_default(conn, sheets, stages):
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('Trukk'))
    bulk_add.commit(conn, rows, default_stage_id=stages['Assembled'])

    at = conn.execute('SELECT stage_id FROM models').fetchone()['stage_id']
    assert at == stages['Assembled']


def test_an_unresolved_line_blocks_the_whole_commit(conn, sheets):
    """A line Clay pasted never vanishes without him saying so."""
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('Trukk\n5 Nothing Real'))

    with pytest.raises(ValueError, match='still need a datasheet'):
        bulk_add.commit(conn, rows)
    assert conn.execute('SELECT COUNT(*) FROM units').fetchone()[0] == 0


def test_skipping_an_unresolved_line_lets_the_rest_through(conn, sheets):
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('Trukk\n5 Nothing Real'))
    rows[1]['skip'] = True

    created = bulk_add.commit(conn, rows)

    assert len(created) == 1
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 1


def test_a_picked_datasheet_resolves_a_line(conn, sheets, stages):
    """What the per-line picker does: Clay chooses, and it commits."""
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('20 Boyz'))
    assert rows[0]['datasheet_id'] is None

    rows[0]['datasheet_id'] = sheets['Boyz:wh40k']

    assert len(bulk_add.commit(conn, rows)) == 1
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 20


def test_committed_units_can_be_assigned_to_an_army(conn, sheets):
    army = col.create_army(conn, 'Da Boyz')
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('Trukk'))

    bulk_add.commit(conn, rows, army_id=army)

    assert conn.execute('SELECT army_id FROM units').fetchone()['army_id'] == army


def test_committing_nothing_is_not_an_error(conn, sheets):
    assert bulk_add.commit(conn, []) == []


# ── List shapes ──────────────────────────────────────────
#
# Spec §2.7. A list arrives as text far more often than as a file, and every
# app writes its points differently.

def test_points_in_parentheses_are_read_and_set_aside(conn, sheets):
    row = bulk_add.parse_lines('10x Intercessor Squad (200)')[0]
    assert row['name'] == 'Intercessor Squad'
    assert row['count'] == 10
    assert row['points_hint'] == 200


def test_every_shape_of_points_annotation(conn):
    for text, name in (
        ('1x Captain (95)', 'Captain'),
        ('10x Intercessor Squad [200pts]', 'Intercessor Squad'),
        ('5x Assault Terminators - 185 pts', 'Assault Terminators'),
        ('Deffkoptas: 105', 'Deffkoptas'),
        ('Warboss on Warbike 90pts', 'Warboss on Warbike'),
    ):
        assert bulk_add.parse_lines(text)[0]['name'] == name, text


def test_a_bare_trailing_number_is_a_count_not_points(conn):
    """"Boyz x20" ends in a count. Reading it as points loses the count and
    leaves a unit called "Boyz x"."""
    row = bulk_add.parse_lines('Boyz x20')[0]
    assert (row['name'], row['count']) == ('Boyz', 20)
    assert row['points_hint'] is None


def test_section_headings_are_skipped_not_reported(conn):
    """A screen full of "no datasheet named + HQ +" buries the lines that
    genuinely need a decision."""
    rows = bulk_add.parse_lines(
        '+ HQ +\n1x Warboss\n++ Battleline ++\n20x Boyz\nTotal: 645pts')

    assert [r['name'] for r in rows] == ['Warboss', 'Boyz']


def test_the_collection_paste_still_parses_as_before(conn):
    """The list shapes must not cost the shapes /add was built for."""
    rows = bulk_add.parse_lines('20 Boyz built\nTrukk primed\nBoyz x20\n5 Nobz')

    assert [(r['name'], r['count'], r['stage_word']) for r in rows] == [
        ('Boyz', 20, 'built'), ('Trukk', 1, 'primed'),
        ('Boyz', 20, None), ('Nobz', 5, None)]


# ── A pasted list becomes a list ─────────────────────────

def test_a_pasted_list_becomes_entries_not_owned_models(conn, sheets):
    """A list says what Clay wants to field; the collection says what he has.
    The gap between them is what the app is for."""
    import lists
    rows = bulk_add.match_lines(
        conn, bulk_add.parse_lines('20x Boyz (200)\n5x Nobz [125pts]'),
        game_system='wh40k')

    result = bulk_add.commit_as_list(conn, rows, 'Saturday')

    assert len(result['entries']) == 2
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 0, \
        'importing a list owns nothing'
    assert lists.get_list(conn, result['list_id'])['name'] == 'Saturday'


def test_the_pasted_points_are_not_stored(conn, sheets):
    """This app prices a list from the Munitorum manual it imported. A number
    copied out of another app is at best a duplicate and at worst stale from a
    previous edition, and it would outrank the official one in every total."""
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('20x Boyz (9999)'),
                                game_system='wh40k')

    result = bulk_add.commit_as_list(conn, rows, 'Saturday')

    snapshot = conn.execute(
        'SELECT points_snapshot FROM list_entries WHERE list_id = ?',
        (result['list_id'],)).fetchone()['points_snapshot']
    assert snapshot != 9999


def test_a_skipped_line_is_left_out(conn, sheets):
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('20x Boyz\n5x Nobz'),
                                game_system='wh40k')
    rows[1]['skip'] = True

    result = bulk_add.commit_as_list(conn, rows, 'Saturday')

    assert len(result['entries']) == 1


def test_importing_nothing_is_refused(conn, sheets):
    """Rather than creating an empty list that looks like it worked."""
    with pytest.raises(ValueError, match='nothing to import'):
        bulk_add.commit_as_list(conn, [], 'Saturday')


def test_the_imported_list_reaches_the_gap_report(conn, sheets):
    """The loop closing: a pasted list immediately says what to buy."""
    import lists
    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('20x Boyz'),
                                game_system='wh40k')
    result = bulk_add.commit_as_list(conn, rows, 'Saturday')

    gap = lists.list_gap(conn, result['list_id'])

    assert gap['to_buy'] == 20, 'owns none of them yet'


# ── Suggestions put the likeliest first ──────────────────

def test_a_one_letter_typo_suggests_the_right_unit_first(conn, sheets):
    """This used to bucket by a shared four-letter prefix and sort the bucket
    alphabetically, so "Killa Kanz" suggested Kill Krusha, Kill Rig and Kill
    Tank — and not Killa Kans. Someone tapping the first suggestion in a hurry
    got the wrong datasheet."""
    faction_id = db.get_faction_by_slug(conn, 'orks')['id']
    for name in ('Killa Kans', 'Kill Rig', 'Kill Tank', 'Kill Krusha'):
        conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'created_at, updated_at) VALUES (?,?,?,1,?,?)',
            (name.lower(), name, faction_id, db.now(), db.now()))

    rows = bulk_add.match_lines(conn, bulk_add.parse_lines('3x Killa Kanz'))

    assert rows[0]['datasheet_id'] is None, 'a typo is not a match'
    assert rows[0]['candidates'][0]['name'] == 'Killa Kans'


# ── Two shapes of paste, one door ────────────────────────
#
# Clay: "I want to be able to paste in a list and it reconcile against the
# datasheets and add." An app's export is the other thing people paste, and the
# shelf grammar mangles it — measured below, because the count is the argument.

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'fixtures', 'lists')


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as handle:
        return handle.read()


def test_the_shelf_parser_really_does_mangle_an_export():
    """The measurement the feature exists for, pinned so it cannot quietly
    stop being true. Seven of fifteen rows are the list's name, its faction,
    its battle size, its detachment and three section headings — every one
    offered to Clay as a unit needing a datasheet, which is exactly how he
    would learn to ignore the unresolved rows."""
    rows = bulk_add.parse_lines(fixture('synthetic_gwapp_orks.txt'))
    names = [r['name'] for r in rows]

    assert 'Da Green Tide' in names and 'CHARACTERS' in names
    assert len(rows) == 15


def test_an_export_comes_back_as_its_units_and_nothing_else():
    fmt, rows = bulk_add.parse_paste(fixture('synthetic_gwapp_orks.txt'))

    assert fmt == 'gw_app'
    assert [(r['count'], r['name']) for r in rows] == [
        (1, 'Warboss'), (1, 'Painboss'), (20, 'Boyz'), (20, 'Boyz'),
        (10, 'Gretchin'), (3, 'Killa Kans'), (3, 'Deffkoptas'), (1, 'Trukk')]


def test_the_other_export_format_is_read_too():
    fmt, rows = bulk_add.parse_paste(fixture('synthetic_newrecruit_orks.txt'))

    assert fmt == 'newrecruit'
    assert (20, 'Boyz') in [(r['count'], r['name']) for r in rows]


def test_a_shelf_typed_from_memory_still_gets_the_shelf_parser():
    """The two parsers are not merged, and this is the half that would be lost:
    stage words exist only in the grammar people type, never in an export."""
    fmt, rows = bulk_add.parse_paste('20 Boyz built\n3 Meganobz primed\nTrukk')

    assert fmt == 'unknown'
    assert [(r['count'], r['name'], r['stage_word']) for r in rows] == [
        (20, 'Boyz', 'built'), (3, 'Meganobz', 'primed'), (1, 'Trukk', None)]


def test_a_retyped_sheet_is_not_mistaken_for_an_export():
    """"Boyz x20 - 180" is someone retyping a list by hand. It carries no
    format markers and must not be detected as an export on the strength of
    looking list-shaped — the preview would then tell Clay it read his own
    typing as "the GW app", which is simply untrue.

    This cannot test the *routing*: measured, both parsers return the same
    eight rows for this input, so sending it either way looks identical. What
    it pins is the detection, and the label that follows from it. The stage-word
    test above is the one that catches a mis-route.
    """
    fmt, rows = bulk_add.parse_paste(fixture('unknown_retyped_sheet.txt'))

    assert fmt == 'unknown'
    assert (20, 'Boyz') in [(r['count'], r['name']) for r in rows]


def test_an_export_carries_no_stage_words(conn, sheets):
    """So every model from one takes the batch default — the honest reading of
    pasting a list here: "I own all of this, and it is all at about here.\""""
    _, rows = bulk_add.parse_paste(fixture('synthetic_gwapp_orks.txt'))

    assert all(r['stage_word'] is None for r in rows)


def test_an_exported_list_resolves_against_the_datasheets(conn, sheets):
    """The whole ask, end to end: paste an export, and the units in it come
    back matched to real datasheets rather than as a screen of unknowns."""
    _, parsed = bulk_add.parse_paste(
        '10x Boyz [90pts]\n1x Trukk [70pts]\n3x Nobz [105pts]')
    rows = bulk_add.match_lines(conn, parsed, game_system='wh40k')

    assert [r['datasheet_name'] for r in rows] == ['Boyz', 'Trukk', 'Nobz']
    assert [r['count'] for r in rows] == [10, 1, 3]


def test_nothing_readable_is_still_nothing(conn):
    fmt, rows = bulk_add.parse_paste('')

    assert rows == []
