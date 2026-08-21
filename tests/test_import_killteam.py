"""Kill Team operatives: the import, and the two ways it silently does nothing.

Both failures pinned here were found by running the importer against the real
127 catalogues, not by reading the code. Either one leaves Clay exactly where he
started — holding a box the app cannot record — with nothing on screen to say
why.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))

import collection as col
import database as db
import import_killteam as kt


CATALOGUE = '''<?xml version="1.0" encoding="utf-8"?>
<catalogue xmlns="http://www.battlescribe.net/schema/catalogueSchema"
           id="cat-{cid}" name="{team}">
  <selectionEntries>
    <selectionEntry id="aaaa-1111" name="Skitarii Ranger Gunner" type="model"/>
    <selectionEntry id="bbbb-2222" name="Skitarii Ranger Alpha" type="model"/>
    <selectionEntry id="cccc-3333" name="Plasma caliver" type="upgrade"/>
  </selectionEntries>
</catalogue>
'''


@pytest.fixture
def conn():
    path = os.path.join(tempfile.mkdtemp(), 'kt.db')
    db.migrate(path)
    c = db.connect(path)
    yield c
    c.close()


@pytest.fixture
def catalogues(tmp_path):
    def write(filename, team, body=None):
        (tmp_path / filename).write_text(
            body if body is not None
            else CATALOGUE.format(cid=filename[:4], team=team))
    return type('Dir', (), {'path': str(tmp_path), 'write': staticmethod(write)})


# ── What comes across ────────────────────────────────────

def test_only_models_become_datasheets(conn, catalogues):
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')
    report = kt.import_all(conn, directory=catalogues.path)

    assert report['inserted'] == 2, 'the upgrade is a weapon, not a miniature'
    names = {r[0] for r in conn.execute('SELECT name FROM datasheets')}
    assert names == {'Skitarii Ranger Gunner', 'Skitarii Ranger Alpha'}


def test_operatives_are_one_model_each(conn, catalogues):
    """The contents form pre-fills the count from min_models, so 1 here is what
    makes picking an operative fill in a usable number."""
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')
    kt.import_all(conn, directory=catalogues.path)

    row = conn.execute('SELECT min_models, max_models FROM datasheets').fetchone()
    assert (row['min_models'], row['max_models']) == (1, 1)


def test_the_game_system_is_recorded(conn, catalogues):
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')
    kt.import_all(conn, directory=catalogues.path)
    systems = {r[0] for r in conn.execute('SELECT game_system FROM datasheets')}
    assert systems == {'killteam'}


def test_edition_comes_from_the_filename(conn, catalogues):
    catalogues.write('2021 - Hunter Clade.cat', 'Hunter Clade')
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')
    catalogues.write('Adeptus Mechanicus.cat', 'Adeptus Mechanicus')

    kt.import_all(conn, directory=catalogues.path)

    editions = {r[0] for r in conn.execute('SELECT variant FROM datasheets')}
    assert editions == {'2018', '2021', '2024'}


def test_kill_team_factions_cannot_collide_with_40k_ones(conn, catalogues):
    """"Orks" exists in both games and they are not the same list."""
    db.upsert_faction(conn, 'Orks', 'orks')
    catalogues.write('2024 - Orks.cat', 'Orks')

    kt.import_all(conn, directory=catalogues.path)

    slugs = {r[0] for r in conn.execute('SELECT slug FROM factions')}
    assert slugs == {'orks', 'kt-orks'}


# ── The first silent failure: collapsed operatives ───────

def test_the_same_entry_id_in_two_teams_keeps_both(conn, catalogues):
    """BSData reuses entry ids across catalogues — 20 of them in the real data.

    Keyed on the bare id, the second catalogue overwrites the first and a team
    quietly loses an operative, which Clay discovers holding the box.
    """
    catalogues.write('2021 - Hunter Clade.cat', 'Hunter Clade')
    catalogues.write('2021 - Forge World (Legends).cat', 'Forge World (Legends)')

    report = kt.import_all(conn, directory=catalogues.path)

    assert report['inserted'] == 4, 'two operatives, in each of two teams'
    assert report['updated'] == 0, 'nothing should have been overwritten'


def test_the_same_entry_id_in_two_editions_keeps_both(conn, catalogues):
    catalogues.write('2021 - Hunter Clade.cat', 'Hunter Clade')
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')

    report = kt.import_all(conn, directory=catalogues.path)

    assert report['inserted'] == 4
    assert report['updated'] == 0


def test_re_importing_updates_rather_than_duplicating(conn, catalogues):
    """The key has to be stable, or a re-sync doubles the collection."""
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')
    kt.import_all(conn, directory=catalogues.path)

    second = kt.import_all(conn, directory=catalogues.path)

    assert (second['inserted'], second['updated']) == (0, 2)
    assert conn.execute('SELECT COUNT(*) FROM datasheets').fetchone()[0] == 2


# ── The second silent failure: imported but invisible ────

def test_operatives_actually_reach_the_picker(conn, catalogues):
    """search_datasheets excludes `variant IS NOT NULL` to hide deprecated
    40,000 printings. Kill Team keeps its *edition* in that column, so the
    unqualified filter imports 1,450 operatives and shows none of them."""
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')
    kt.import_all(conn, directory=catalogues.path)

    results = col.search_datasheets(conn, 'Skitarii')

    assert len(results) == 2
    assert results[0]['game_system'] == 'killteam'
    assert results[0]['variant'] == '2024', 'the picker has to show which edition'


def test_40k_datasheets_rank_above_kill_team(conn, catalogues):
    """This is a 40,000 collection first.

    Ordering by game_system alphabetically puts "killteam" ahead of "wh40k",
    which buried the Intercessor Squad datasheet under Kill Team's Intercessor
    Gunner and Warrior — a regression in the primary use case, caused by adding
    the secondary one.
    """
    faction = db.upsert_faction(conn, 'Space Marines', 'space-marines')
    conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
        'game_system, created_at, updated_at) '
        "VALUES ('ds-int', 'Intercessor Squad', ?, 1, 'wh40k', ?, ?)",
        (faction, db.now(), db.now()))
    catalogues.write('2024 - Angels of Death.cat', 'Angels of Death',
                     body=CATALOGUE.format(cid='aod', team='Angels of Death')
                     .replace('Skitarii Ranger Gunner', 'Intercessor Gunner')
                     .replace('Skitarii Ranger Alpha', 'Intercessor Warrior'))
    kt.import_all(conn, directory=catalogues.path)

    results = col.search_datasheets(conn, 'Intercessor')

    assert results[0]['name'] == 'Intercessor Squad'
    assert results[0]['game_system'] == 'wh40k'
    assert {r['game_system'] for r in results[1:]} == {'killteam'}


def test_deprecated_40k_printings_are_still_hidden(conn):
    """The fix must not also un-hide Legends, which is what the filter is for."""
    faction = db.upsert_faction(conn, 'Aeldari', 'aeldari')
    for name, variant in (('Vypers', None), ('Vypers', 'legends')):
        conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'variant, created_at, updated_at) VALUES (?,?,?,1,?,?,?)',
            (f'ds-{name}-{variant}', name, faction, variant, db.now(), db.now()))

    results = col.search_datasheets(conn, 'Vypers')

    assert len(results) == 1
    assert results[0]['variant'] is None


# ── Nothing is dropped quietly ───────────────────────────

def test_a_catalogue_with_no_operatives_is_reported(conn, catalogues):
    catalogues.write('2024 - Empty.cat',
                     'Empty', body=CATALOGUE.format(cid='empt', team='Empty')
                     .replace('type="model"', 'type="upgrade"'))

    report = kt.import_all(conn, directory=catalogues.path)

    assert report['empty'] == ['2024 - Empty.cat']
    assert conn.execute(
        "SELECT COUNT(*) FROM unresolved_imports WHERE importer = 'killteam'"
    ).fetchone()[0] == 1


def test_an_unparseable_catalogue_is_reported_not_skipped(conn, catalogues):
    catalogues.write('2024 - Broken.cat', 'Broken', body='<catalogue><oops>')

    report = kt.import_all(conn, directory=catalogues.path)

    assert len(report['unreadable']) == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM unresolved_imports WHERE importer = 'killteam'"
    ).fetchone()[0] == 1
