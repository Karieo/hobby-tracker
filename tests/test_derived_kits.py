"""The derived kit catalogue.

The rule under test is the one that would do real damage if it slipped: a box's
contents are derived from published sources and reviewed, or they do not ship.
A missing template costs two minutes at the review screen. A wrong one corrupts
ownership and purchase advice for months, with nothing to prompt anyone to
check it.

Barcodes are held to a higher bar than contents, and that is deliberate. Wrong
contents under a name are visible the moment Clay opens the box; a wrong
barcode silently attaches the wrong contents to a box he scans months later.
"""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'seed'))

import database as db  # noqa: E402
import derived_kits as seeder  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / 'derived.db')
    db.migrate(path)
    c = db.connect(path)
    yield c
    c.close()


@pytest.fixture
def orks(conn):
    faction_id = db.upsert_faction(conn, 'Orks', 'orks')
    ids = {}
    for name in ('Beastboss', 'Beast Snagga Boyz', 'Squighog Boyz'):
        ids[name] = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)',
            (name.lower(), name, faction_id, db.now(), db.now())).lastrowid
    conn.commit()
    return ids


def entry(**overrides):
    base = {
        'name': 'Combat Patrol: Orks',
        'year': 2024,
        'faction': 'orks',
        'contents': [{'unit': 'Beastboss', 'models': 1},
                     {'unit': 'Beast Snagga Boyz', 'models': 20}],
        'sources': {'urls': ['https://example.invalid/a'],
                    'retrieved_on': '2026-08-22',
                    'confidence': 'high',
                    'corroborated_by': 2},
    }
    base.update(overrides)
    return base


# ── Provenance is a precondition ─────────────────────────

def test_a_sourced_entry_passes(orks):
    assert seeder.check_entry(entry(), 1) == []


def test_an_entry_with_no_sources_is_refused():
    problems = seeder.check_entry(entry(sources={}), 1)
    assert any('sources.urls is empty' in p for p in problems)


def test_a_single_source_is_refused():
    """One source is a guess — the same rule the scanned-contents flow uses."""
    bad = entry()
    bad['sources']['corroborated_by'] = 1
    assert any('corroborated_by' in p for p in seeder.check_entry(bad, 1))


def test_an_undated_entry_is_refused():
    bad = entry()
    del bad['sources']['retrieved_on']
    assert any('retrieved_on' in p for p in seeder.check_entry(bad, 1))


def test_a_nonsense_confidence_is_refused():
    bad = entry()
    bad['sources']['confidence'] = 'pretty sure'
    assert any('confidence' in p for p in seeder.check_entry(bad, 1))


def test_an_entry_with_no_contents_is_refused():
    """An empty template instantiates an empty kit, which looks like it
    worked."""
    assert any('no contents' in p for p in seeder.check_entry(entry(contents=[]), 1))


# ── Barcodes are held higher ─────────────────────────────

def test_a_barcode_needs_two_sources():
    bad = entry(barcode='5011921204021',
                barcode_sources=['https://example.invalid/only-one'])
    problems = seeder.check_entry(bad, 1)
    assert any('needs two independent' in p for p in problems)


def test_a_corroborated_barcode_is_accepted():
    ok = entry(barcode='5011921204021',
               barcode_sources=['https://example.invalid/a',
                                'https://example.invalid/b'])
    assert seeder.check_entry(ok, 1) == []


def test_an_unsourced_barcode_is_refused_rather_than_dropped():
    """Silently importing without it would hide that someone tried."""
    bad = entry(barcode='5011921204021')
    assert any('5011921204021' in p for p in seeder.check_entry(bad, 1))


def test_a_barcode_must_be_plain_digits():
    bad = entry(barcode='5011921-204021',
                barcode_sources=['https://a.invalid', 'https://b.invalid'])
    assert any('plain digits' in p for p in seeder.check_entry(bad, 1))


def test_sources_without_a_barcode_are_refused():
    bad = entry(barcode_sources=['https://a.invalid', 'https://b.invalid'])
    assert any('no barcode' in p for p in seeder.check_entry(bad, 1))


# ── Nothing is invented ──────────────────────────────────

def test_an_unmatched_unit_is_recorded_not_guessed(conn, orks):
    data = {'kits': [entry(contents=[{'unit': 'Beastboss', 'models': 1},
                                     {'unit': 'Grot Mega-Tank', 'models': 1}])]}

    report = seeder.seed(conn, data)

    assert len(report['unresolved']) == 1
    assert report['unresolved'][0][1] == 'Grot Mega-Tank'
    open_rows = db.open_unresolved(conn, 'derived_kits')
    assert [r['raw_name'] for r in open_rows] == ['Grot Mega-Tank']
    # And the rest of the box still seeds — one bad line is not a lost box.
    assert report['created'] == 1


def test_a_kit_whose_lines_all_fail_creates_no_template(conn, orks):
    data = {'kits': [entry(contents=[{'unit': 'Nothing Real', 'models': 1}])]}

    report = seeder.seed(conn, data)

    assert report['created'] == 0
    assert report['skipped'] == [('Combat Patrol: Orks', 'no contents resolved')]
    assert conn.execute('SELECT COUNT(*) FROM kit_templates').fetchone()[0] == 0


def test_a_dry_run_writes_nothing(conn, orks):
    seeder.seed(conn, {'kits': [entry()]}, dry_run=True)
    assert conn.execute('SELECT COUNT(*) FROM kit_templates').fetchone()[0] == 0


# ── Importing ────────────────────────────────────────────

def test_seeding_creates_the_template_and_its_contents(conn, orks):
    report = seeder.seed(conn, {'kits': [entry()]})

    assert report['created'] == 1
    template = conn.execute('SELECT * FROM kit_templates').fetchone()
    assert template['name'] == 'Combat Patrol: Orks'
    assert template['year'] == 2024
    models = conn.execute(
        'SELECT SUM(model_count) FROM kit_template_units').fetchone()[0]
    assert models == 21


def test_provenance_is_recorded_on_the_template(conn, orks):
    """Months from now the shopping list says "buy this" on top of these
    contents, and the claim has to be traceable."""
    seeder.seed(conn, {'kits': [entry()]})

    template = conn.execute('SELECT * FROM kit_templates').fetchone()
    assert template['contents_source'] == 'seed'
    assert template['contents_confidence'] == 'high'
    assert 'example.invalid' in template['contents_source_urls']


def test_a_corroborated_barcode_is_linked(conn, orks):
    seeder.seed(conn, {'kits': [entry(
        barcode='5011921204021',
        barcode_sources=['https://a.invalid', 'https://b.invalid'])]})

    import scanning as scan
    assert scan.template_for_code(conn, '5011921204021')['name'] \
        == 'Combat Patrol: Orks'


def test_re_running_updates_rather_than_duplicating(conn, orks):
    data = {'kits': [entry()]}
    seeder.seed(conn, data)

    report = seeder.seed(conn, data)

    assert (report['created'], report['updated']) == (0, 1)
    assert conn.execute('SELECT COUNT(*) FROM kit_templates').fetchone()[0] == 1


def test_the_same_name_in_a_different_year_is_a_different_box(conn, orks):
    """Combat Patrol: Orks is a 2021 box and a 2024 box with completely
    different contents, and Clay owns both."""
    seeder.seed(conn, {'kits': [entry(year=2021), entry(year=2024)]})

    assert conn.execute('SELECT COUNT(*) FROM kit_templates').fetchone()[0] == 2


def test_seeding_creates_no_kits_and_no_models(conn, orks):
    """A catalogue says what a box holds. It never asserts Clay owns one."""
    seeder.seed(conn, {'kits': [entry()]})

    assert conn.execute('SELECT COUNT(*) FROM kits').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 0


# ── The shipped data file ────────────────────────────────

def test_the_shipped_catalogue_passes_its_own_rules():
    """Whatever is in the file, it obeys the provenance rules — that is what
    makes it safe to trust when it grows."""
    data = seeder.load_data()
    problems = []
    for index, row in enumerate(data.get('kits') or [], start=1):
        problems += seeder.check_entry(row, index)
    assert problems == []


def test_every_shipped_barcode_is_corroborated():
    for row in seeder.load_data().get('kits') or []:
        if (row.get('barcode') or '').strip():
            assert len(row.get('barcode_sources') or []) >= 2, row['name']
