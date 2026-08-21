"""Barcodes, the scan sprint queue, and kit templates.

Most of these guard rules from §12 that exist because getting them wrong
corrupts the collection quietly: never reject a real box, never invent
contents, never let a name-keyed guess decide what Clay owns.
"""

import pytest

import collection as col
import database as db
import scanning as scan


@pytest.fixture
def orks(conn):
    faction_id = db.upsert_faction(conn, 'Orks', 'orks')
    ids = {}
    for name, effort in (('Boyz', 1), ('Deff Dread', 8), ('Beast Snagga Boyz', 1)):
        cur = conn.execute(
            'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
            'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
            (name.lower(), name, faction_id, effort, db.now(), db.now()))
        ids[name] = cur.lastrowid
    return {'faction_id': faction_id, **ids}


@pytest.fixture
def template(conn, orks):
    return scan.create_template(
        conn, 'Combat Patrol: Orks', year=2024, faction_id=orks['faction_id'],
        contents=[{'datasheet_id': orks['Beast Snagga Boyz'], 'model_count': 20},
                  {'datasheet_id': orks['Deff Dread'], 'model_count': 1}])


# ── Codes: warn, never reject ────────────────────────────

@pytest.mark.parametrize('raw, expected', [
    ('5011921204021', '5011921204021'),
    ('5011921 204021', '5011921204021'),
    (' 501-1921-204021\n', '5011921204021'),
    ('', ''),
])
def test_normalise_strips_everything_but_digits(raw, expected):
    assert scan.normalise_code(raw) == expected


def test_a_games_workshop_code_passes_clean():
    out = scan.describe_code('5011921204021')
    assert out['looks_like_gw'] is True
    assert out['notes'] == []


def test_an_isbn_is_flagged_as_a_book_not_rejected():
    """Codexes carry ISBN-derived codes. That is information, not an error."""
    out = scan.describe_code('9781839062865')
    assert out['looks_like_gw'] is False
    assert any('book or codex' in n for n in out['notes'])


def test_a_foreign_prefix_warns_rather_than_refusing():
    """Secondhand and non-GW boxes are real. Refusing one Clay is holding is
    worse than shrugging at it."""
    out = scan.describe_code('4006874052004')
    assert out['looks_like_gw'] is False
    assert out['notes'], 'it should say something'
    assert not any('invalid' in n.lower() for n in out['notes'])


def test_a_bad_check_digit_is_reported():
    good = '5011921204021'
    bad = good[:-1] + ('0' if good[-1] != '0' else '1')
    assert scan.describe_code(good)['checksum_ok'] is True
    assert scan.describe_code(bad)['checksum_ok'] is False
    assert any('Check digit' in n for n in scan.describe_code(bad)['notes'])


def test_an_odd_length_is_noted_but_still_usable():
    out = scan.describe_code('12345')
    assert any('digits' in n for n in out['notes'])
    assert out['code'] == '12345'


# ── Capture ──────────────────────────────────────────────

def test_a_scan_lands_on_the_queue_immediately(conn):
    """A reload or a dead battery must not cost Clay the shelf."""
    out = scan.enqueue_scan(conn, '5011921204021')
    assert out['quantity'] == 1 and out['duplicate'] is False
    rows = scan.queue_rows(conn)
    assert len(rows) == 1 and rows[0]['code'] == '5011921204021'


def test_scanning_the_same_box_twice_means_two_boxes(conn):
    scan.enqueue_scan(conn, '5011921204021')
    out = scan.enqueue_scan(conn, '5011921204021')
    assert out['duplicate'] is True and out['quantity'] == 2
    rows = scan.queue_rows(conn)
    assert len(rows) == 1, 'one row, not two'
    assert rows[0]['quantity'] == 2


def test_an_empty_code_is_refused(conn):
    with pytest.raises(ValueError):
        scan.enqueue_scan(conn, '---')


def test_every_code_seen_enters_the_local_table(conn):
    """The local table is the only part of this guaranteed to work in 5 years."""
    scan.enqueue_scan(conn, '5011921204021')
    scan.enqueue_scan(conn, '5011921204021')
    row = conn.execute("SELECT * FROM barcodes WHERE code = '5011921204021'").fetchone()
    assert row['scan_count'] == 2
    assert row['kit_template_id'] is None


def test_a_scan_after_resolution_starts_a_new_row(conn, template):
    """A later scan of the same code is another box, not an edit of a finished one."""
    scan.link_barcode(conn, '5011921204021', template)
    first = scan.enqueue_scan(conn, '5011921204021')
    scan.resolve_queue_row(conn, first['queue_id'])
    second = scan.enqueue_scan(conn, '5011921204021')
    assert second['queue_id'] != first['queue_id']
    assert second['quantity'] == 1


# ── The local lookup table is the trick ──────────────────

def test_a_known_code_arrives_pre_resolved(conn, template):
    scan.link_barcode(conn, '5011921204021', template)
    out = scan.enqueue_scan(conn, '5011921204021')
    assert out['known'] is True
    assert out['name'] == 'Combat Patrol: Orks'
    assert scan.queue_rows(conn)[0]['ready'] is True


def test_an_unknown_code_is_not_ready_and_says_so(conn):
    scan.enqueue_scan(conn, '5011921999999')
    row = scan.queue_rows(conn)[0]
    assert row['ready'] is False and row['template_id'] is None


def test_defining_one_box_resolves_its_shelf_duplicates(conn, orks):
    """The point of the whole design: contents defined once, instant forever."""
    for _ in range(3):
        scan.enqueue_scan(conn, '5011921204021')
    assert scan.queue_rows(conn)[0]['ready'] is False

    template_id = scan.create_template(
        conn, 'Combat Patrol: Orks', year=2024,
        contents=[{'datasheet_id': orks['Boyz'], 'model_count': 20}])
    scan.link_barcode(conn, '5011921204021', template_id)

    row = scan.queue_rows(conn)[0]
    assert row['ready'] is True and row['quantity'] == 3


def test_summary_splits_known_from_unknown(conn, template):
    scan.link_barcode(conn, '5011921204021', template)
    scan.enqueue_scan(conn, '5011921204021')
    scan.enqueue_scan(conn, '5011921204021')
    scan.enqueue_scan(conn, '5011921111111')
    summary = scan.queue_summary(conn)
    assert summary == {'open_rows': 2, 'open_boxes': 3, 'known': 1, 'unknown': 1}


# ── Resolution ───────────────────────────────────────────

def test_resolving_creates_the_kit_and_all_its_models(conn, template):
    scan.link_barcode(conn, '5011921204021', template)
    out = scan.enqueue_scan(conn, '5011921204021')
    kit_ids = scan.resolve_queue_row(conn, out['queue_id'])
    assert len(kit_ids) == 1
    assert conn.execute('SELECT COUNT(*) c FROM models').fetchone()['c'] == 21
    assert {m['stage_name'] for m in col.unit_models(
        conn, conn.execute('SELECT id FROM units LIMIT 1').fetchone()['id'])} \
        == {'On sprue'}


def test_quantity_three_makes_three_kits(conn, template):
    scan.link_barcode(conn, '5011921204021', template)
    out = scan.enqueue_scan(conn, '5011921204021')
    scan.enqueue_scan(conn, '5011921204021')
    scan.enqueue_scan(conn, '5011921204021')
    kit_ids = scan.resolve_queue_row(conn, out['queue_id'])
    assert len(kit_ids) == 3
    assert conn.execute('SELECT COUNT(*) c FROM models').fetchone()['c'] == 63


def test_resolved_rows_are_kept_as_the_audit_trail(conn, template):
    scan.link_barcode(conn, '5011921204021', template)
    out = scan.enqueue_scan(conn, '5011921204021')
    scan.resolve_queue_row(conn, out['queue_id'])
    assert scan.queue_rows(conn) == []
    kept = scan.queue_rows(conn, include_resolved=True)
    assert len(kept) == 1 and kept[0]['resolved_at'] and kept[0]['kit_id']


def test_an_unknown_code_cannot_be_resolved(conn):
    out = scan.enqueue_scan(conn, '5011921999999')
    with pytest.raises(ValueError, match='no kit template'):
        scan.resolve_queue_row(conn, out['queue_id'])


def test_resolving_twice_is_refused(conn, template):
    scan.link_barcode(conn, '5011921204021', template)
    out = scan.enqueue_scan(conn, '5011921204021')
    scan.resolve_queue_row(conn, out['queue_id'])
    with pytest.raises(ValueError, match='already been resolved'):
        scan.resolve_queue_row(conn, out['queue_id'])


def test_a_misscan_can_be_discarded(conn):
    out = scan.enqueue_scan(conn, '5011921999999')
    scan.discard_queue_row(conn, out['queue_id'])
    assert scan.queue_rows(conn) == []


def test_a_resolved_row_cannot_be_discarded(conn, template):
    """It is history now, and history is not editable."""
    scan.link_barcode(conn, '5011921204021', template)
    out = scan.enqueue_scan(conn, '5011921204021')
    scan.resolve_queue_row(conn, out['queue_id'])
    scan.discard_queue_row(conn, out['queue_id'])
    assert len(scan.queue_rows(conn, include_resolved=True)) == 1


# ── Templates: never invented, never empty ───────────────

def test_a_template_records_what_is_in_the_box(conn, template):
    got = scan.get_template(conn, template)
    assert got['name'] == 'Combat Patrol: Orks' and got['year'] == 2024
    assert [(c['datasheet_name'], c['model_count']) for c in got['contents']] == \
        [('Beast Snagga Boyz', 20), ('Deff Dread', 1)]


def test_a_template_with_no_contents_is_refused(conn):
    """An empty template silently creates empty kits, which looks like success."""
    with pytest.raises(ValueError, match='at least one unit'):
        scan.create_template(conn, 'Mystery Box', contents=[])


def test_a_template_needs_a_name(conn, orks):
    with pytest.raises(ValueError, match='name'):
        scan.create_template(conn, '  ', contents=[
            {'datasheet_id': orks['Boyz'], 'model_count': 1}])


def test_a_zero_model_line_is_refused(conn, orks):
    with pytest.raises(ValueError, match='at least one model'):
        scan.create_template(conn, 'Box', contents=[
            {'datasheet_id': orks['Boyz'], 'model_count': 0}])


def test_year_separates_two_boxes_with_the_same_name(conn, orks):
    """Combat Patrol: Orks is a 2021 box and a 2024 box, and Clay owns both.

    They must be two templates behind two barcodes — never one name-keyed row.
    """
    old = scan.create_template(
        conn, 'Combat Patrol: Orks', year=2021,
        contents=[{'datasheet_id': orks['Boyz'], 'model_count': 20},
                  {'datasheet_id': orks['Deff Dread'], 'model_count': 1}])
    new = scan.create_template(
        conn, 'Combat Patrol: Orks', year=2024,
        contents=[{'datasheet_id': orks['Beast Snagga Boyz'], 'model_count': 20}])
    scan.link_barcode(conn, '5011921204021', new)
    scan.link_barcode(conn, '5011921111111', old)

    assert scan.template_for_code(conn, '5011921204021')['year'] == 2024
    assert scan.template_for_code(conn, '5011921111111')['year'] == 2021
    assert len(scan.get_template(conn, old)['contents']) == 2
    assert len(scan.get_template(conn, new)['contents']) == 1


def test_editing_contents_replaces_them_wholesale(conn, template, orks):
    scan.update_template(conn, template, contents=[
        {'datasheet_id': orks['Boyz'], 'model_count': 10}])
    got = scan.get_template(conn, template)
    assert [(c['datasheet_name'], c['model_count']) for c in got['contents']] == \
        [('Boyz', 10)]


def test_a_template_cannot_be_emptied_by_an_edit(conn, template):
    with pytest.raises(ValueError, match='at least one unit'):
        scan.update_template(conn, template, contents=[])
    assert len(scan.get_template(conn, template)['contents']) == 2


def test_templates_track_where_their_contents_came_from(conn, orks):
    """When the shopping list later says "buy this", the claim must be traceable."""
    template_id = scan.create_template(
        conn, 'Wrecka Krew', contents=[{'datasheet_id': orks['Boyz'], 'model_count': 5}],
        contents_source='ean_lookup', contents_confidence='medium',
        source_urls=['https://example.test/a', 'https://example.test/b'])
    got = scan.get_template(conn, template_id)
    assert got['contents_source'] == 'ean_lookup'
    assert got['contents_confidence'] == 'medium'
    assert len(got['source_urls']) == 2


def test_a_template_lists_the_barcodes_that_point_at_it(conn, template):
    scan.link_barcode(conn, '5011921204021', template)
    scan.link_barcode(conn, '5011921204038', template)
    assert len(scan.get_template(conn, template)['barcodes']) == 2


# ── The lookup seam ──────────────────────────────────────

def test_lookup_is_optional_and_returns_nothing_by_default():
    """Onboarding must work identically when the lookup gives nothing."""
    assert scan.lookup_code('5011921204021') is None
