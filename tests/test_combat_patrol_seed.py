"""The Combat Patrol magazine seed.

The rules under test are §11's: seed data is derived and reviewed or it does
not ship, a wrong template is worse than a missing one, and nothing is ever
invented to fill a gap.
"""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'seed'))

import combat_patrol_magazine as seeder  # noqa: E402
import database as db  # noqa: E402
import scanning as scan  # noqa: E402


@pytest.fixture
def orks(conn):
    faction_id = db.upsert_faction(conn, 'Orks', 'orks')
    marines = db.upsert_faction(conn, 'Space Marines', 'space-marines')
    ids = {}
    for name, effort, fid in (('Boyz', 1, faction_id), ('Deff Dread', 8, faction_id),
                              ('Deffkoptas', 4, faction_id),
                              ('Maulerfiend', 8, faction_id),
                              ('Rhino', 8, marines)):
        cur = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
            (name.lower(), name, fid, effort, db.now(), db.now()))
        ids[name] = cur.lastrowid
    # Committed, not just written: the tests that go through main() open their
    # own connection, and an open write transaction here would lock them out.
    conn.commit()
    return ids


def data(issues, **overrides):
    """A minimal, provenance-complete contents file."""
    base = {
        'source': {'retrieved_on': '2026-08-21', 'confidence': 'high',
                   'urls': ['https://example.test/contents'],
                   'corroborated_by': ['https://example.test/other']},
        'collections': {'orks': {'name': 'Combat Patrol: Orks', 'faction': 'orks'},
                        'premium': {'name': 'Premium: Killa Kans', 'premium': True}},
        'issues': issues,
    }
    base.update(overrides)
    return base


# ── It ships empty, and says so ──────────────────────────

def test_the_shipped_data_file_has_no_invented_contents():
    """The whole point. If this ever fails, someone wrote a partwork list from
    memory and it is about to become trusted seed data."""
    shipped = seeder.load_data()
    assert shipped['issues'] == [], \
        'seed/data/combat_patrol_issues.yaml must ship with no issue contents'
    assert shipped['source']['urls'] == []


def test_the_shipped_file_still_describes_all_thirteen_collections():
    """The nine Combat Patrols and four premium kits come from the spec, not
    from a web source, so they are safe to ship."""
    shipped = seeder.load_data()
    collections = shipped['collections']
    assert len(collections) == 13
    assert sum(1 for c in collections.values() if c.get('premium')) == 4


def test_seeding_nothing_reports_rather_than_silently_succeeding(tmp_path, db_path):
    empty = tmp_path / 'empty.yaml'
    empty.write_text(yaml.safe_dump({'source': {}, 'collections': {}, 'issues': []}))
    assert seeder.main(['--data', str(empty), '--db', db_path]) == 1


def test_status_never_touches_the_database(tmp_path, db_path):
    assert seeder.main(['--status', '--data', str(_write(tmp_path, data([
        {'issue': 1, 'collection': 'orks',
         'contents': [{'unit': 'Boyz', 'models': 10}]}]))), '--db', db_path]) == 0
    with db.connect(db_path) as conn:
        assert conn.execute('SELECT COUNT(*) c FROM kit_templates').fetchone()['c'] == 0


def _write(tmp_path, payload):
    path = tmp_path / 'issues.yaml'
    path.write_text(yaml.safe_dump(payload, allow_unicode=True))
    return path


# ── Provenance is not optional ───────────────────────────

def test_contents_without_a_source_are_refused(tmp_path, db_path):
    """Undated, unattributed seed data is indistinguishable from invented seed
    data once it is in the database."""
    payload = data([{'issue': 1, 'collection': 'orks',
                     'contents': [{'unit': 'Boyz', 'models': 10}]}])
    payload['source'] = {'urls': [], 'retrieved_on': None, 'confidence': None,
                         'corroborated_by': []}
    assert seeder.main(['--data', str(_write(tmp_path, payload)), '--db', db_path]) == 1


def test_one_source_is_a_guess(tmp_path, db_path):
    payload = data([{'issue': 1, 'collection': 'orks',
                     'contents': [{'unit': 'Boyz', 'models': 10}]}])
    payload['source']['corroborated_by'] = []
    problems = seeder.check_provenance(payload)
    assert any('one source is a guess' in p for p in problems)


def test_a_draft_can_still_be_dry_run(tmp_path, db_path, orks):
    """Checking a half-finished list must not require finished provenance."""
    payload = data([{'issue': 1, 'collection': 'orks',
                     'contents': [{'unit': 'Boyz', 'models': 10}]}])
    payload['source'] = {'urls': [], 'retrieved_on': None, 'confidence': None,
                         'corroborated_by': []}
    assert seeder.main(['--dry-run', '--data', str(_write(tmp_path, payload)),
                        '--db', db_path]) == 0


# ── Structure ────────────────────────────────────────────

@pytest.mark.parametrize('issues, fragment', [
    ([{'issue': 0, 'collection': 'orks', 'contents': []}], 'between 1 and 90'),
    ([{'issue': 91, 'collection': 'orks', 'contents': []}], 'between 1 and 90'),
    ([{'issue': 1, 'collection': 'orks', 'contents': []},
      {'issue': 1, 'collection': 'orks', 'contents': []}], 'more than once'),
    ([{'issue': 1, 'collection': 'nope', 'contents': []}], 'unknown collection'),
    ([{'issue': 1, 'collection': 'orks',
       'contents': [{'unit': 'Boyz', 'models': 0}]}], 'at least one model'),
    ([{'issue': 1, 'collection': 'orks',
       'contents': [{'unit': 'Boyz', 'models': 1, 'spans': [7, 8]}]}], 'spans'),
])
def test_structural_problems_are_caught_before_any_write(issues, fragment):
    problems = seeder.validate_issues(data(issues))
    assert any(fragment in p for p in problems), problems


# ── Matching: never invent a datasheet ───────────────────

def test_each_issue_becomes_a_template(conn, orks):
    report = seeder.seed(conn, data([
        {'issue': 43, 'collection': 'orks',
         'contents': [{'unit': 'Boyz', 'models': 10}]},
        {'issue': 49, 'collection': 'orks',
         'contents': [{'unit': 'Deff Dread', 'models': 1}]},
    ]))
    assert report['templates_created'] == 2
    names = [t['name'] for t in scan.list_templates(conn)]
    assert 'Combat Patrol Magazine #43 — Combat Patrol: Orks' in names


def test_an_unmatched_unit_is_reported_never_invented(conn, orks):
    report = seeder.seed(conn, data([
        {'issue': 5, 'collection': 'orks',
         'contents': [{'unit': 'Squig Launcha', 'models': 1}]}]))
    assert report['templates_created'] == 0
    assert len(report['unresolved']) == 1
    assert report['unresolved'][0][1] == 'Squig Launcha'
    rows = db.open_unresolved(conn, 'combat_patrol')
    assert len(rows) == 1 and rows[0]['raw_name'] == 'Squig Launcha'


def test_a_name_in_another_faction_does_not_match(conn, orks):
    """The Space Marines Rhino must not be seeded into an Orks issue."""
    report = seeder.seed(conn, data([
        {'issue': 5, 'collection': 'orks',
         'contents': [{'unit': 'Rhino', 'models': 1}]}]))
    assert report['templates_created'] == 1, \
        'a globally unique name is still usable'
    # ...but it resolved to the real Rhino, not something invented.
    template = scan.get_template(conn, scan.list_templates(conn)[0]['id'])
    assert template['contents'][0]['datasheet_name'] == 'Rhino'


def test_a_legends_variant_is_never_matched(conn, orks):
    conn.execute(
        "INSERT INTO datasheets (bsdata_id, name, faction_id, effort, variant, "
        "created_at, updated_at) VALUES ('v', 'Squig Launcha', 1, 1, 'legends', ?, ?)",
        (db.now(), db.now()))
    report = seeder.seed(conn, data([
        {'issue': 5, 'collection': 'orks',
         'contents': [{'unit': 'Squig Launcha', 'models': 1}]}]))
    assert report['unresolved'], 'a deprecated printing must not satisfy a seed'


def test_an_issue_whose_units_all_fail_makes_no_empty_template(conn, orks):
    """An empty template instantiates an empty kit, which looks like success."""
    report = seeder.seed(conn, data([
        {'issue': 5, 'collection': 'orks',
         'contents': [{'unit': 'Not A Real Unit', 'models': 1}]}]))
    assert scan.list_templates(conn) == []
    assert 5 in report['parts_only']


# ── Multi-issue sprues ───────────────────────────────────

def test_a_sprue_split_across_issues_lands_on_the_one_that_completes_it(conn, orks):
    """Half a Maulerfiend is not a model you own."""
    report = seeder.seed(conn, data([
        {'issue': 89, 'collection': 'orks',
         'contents': [{'unit': 'Maulerfiend', 'models': 1, 'spans': [89, 90]}]},
        {'issue': 90, 'collection': 'orks',
         'contents': [{'unit': 'Maulerfiend', 'models': 1, 'spans': [89, 90]}]},
    ]))
    assert report['templates_created'] == 1
    assert report['parts_only'] == [89]
    assert scan.list_templates(conn)[0]['name'].startswith(
        'Combat Patrol Magazine #90')


# ── Owned kits ───────────────────────────────────────────

def test_owned_through_creates_kits_and_models_up_to_that_issue(conn, orks):
    payload = data([
        {'issue': 43, 'collection': 'orks',
         'contents': [{'unit': 'Boyz', 'models': 10}]},
        {'issue': 80, 'collection': 'orks',
         'contents': [{'unit': 'Deff Dread', 'models': 1}]},
    ])
    report = seeder.seed(conn, payload, owned_through=75)
    assert report['templates_created'] == 2, 'templates for every issue in the file'
    assert report['kits_created'] == 1, 'but only issue 43 is owned'
    assert report['models_seeded'] == 10
    kits = conn.execute('SELECT * FROM kits').fetchall()
    assert kits[0]['source'] == 'magazine_issue'
    assert kits[0]['source_ref'] == 'Combat Patrol Magazine #43'
    # Magazine sprues arrive in a polybag, not a box.
    assert kits[0]['box_state'] == 'no_box'


def test_seeded_models_start_on_sprue(conn, orks):
    seeder.seed(conn, data([{'issue': 1, 'collection': 'orks',
                             'contents': [{'unit': 'Boyz', 'models': 10}]}]),
                owned_through=75)
    stages = {r['name'] for r in conn.execute(
        'SELECT s.name FROM models m JOIN stages s ON s.id = m.stage_id')}
    assert stages == {'On sprue'}


def test_re_running_does_not_duplicate_anything(conn, orks):
    payload = data([{'issue': 43, 'collection': 'orks',
                     'contents': [{'unit': 'Boyz', 'models': 10}]}])
    first = seeder.seed(conn, payload, owned_through=75)
    second = seeder.seed(conn, payload, owned_through=75)
    assert (first['templates_created'], first['kits_created']) == (1, 1)
    assert (second['templates_created'], second['kits_created']) == (0, 0)
    assert second['templates_updated'] == 1
    assert len(scan.list_templates(conn)) == 1
    assert conn.execute('SELECT COUNT(*) c FROM models').fetchone()['c'] == 10


# ── Reporting ────────────────────────────────────────────

def test_missing_issues_are_reported_as_ranges(conn, orks):
    report = seeder.seed(conn, data([
        {'issue': 1, 'collection': 'orks',
         'contents': [{'unit': 'Boyz', 'models': 10}]}]))
    assert report['issues_missing'][0] == 2
    assert len(report['issues_missing']) == 89


@pytest.mark.parametrize('numbers, expected', [
    ([1, 2, 3], '1-3'),
    ([1, 2, 3, 7, 8], '1-3, 7-8'),
    ([5], '5'),
    ([2, 4, 6], '2, 4, 6'),
    ([], ''),
])
def test_range_formatting(numbers, expected):
    """Ninety individual numbers is not a report."""
    assert seeder._ranges(numbers) == expected


def test_templates_carry_their_provenance(conn, orks):
    seeder.seed(conn, data([{'issue': 1, 'collection': 'orks',
                             'contents': [{'unit': 'Boyz', 'models': 10}]}]))
    template = scan.get_template(conn, scan.list_templates(conn)[0]['id'])
    assert template['contents_source'] == 'seed'
    assert template['contents_confidence'] == 'high'
    assert 'https://example.test/contents' in template['source_urls']
    assert 'https://example.test/other' in template['source_urls']


# ── Premium kits ─────────────────────────────────────────

def test_the_four_premium_kits_ship_with_contents():
    """Unlike the 90 issues, these are documented in the spec — a reviewed
    source — so shipping them is derived, not recalled."""
    shipped = seeder.load_data()
    kits = shipped['premium_kits']
    assert len(kits) == 4
    assert all(kit['contents'] for kit in kits.values())
    assert shipped['premium_source']['from'] == 'warhammer-tracker-spec.md §11'


def test_no_premium_kit_ships_marked_as_owned():
    """A premium kit is an optional extra. Claiming one Clay never bought would
    put models in his collection that do not exist."""
    shipped = seeder.load_data()
    assert not any(kit.get('owned') for kit in shipped['premium_kits'].values())


def premium(contents, owned=False):
    return {
        'source': {}, 'issues': [],
        'collections': {'p': {'name': 'Premium: Test Kit', 'premium': True}},
        'premium_source': {'from': 'spec §11', 'confidence': 'medium'},
        'premium_kits': {'p': {'owned': owned, 'contents': contents}},
    }


def test_min_resolves_to_the_datasheets_minimum_unit_size(conn, orks):
    """The count comes from the rules data, so it can never drift from them."""
    conn.execute('UPDATE datasheets SET min_models = 3 WHERE name = ?', ('Deffkoptas',))
    report = seeder.seed(conn, premium([{'unit': 'Deffkoptas', 'models': 'min'}]))
    assert report['premium_created'] == 1
    template = scan.get_template(conn, scan.list_templates(conn)[0]['id'])
    assert template['contents'][0]['model_count'] == 3


def test_an_explicit_count_still_works(conn, orks):
    seeder.seed(conn, premium([{'unit': 'Boyz', 'models': 7}]))
    template = scan.get_template(conn, scan.list_templates(conn)[0]['id'])
    assert template['contents'][0]['model_count'] == 7


def test_min_is_refused_when_the_unit_size_is_unknown(conn, orks):
    """A datasheet with no points entry has no minimum — better to say so than
    to quietly assume one."""
    conn.execute('UPDATE datasheets SET min_models = NULL WHERE name = ?', ('Boyz',))
    report = seeder.seed(conn, premium([{'unit': 'Boyz', 'models': 'min'}]))
    assert report['premium_created'] == 0
    assert any('unit size unknown' in why for _w, _u, why in report['unresolved'])


def test_a_premium_kit_is_built_from_the_lines_that_resolve(conn, orks):
    """Half a premium kit recorded honestly beats none — and beats a guess at
    the missing half."""
    report = seeder.seed(conn, premium([
        {'unit': 'Boyz', 'models': 10},
        {'unit': 'Daemon Prince', 'models': 'min'},      # genuinely ambiguous
    ]))
    assert report['premium_created'] == 1
    assert report['premium_partial'] == [('Premium: Test Kit', ['Daemon Prince'])]
    template = scan.get_template(conn, scan.list_templates(conn)[0]['id'])
    assert len(template['contents']) == 1
    assert 'unresolved: Daemon Prince' in template['notes']


def test_an_ambiguous_name_offers_the_near_misses(conn, orks):
    """"Daemon Prince" is a build-time choice between four datasheets, so the
    report has to name them rather than just refusing."""
    for suffix in ('of Chaos', 'of Khorne'):
        conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, effort, created_at, '
            'updated_at) VALUES (?, ?, 8, ?, ?)',
            (suffix, f'Daemon Prince {suffix}', db.now(), db.now()))
    report = seeder.seed(conn, premium([{'unit': 'Daemon Prince', 'models': 'min'}]))
    why = report['unresolved'][0][2]
    assert 'did you mean' in why and 'Daemon Prince of Chaos' in why


def test_a_premium_kit_with_nothing_resolvable_makes_no_template(conn, orks):
    report = seeder.seed(conn, premium([{'unit': 'Not Real At All', 'models': 1}]))
    assert report['premium_created'] == 0
    assert scan.list_templates(conn) == []


def test_premium_kits_are_not_owned_unless_asked(conn, orks):
    seeder.seed(conn, premium([{'unit': 'Boyz', 'models': 10}]))
    assert conn.execute('SELECT COUNT(*) c FROM kits').fetchone()['c'] == 0


def test_marking_one_owned_creates_its_kit_and_models(conn, orks):
    report = seeder.seed(conn, premium([{'unit': 'Boyz', 'models': 10}], owned=True))
    assert report['premium_owned'] == 1
    kit = conn.execute('SELECT * FROM kits').fetchone()
    assert kit['source'] == 'premium_kit'
    assert kit['box_state'] == 'no_box'
    assert conn.execute('SELECT COUNT(*) c FROM models').fetchone()['c'] == 10


def test_premium_kits_carry_their_own_provenance(conn, orks):
    seeder.seed(conn, premium([{'unit': 'Boyz', 'models': 10}]))
    template = scan.get_template(conn, scan.list_templates(conn)[0]['id'])
    assert template['contents_source'] == 'seed'
    assert template['contents_confidence'] == 'medium'
    assert template['source_urls'] == ['spec §11']


def test_a_premium_kit_may_span_factions(conn, orks):
    """Brutalis Dreadnought + Hive Tyrant is one kit and two factions."""
    report = seeder.seed(conn, premium([
        {'unit': 'Boyz', 'models': 10}, {'unit': 'Rhino', 'models': 1}]))
    assert report['premium_created'] == 1
    template = scan.get_template(conn, scan.list_templates(conn)[0]['id'])
    assert {c['faction_name'] for c in template['contents']} == {'Orks', 'Space Marines'}


def test_re_running_premium_kits_does_not_duplicate(conn, orks):
    payload = premium([{'unit': 'Boyz', 'models': 10}], owned=True)
    seeder.seed(conn, payload)
    second = seeder.seed(conn, payload)
    assert (second['premium_created'], second['premium_owned']) == (0, 0)
    assert second['premium_updated'] == 1
    assert conn.execute('SELECT COUNT(*) c FROM models').fetchone()['c'] == 10


@pytest.mark.parametrize('kits, fragment', [
    ({'nope': {'contents': [{'unit': 'Boyz', 'models': 1}]}}, 'no entry in collections'),
    ({'p': {'contents': []}}, 'no contents'),
    ({'p': {'contents': [{'models': 1}]}}, 'no unit'),
    ({'p': {'contents': [{'unit': 'Boyz', 'models': 'lots'}]}}, 'number or "min"'),
])
def test_premium_structural_problems_are_caught(kits, fragment):
    payload = premium([])
    payload['premium_kits'] = kits
    problems = seeder.validate_premium(payload)
    assert any(fragment in p for p in problems), problems


def test_premium_kits_seed_even_with_no_issue_contents(tmp_path, db_path, orks):
    """The whole point of shipping them: they work today, the issues do not."""
    payload = premium([{'unit': 'Boyz', 'models': 10}])
    assert seeder.main(['--data', str(_write(tmp_path, payload)),
                        '--db', db_path]) == 0
