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

# ── Which faction a team belongs to ──────────────────────
#
# Clay: "when I filter for orks it filters out my ork kill team."
#
# It did. The importer matched a team's *name* against a 40,000 faction, so
# Orks matched Orks and Kommandos matched nothing — 1158 of 1450 operatives sat
# on faction rows no 40,000 filter would ever reach. The allegiance was in the
# data the whole time, as category ids the 2024 game system defines and each
# catalogue references.
#
# Built here rather than read from data/killteam/, which is fetched and not in
# the repository: a test that silently skips when the checkout is missing is a
# test CI never runs.

GST = '''<?xml version="1.0" encoding="utf-8"?>
<gameSystem xmlns="http://www.battlescribe.net/schema/gameSystemSchema">
  <categoryEntries>
    <categoryEntry name="Operative" id="role-oper"/>
    <categoryEntry name="Leader" id="role-lead"/>
    <categoryEntry name="Imperium" id="all-imp"/>
    <categoryEntry name="Aeldari" id="fac-aeldari"/>
    <categoryEntry name="Drukhari" id="fac-drukhari"/>
    <categoryEntry name="Ork" id="fac-ork"/>
  </categoryEntries>
</gameSystem>
'''

TEAM = '''<?xml version="1.0" encoding="utf-8"?>
<catalogue xmlns="http://www.battlescribe.net/schema/catalogueSchema"
           id="cat-{cid}" name="{team}">
  <selectionEntries>
    <selectionEntry id="{cid}-1" name="{team} Fighter" type="model"/>
  </selectionEntries>
  <categoryLinks>{links}</categoryLinks>
</catalogue>
'''


@pytest.fixture
def teams(tmp_path):
    """A miniature of the real directory: one game system, four teams."""
    (tmp_path / '2024 - Kill Team.gst').write_text(GST)

    def team(filename, name, *category_ids):
        links = ''.join(
            f'<categoryLink id="l{i}" targetId="{c}"/>'
            for i, c in enumerate(category_ids))
        (tmp_path / filename).write_text(
            TEAM.format(cid=filename[:4].replace(' ', ''), team=name, links=links))

    team('2024 - Kommandos.cat', 'Kommandos', 'fac-ork')
    # A second Aeldari team, so Aeldari is broader than Drukhari here exactly
    # as it is in the real data — six catalogues to one. Without it both sit
    # at breadth 1 and the test would be measuring the tiebreak instead.
    team('2024 - Blades of Khaine.cat', 'Blades of Khaine', 'fac-aeldari')
    team('2024 - Mandrakes.cat', 'Mandrakes', 'fac-aeldari', 'fac-drukhari')
    team('2024 - Battleclade.cat', 'Battleclade', 'all-imp')
    team('2021 - Kommando.cat', 'Kommando')          # no categories at all
    return str(tmp_path)


@pytest.fixture
def factions(conn):
    return {slug: db.upsert_faction(conn, name, slug) for name, slug in
            (('Orks', 'orks'), ('Aeldari', 'aeldari'), ('Drukhari', 'drukhari'))}


def test_a_team_lands_on_the_faction_its_catalogue_claims(conn, teams, factions):
    """Kommandos are Orks and say so in their category links. Nothing about
    the string "Kommandos" says it, which is why the name match never could."""
    placed = kt.resolve_factions(conn, teams, reviewed_path=None)

    assert placed['Kommandos'] == factions['orks']


def test_the_older_printing_inherits_from_the_newer(conn, teams, factions):
    """The 2021 catalogues carry no categories at all. `Kommando` is the same
    team as `Kommandos`, matched on the name rather than on a guess."""
    placed = kt.resolve_factions(conn, teams, reviewed_path=None)

    assert placed['Kommando'] == factions['orks']


def test_the_narrowest_category_wins(conn, teams, factions):
    """Mandrakes claim Aeldari and Drukhari and both name a real faction.
    Drukhari is claimed by one team here and Aeldari by two, so the rarer is
    the more specific — an ordering read off the data, not asserted. The
    fixture mirrors the real breadths (Aeldari 6, Drukhari 1) on purpose: with
    both at one this would be measuring the tiebreak."""
    placed = kt.resolve_factions(conn, teams, reviewed_path=None)

    assert placed['Mandrakes'] == factions['drukhari']


def test_a_team_the_data_cannot_place_is_left_alone(conn, teams, factions):
    """An alliance is not a faction. `Imperium` names no row in `factions`, so
    Battleclade keeps its own — assigning one from recall is the change this
    repo forbids."""
    placed = kt.resolve_factions(conn, teams, reviewed_path=None)

    assert 'Battleclade' not in placed


def test_nothing_lands_on_a_kill_team_only_row(conn, teams, factions):
    """Every id returned has to be a real 40,000 faction. One that is not
    leaves the filter this exists to fix still broken."""
    db.upsert_faction(conn, 'Battleclade', 'kt-battleclade')

    placed = kt.resolve_factions(conn, teams, reviewed_path=None)

    real = {r['id'] for r in conn.execute(
        "SELECT id FROM factions WHERE slug NOT LIKE 'kt-%'")}
    assert placed and set(placed.values()) <= real


def test_a_plural_is_the_same_army(conn, teams, factions):
    """The category is `Ork` and the faction is `Orks`. That is a spelling
    difference, and tolerating it in one direction is what lets the only two
    Ork teams in the game reach the Orks filter."""
    placed = kt.resolve_factions(conn, teams, reviewed_path=None)

    assert placed['Kommandos'] == factions['orks']


def test_only_models_become_datasheets(conn, catalogues):
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')
    report = kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    assert report['inserted'] == 2, 'the upgrade is a weapon, not a miniature'
    names = {r[0] for r in conn.execute('SELECT name FROM datasheets')}
    assert names == {'Skitarii Ranger Gunner', 'Skitarii Ranger Alpha'}


def test_operatives_are_one_model_each(conn, catalogues):
    """The contents form pre-fills the count from min_models, so 1 here is what
    makes picking an operative fill in a usable number."""
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')
    kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    row = conn.execute('SELECT min_models, max_models FROM datasheets').fetchone()
    assert (row['min_models'], row['max_models']) == (1, 1)


def test_the_game_system_is_recorded(conn, catalogues):
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')
    kt.import_all(conn, directory=catalogues.path, reviewed_path=None)
    systems = {r[0] for r in conn.execute('SELECT game_system FROM datasheets')}
    assert systems == {'killteam'}


def test_edition_comes_from_the_filename(conn, catalogues):
    catalogues.write('2021 - Hunter Clade.cat', 'Hunter Clade')
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')
    catalogues.write('Adeptus Mechanicus.cat', 'Adeptus Mechanicus')

    kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    editions = {r[0] for r in conn.execute('SELECT variant FROM datasheets')}
    assert editions == {'2018', '2021', '2024'}


def test_a_team_reuses_the_40k_faction_of_the_same_name(conn, catalogues):
    """This used to assert the opposite, and the reversal is deliberate.

    "Orks" exists in both games and they are genuinely not the same list — but
    what keeps those lists apart is `datasheets.game_system`, not the faction
    row. A faction is the label Clay picks when tagging an army, a kit or a
    list, and there he only ever meant one Orks. Two rows meant a picker
    offering the same name twice with no way to choose, on seven screens.
    """
    orks = db.upsert_faction(conn, 'Orks', 'orks')
    catalogues.write('2024 - Orks.cat', 'Orks')

    kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    slugs = {r[0] for r in conn.execute('SELECT slug FROM factions')}
    assert slugs == {'orks'}, 'no second row for a name that already exists'
    operative_factions = {r[0] for r in conn.execute(
        "SELECT DISTINCT faction_id FROM datasheets WHERE game_system = 'killteam'")}
    assert operative_factions == {orks}


def test_the_two_unit_lists_stay_apart_after_sharing_a_faction(conn, catalogues):
    """The thing the slug prefix was protecting. Sharing a faction row must not
    let a Kill Team operative and a 40,000 datasheet become one list."""
    orks = db.upsert_faction(conn, 'Orks', 'orks')
    conn.execute(
        'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
        "game_system, created_at, updated_at) VALUES ('boyz', 'Boyz', ?, 1, "
        "'wh40k', ?, ?)", (orks, db.now(), db.now()))
    catalogues.write('2024 - Orks.cat', 'Orks')

    kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    systems = {r[0]: r[1] for r in conn.execute(
        'SELECT game_system, COUNT(*) FROM datasheets GROUP BY game_system')}
    assert systems['wh40k'] == 1
    assert systems['killteam'] >= 1, 'both present, told apart by system'


def test_a_team_with_no_40k_namesake_still_gets_its_own_row(conn, catalogues):
    """Wrecka Krew is not a duplicate of anything."""
    catalogues.write('2024 - Wrecka Krew.cat', 'Wrecka Krew')

    kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    slugs = {r[0] for r in conn.execute('SELECT slug FROM factions')}
    assert slugs == {'kt-wrecka-krew'}


# ── The first silent failure: collapsed operatives ───────

def test_the_same_entry_id_in_two_teams_keeps_both(conn, catalogues):
    """BSData reuses entry ids across catalogues — 20 of them in the real data.

    Keyed on the bare id, the second catalogue overwrites the first and a team
    quietly loses an operative, which Clay discovers holding the box.
    """
    catalogues.write('2021 - Hunter Clade.cat', 'Hunter Clade')
    catalogues.write('2021 - Forge World (Legends).cat', 'Forge World (Legends)')

    report = kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    assert report['inserted'] == 4, 'two operatives, in each of two teams'
    assert report['updated'] == 0, 'nothing should have been overwritten'


def test_the_same_entry_id_in_two_editions_keeps_both(conn, catalogues):
    catalogues.write('2021 - Hunter Clade.cat', 'Hunter Clade')
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')

    report = kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    assert report['inserted'] == 4
    assert report['updated'] == 0


def test_re_importing_updates_rather_than_duplicating(conn, catalogues):
    """The key has to be stable, or a re-sync doubles the collection."""
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')
    kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    second = kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    assert (second['inserted'], second['updated']) == (0, 2)
    assert conn.execute('SELECT COUNT(*) FROM datasheets').fetchone()[0] == 2


# ── The second silent failure: imported but invisible ────

def test_operatives_actually_reach_the_picker(conn, catalogues):
    """search_datasheets excludes `variant IS NOT NULL` to hide deprecated
    40,000 printings. Kill Team keeps its *edition* in that column, so the
    unqualified filter imports 1,450 operatives and shows none of them."""
    catalogues.write('2024 - Hunter Clade.cat', 'Hunter Clade')
    kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

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
    kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

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

    report = kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    assert report['empty'] == ['2024 - Empty.cat']
    assert conn.execute(
        "SELECT COUNT(*) FROM unresolved_imports WHERE importer = 'killteam'"
    ).fetchone()[0] == 1


def test_an_unparseable_catalogue_is_reported_not_skipped(conn, catalogues):
    catalogues.write('2024 - Broken.cat', 'Broken', body='<catalogue><oops>')

    report = kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    assert len(report['unreadable']) == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM unresolved_imports WHERE importer = 'killteam'"
    ).fetchone()[0] == 1


# ── The reviewed table ───────────────────────────────────
#
# Clay handed over a team -> faction table. It is trusted for one reason
# only: a person reviewed it and said so in the file. That is also the whole
# difference between it and a table a model wrote from memory, which is the
# thing this repo will not import — so provenance is checked, and everything
# the table names that cannot be used is reported rather than approximated.


def reviewed_file(tmp_path, body):
    path = tmp_path / 'reviewed.yaml'
    path.write_text(body)
    return str(path)


TABLE = '''
source:
  retrieved_on: 2026-08-24
  confidence: high
  reviewed_by: Clay
teams:
{entries}
'''


@pytest.fixture
def more_factions(conn, factions):
    extra = {slug: db.upsert_faction(conn, name, slug) for name, slug in
             (('Adeptus Mechanicus', 'adeptus-mechanicus'),
              ('Tyranids', 'tyranids'),
              ('Genestealer Cults', 'genestealer-cults'))}
    return {**factions, **extra}


def test_the_reviewed_table_places_what_the_categories_cannot(
        conn, teams, more_factions, tmp_path):
    """Battleclade claims `Imperium`, which covers nineteen teams and names no
    faction, so the category rule correctly refuses it. The table is where
    that answer comes from instead — reviewed, not inferred."""
    path = reviewed_file(tmp_path, TABLE.format(
        entries='  - {name: Battleclade, faction: Adeptus Mechanicus}'))

    placed = kt.resolve_factions(conn, teams, reviewed_path=path)

    assert placed['Battleclade'] == more_factions['adeptus-mechanicus']


def test_the_reviewed_table_beats_the_derived_answer(
        conn, teams, more_factions, tmp_path):
    """Mandrakes derive to Drukhari by the narrowest-category rule. If the
    reviewed table said Aeldari, the person would win — the inference is the
    fallback, not the authority."""
    path = reviewed_file(tmp_path, TABLE.format(
        entries='  - {name: Mandrakes, faction: Aeldari}'))

    placed = kt.resolve_factions(conn, teams, reviewed_path=path)

    assert placed['Mandrakes'] == more_factions['aeldari']


def test_an_override_is_reported_never_swallowed(
        conn, teams, more_factions, tmp_path):
    """A place where a person and the category rule disagree means one of the
    two is wrong. Applying it silently loses the only signal that says so."""
    path = reviewed_file(tmp_path, TABLE.format(
        entries='  - {name: Mandrakes, faction: Aeldari}'))
    report = {}

    kt.resolve_factions(conn, teams, reviewed_path=path, report=report)

    assert report['disagreed'] == [('Mandrakes', 'Drukhari', 'Aeldari')]


def test_a_table_with_no_provenance_is_refused(conn, teams, tmp_path):
    """Unattributed, it is indistinguishable from one written from memory."""
    path = reviewed_file(tmp_path, '''
source: {confidence: high}
teams:
  - {name: Battleclade, faction: Adeptus Mechanicus}
''')

    with pytest.raises(ValueError, match='provenance'):
        kt.resolve_factions(conn, teams, reviewed_path=path)


def test_a_faction_with_no_row_is_reported_not_approximated(
        conn, teams, factions, tmp_path):
    """`Chaos` is an alliance covering several armies and names no row. The
    nearest one is not the answer: a team quietly filed under the wrong army
    is worse than one visibly unfiled."""
    path = reviewed_file(tmp_path, TABLE.format(
        entries='  - {name: Battleclade, faction: Chaos}'))
    report = {}

    placed = kt.resolve_factions(conn, teams, reviewed_path=path, report=report)

    assert 'Battleclade' not in placed
    assert report['reviewed_no_faction'] == [('Battleclade', 'Chaos')]


def test_a_team_with_no_catalogue_is_reported_not_dropped(
        conn, teams, more_factions, tmp_path):
    """A line in the table that matches nothing is a team Clay believes is
    filed and is not. Silence here is the shortfall he finds holding the box."""
    path = reviewed_file(tmp_path, TABLE.format(
        entries='  - {name: Nonesuch Krew, faction: Adeptus Mechanicus}'))
    report = {}

    kt.resolve_factions(conn, teams, reviewed_path=path, report=report)

    assert report['reviewed_no_catalogue'] == ['Nonesuch Krew']


def test_a_reviewed_placement_reaches_both_printings(
        conn, teams, factions, tmp_path):
    """`Kommando` (2021) carries no categories at all. The table names only
    `Kommandos`, and both are the same team in two printings."""
    path = reviewed_file(tmp_path, TABLE.format(
        entries='  - {name: Kommandos, faction: Orks}'))

    placed = kt.resolve_factions(conn, teams, reviewed_path=path)

    assert placed['Kommando'] == factions['orks']


def test_an_override_reaches_the_other_printing_too(
        conn, teams, more_factions, tmp_path):
    """The correction has to cover the whole twin group, not just the printing
    the table happened to name — otherwise one edition silently keeps the
    answer the reviewer rejected."""
    path = reviewed_file(tmp_path, TABLE.format(
        entries='  - {name: Kommandos, faction: Aeldari}'))

    placed = kt.resolve_factions(conn, teams, reviewed_path=path)

    assert placed['Kommando'] == more_factions['aeldari']


def test_a_catalogue_named_differently_needs_saying_so(
        conn, teams, more_factions, tmp_path):
    """`XV26 Stealth Battlesuits` is `XV26 Battlesuits` in BSData. That is a
    different name rather than a plural, so it is declared rather than
    guessed at with a fuzzy match."""
    path = reviewed_file(tmp_path, TABLE.format(entries=(
        '  - {name: Battleclade Prime, faction: Adeptus Mechanicus, '
        'catalogue: Battleclade}')))

    placed = kt.resolve_factions(conn, teams, reviewed_path=path)

    assert placed['Battleclade'] == more_factions['adeptus-mechanicus']


def test_no_table_is_not_an_error(conn, teams, factions):
    """The catalogues still place what they can without it."""
    placed = kt.resolve_factions(conn, teams, reviewed_path='/nope/absent.yaml')

    assert placed['Kommandos'] == factions['orks']


# ── The shipped table ────────────────────────────────────

def test_the_shipped_table_carries_its_provenance():
    """The file in the repo is the one production loads. If it ever lost its
    attribution the importer would refuse it at run time on bastion, which is
    a worse place to find out."""
    data = kt.load_reviewed(kt.REVIEWED_PATH)

    assert data, 'seed/data/killteam_factions.yaml should ship'
    assert data['source']['reviewed_by']
    assert data['source']['retrieved_on']
    assert data['teams']


def test_every_shipped_entry_names_a_team_and_a_faction():
    """A half-written line resolves to nothing and reports as a miss, which
    reads like missing data rather than a typo."""
    for entry in kt.load_reviewed(kt.REVIEWED_PATH)['teams']:
        assert entry.get('name'), entry
        assert entry.get('faction'), entry


# ── The name match, and the row it used to duplicate ─────
#
# The compendium teams are named after their faction, and that name match is
# what files them. It compared raw strings, so it missed on punctuation alone:
# BSData writes the faction `T’au Empire` with a curly apostrophe and the team
# `T'au Empire` with a straight one. 24 operatives ended up on a second
# `kt-t-au-empire` row — a duplicate the army picker offered twice and no
# T'au filter ever reached.


def test_a_team_named_for_its_faction_reuses_that_row(conn, catalogues):
    """Same words, different apostrophe. Making a row of its own here is how
    the collection ends up with two factions of the same name."""
    real = db.upsert_faction(conn, 'T’au Empire', 'tau-empire')
    catalogues.write("2021 - T'au Empire.cat", "T'au Empire")

    kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    assert {r['faction_id'] for r in conn.execute(
        'SELECT faction_id FROM datasheets')} == {real}
    assert conn.execute(
        "SELECT COUNT(*) FROM factions WHERE slug LIKE 'kt-%'").fetchone()[0] == 0


def test_a_singular_team_name_finds_its_plural_faction(conn, catalogues):
    """`Space Marine` is the compendium team and `Space Marines` the faction.
    A plural is a spelling difference, not a different army."""
    real = db.upsert_faction(conn, 'Space Marines', 'space-marines')
    catalogues.write('2021 - Space Marine.cat', 'Space Marine')

    kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    assert {r['faction_id'] for r in conn.execute(
        'SELECT faction_id FROM datasheets')} == {real}


def test_a_team_that_is_not_a_faction_still_gets_its_own_row(conn, catalogues):
    """The tolerance above must not become a fuzzy match. `Wrecka Krew` is not
    a faction under any spelling, and inventing one for it is the change this
    repo forbids."""
    db.upsert_faction(conn, 'Orks', 'orks')
    catalogues.write('2024 - Wrecka Krew.cat', 'Wrecka Krew')

    report = kt.import_all(conn, directory=catalogues.path, reviewed_path=None)

    assert report['unplaced'] == ['Wrecka Krew']
    assert conn.execute(
        "SELECT slug FROM factions WHERE slug LIKE 'kt-%'").fetchone()['slug'] \
        == 'kt-wrecka-krew'
