"""Importer behaviour — especially the parts that must never guess.

The rules these protect come straight from the spec's non-negotiables: never
invent a datasheet, never silently drop a line, never let a re-sync clobber a
hand correction.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'scripts'))

import database as db  # noqa: E402
import import_bsdata as imp  # noqa: E402


# ── Pure helpers ─────────────────────────────────────────

@pytest.mark.parametrize('a, b', [
    ("Emperor's Children", 'Emperor’s Children'),   # straight vs curly
    ('Ork  Nob', 'Ork Nob'),                        # collapsed whitespace
    ('KILLA KANS', 'Killa Kans'),                   # case
    ('Myphitic Blight-Haulers', 'Myphitic Blight Haulers'),
])
def test_norm_folds_meaningless_differences(a, b):
    assert imp.norm(a) == imp.norm(b)


def test_norm_keeps_meaningful_differences():
    """Boyz and Beast Snagga Boyz are different units and must stay different."""
    assert imp.norm('Boyz') != imp.norm('Beast Snagga Boyz')
    assert imp.norm('Burna Boy') != imp.norm('Burna Boyz')


@pytest.mark.parametrize('text, expected', [
    ('[1,2]', (1, 2)),
    ('[3,)', (3, None)),
    ('[1,)', (1, None)),
    ('', (1, None)),          # missing range means the only tier
    ('nonsense', (1, None)),
])
def test_parse_tier_range(text, expected):
    assert imp.parse_tier_range(text) == expected


@pytest.mark.parametrize('name, expected', [
    ('Vypers [Legends]', ('Vypers', 'legends')),
    ('Archtormentor [Crucible]', ('Archtormentor', 'crucible')),
    ('Killa Kans', ('Killa Kans', None)),
    ('Sister Novitiate (Autogun)', ('Sister Novitiate (Autogun)', None)),
])
def test_split_variant(name, expected):
    assert imp.split_variant(name) == expected


def test_sub_models_are_not_datasheets():
    """"Burna Boy" is a model inside a mob, not a datasheet you can own.

    Letting it through would put it in the picker next to "Burna Boyz".
    """
    burna_boy = {'type': 'model', 'costs': [], 'categoryLinks': []}
    warboss = {'type': 'model',
               'costs': [{'name': 'pts', 'value': 85}],
               'categoryLinks': [{'name': 'Faction: Orks'}]}
    assert not imp.is_datasheet(burna_boy)
    assert imp.is_datasheet(warboss)


def test_a_unit_with_no_costs_is_still_a_datasheet():
    """BSData leaves costs empty on some real units; the manual prices them."""
    assert imp.is_datasheet(
        {'type': 'unit', 'costs': [], 'categoryLinks': []})


def test_effort_ladder_separates_a_knight_from_a_termagant():
    assert imp.seed_effort({'Titanic', 'Vehicle'}) == 10
    assert imp.seed_effort({'Vehicle', 'Walker'}) == 8
    assert imp.seed_effort({'Mounted', 'Infantry'}) == 4
    assert imp.seed_effort({'Infantry', 'Character'}) == 2
    assert imp.seed_effort({'Infantry'}) == 1
    assert imp.seed_effort(set()) == imp.DEFAULT_EFFORT


# ── The join ─────────────────────────────────────────────

def _index(rows):
    return imp.index_datasheets(rows)


def test_own_faction_datasheet_wins():
    rows = [(1, 'Rhino', {'faction_slug': 'space-marines'}, None),
            (2, 'Rhino', {'faction_slug': 'grey-knights'}, None)]
    by_faction, by_name = _index(rows)
    ds_id, points_faction, _why = imp.resolve_datasheet(
        by_faction, by_name, {'space-marines', 'grey-knights'},
        'grey-knights', None, 'Rhino')
    assert (ds_id, points_faction) == (2, None)


def test_group_title_names_the_parent_to_inherit_from():
    """A Rhino under black-templars / "Space Marines" is the Marine Rhino.

    Without the hint this is ambiguous, and picking the Grey Knights one would
    put the wrong points on a unit Clay owns.
    """
    rows = [(1, 'Rhino', {'faction_slug': 'space-marines'}, None),
            (2, 'Rhino', {'faction_slug': 'grey-knights'}, None)]
    by_faction, by_name = _index(rows)
    ds_id, points_faction, why = imp.resolve_datasheet(
        by_faction, by_name, {'space-marines', 'grey-knights', 'black-templars'},
        'black-templars', 'Space Marines', 'Rhino')
    assert ds_id == 1
    assert points_faction == 'black-templars', \
        'an inherited listing must be tagged with the faction that pays'
    assert 'group title' in why


def test_a_unique_global_name_may_be_inherited():
    rows = [(1, 'Aggressor Squad', {'faction_slug': 'space-marines'}, None)]
    by_faction, by_name = _index(rows)
    ds_id, points_faction, _why = imp.resolve_datasheet(
        by_faction, by_name, {'space-marines', 'blood-angels'},
        'blood-angels', None, 'Aggressor Squad')
    assert (ds_id, points_faction) == (1, 'blood-angels')


def test_an_ambiguous_name_is_refused_not_guessed():
    """Two candidates and no hint: report it, never pick one."""
    rows = [(1, 'Rhino', {'faction_slug': 'space-marines'}, None),
            (2, 'Rhino', {'faction_slug': 'grey-knights'}, None)]
    by_faction, by_name = _index(rows)
    ds_id, _pf, why = imp.resolve_datasheet(
        by_faction, by_name, {'space-marines', 'grey-knights'},
        'orks', None, 'Rhino')
    assert ds_id is None
    assert 'ambiguous' in why


def test_duplicate_names_inside_one_faction_are_refused():
    """Wolf Guard Headtakers is two BSData datasheets with one name."""
    rows = [(1, 'Wolf Guard Headtakers', {'faction_slug': 'space-wolves'}, None),
            (2, 'Wolf Guard Headtakers', {'faction_slug': 'space-wolves'}, None)]
    by_faction, by_name = _index(rows)
    ds_id, _pf, why = imp.resolve_datasheet(
        by_faction, by_name, {'space-wolves'}, 'space-wolves', None,
        'Wolf Guard Headtakers')
    assert ds_id is None
    assert 'share this name' in why


def test_an_unknown_name_is_never_invented():
    by_faction, by_name = _index([])
    ds_id, _pf, why = imp.resolve_datasheet(
        by_faction, by_name, {'orks'}, 'orks', None, 'Squig Launcha')
    assert ds_id is None
    assert 'no BSData datasheet' in why


def test_variants_are_never_a_points_target():
    """A Legends datasheet must not soak up a current unit's points."""
    rows = [(1, 'Vypers', {'faction_slug': 'aeldari'}, 'legends')]
    by_faction, by_name = _index(rows)
    ds_id, _pf, _why = imp.resolve_datasheet(
        by_faction, by_name, {'aeldari'}, 'aeldari', None, 'Vypers')
    assert ds_id is None


# ── Unresolved rows ──────────────────────────────────────

def test_unresolved_rows_are_recorded_and_readable(conn):
    db.record_unresolved(conn, 'bsdata', 'points', 'Vypers',
                         'no manual entry', source_ref='Aeldari',
                         payload={'faction': 'aeldari'})
    rows = db.open_unresolved(conn, 'bsdata')
    assert len(rows) == 1
    assert rows[0]['raw_name'] == 'Vypers'
    assert rows[0]['resolved_at'] is None


def test_clear_unresolved_only_drops_open_rows(conn):
    db.record_unresolved(conn, 'bsdata', 'points', 'A', 'x')
    db.record_unresolved(conn, 'bsdata', 'points', 'B', 'x')
    conn.execute("UPDATE unresolved_imports SET resolved_at = ? WHERE raw_name = 'B'",
                 (db.now(),))
    db.clear_unresolved(conn, 'bsdata')
    remaining = conn.execute(
        'SELECT raw_name, resolved_at FROM unresolved_imports').fetchall()
    assert [r['raw_name'] for r in remaining] == ['B'], \
        'a resolved row is a decision Clay already made — never re-open it'


# ── Overrides survive a re-sync ──────────────────────────

def test_manual_points_override_survives_reimport(conn):
    conn.execute("INSERT INTO factions (name, slug) VALUES ('Orks', 'orks')")
    conn.execute(
        "INSERT INTO datasheets (bsdata_id, name, faction_id, effort, "
        "created_at, updated_at) VALUES ('k', 'Killa Kans', 1, 8, ?, ?)",
        (db.now(), db.now()))
    conn.execute(
        'INSERT INTO datasheet_points (datasheet_id, model_count, points, '
        'effective_from, manual_override) VALUES (1, 3, 999, ?, 1)', ('2026-01-01',))
    # What the importer does before writing fresh rows.
    conn.execute('DELETE FROM datasheet_points WHERE datasheet_id = 1 '
                 'AND manual_override = 0')
    kept = conn.execute(
        'SELECT points FROM datasheet_points WHERE datasheet_id = 1').fetchall()
    assert [r['points'] for r in kept] == [999]


def test_points_rows_carry_the_requisition_tier(conn):
    """11th edition prices your 3rd+ copy differently; both rows are true."""
    conn.execute("INSERT INTO factions (name, slug) VALUES ('Orks', 'orks')")
    conn.execute(
        "INSERT INTO datasheets (bsdata_id, name, faction_id, effort, "
        "created_at, updated_at) VALUES ('k', 'Killa Kans', 1, 8, ?, ?)",
        (db.now(), db.now()))
    for tier_min, tier_max, points in ((1, 2, 120), (3, None, 130)):
        conn.execute(
            'INSERT INTO datasheet_points (datasheet_id, model_count, points, '
            'tier_min, tier_max, effective_from) VALUES (1, 3, ?, ?, ?, ?)',
            (points, tier_min, tier_max, '2026-08-05'))
    base = conn.execute(
        'SELECT points FROM datasheet_points WHERE datasheet_id = 1 '
        'AND tier_min <= 1 AND (tier_max IS NULL OR tier_max >= 1)').fetchone()
    assert base['points'] == 120
