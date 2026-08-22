"""Barcodes, the scan sprint queue, and kit templates.

Onboarding ~100 boxes is the job this module exists for, and the naive shape —
scan, modal, fill a form, save, repeat — turns an afternoon into a week and the
project dies half-catalogued. So capture is split from enrichment:

**Capture** writes a row and gets out of the way. The camera never closes, each
decode lands on ``scan_queue`` immediately, and Clay keeps turning boxes over. A
reload or a dead battery must not cost him the shelf, so every scan is written
to the server the moment it decodes — nothing is buffered in the page.

**Enrichment** happens later at a keyboard. Known codes are already resolved and
need only a confirmation; unknown ones need contents defined once, after which
every other copy of that box on the shelf resolves behind it automatically.

The local ``barcodes`` table is the point. Unknown code once, contents defined
once, instant forever after — and unlike any external GTIN provider, it is
guaranteed to still work in five years.
"""

import json
import re

import collection as col
import database as db

# Games Workshop's EAN prefix: GB country code plus their company code. Books
# and codexes are the exception — they carry ISBN-derived codes starting 978/979.
GW_EAN_PREFIX = '5011921'
BOOK_PREFIXES = ('978', '979')


# ── Codes ────────────────────────────────────────────────

def normalise_code(raw):
    """Digits only. Scanners and humans both add spaces, dashes and newlines."""
    return re.sub(r'\D', '', raw or '')


def _checksum_ok(code):
    """GTIN check digit. EAN-13 and UPC-A share the algorithm once padded."""
    if len(code) not in (8, 12, 13, 14):
        return None                      # not a length we can check
    padded = code.zfill(14)
    total = sum(int(d) * (3 if i % 2 == 0 else 1)
                for i, d in enumerate(padded[:-1]))
    return (10 - total % 10) % 10 == int(padded[-1])


def describe_code(code):
    """Sanity notes for a code. Never a rejection.

    An unexpected prefix means *check this*, not *invalid* — Clay's codexes are
    ISBNs, secondhand boxes carry other companies' codes, and a scanner that
    refuses a real box he is holding is worse than one that shrugs. Everything
    here is advisory and the code is stored either way.
    """
    notes = []
    if not code:
        return {'code': code, 'notes': ['Empty code'], 'looks_like_gw': False}

    if len(code) not in (8, 12, 13, 14):
        notes.append(f'{len(code)} digits — not a standard EAN-13 or UPC-A length')

    checksum = _checksum_ok(code)
    if checksum is False:
        notes.append('Check digit does not match — possible misread, worth '
                     'rescanning or retyping')

    looks_like_gw = code.startswith(GW_EAN_PREFIX)
    if not looks_like_gw:
        if code.startswith(BOOK_PREFIXES):
            notes.append('ISBN-derived code — a book or codex rather than a kit')
        else:
            notes.append(f'Does not start {GW_EAN_PREFIX} (the Games Workshop '
                         'prefix) — fine for secondhand or non-GW boxes, worth '
                         'a glance otherwise')
    return {'code': code, 'notes': notes, 'looks_like_gw': looks_like_gw,
            'checksum_ok': checksum}


# ── Capture ──────────────────────────────────────────────

def enqueue_scan(conn, raw_code, photo_url=None):
    """Record one decode. Returns what the camera page needs to show.

    Scanning the same box twice means Clay owns two of them, so a repeat bumps
    the quantity on the open queue row rather than creating a second one. A row
    that has already been resolved is left alone — a later scan of the same code
    is a new box, not an edit of a finished one.
    """
    code = normalise_code(raw_code)
    if not code:
        raise ValueError('No digits in that code')

    open_row = conn.execute(
        'SELECT * FROM scan_queue WHERE code = ? AND resolved_at IS NULL '
        'ORDER BY id LIMIT 1', (code,)).fetchone()
    if open_row:
        conn.execute('UPDATE scan_queue SET quantity = quantity + 1, '
                     'scanned_at = ? WHERE id = ?', (db.now(), open_row['id']))
        queue_id, quantity, duplicate = open_row['id'], open_row['quantity'] + 1, True
    else:
        cur = conn.execute(
            'INSERT INTO scan_queue (code, quantity, scanned_at, photo_url) '
            'VALUES (?, 1, ?, ?)', (code, db.now(), photo_url))
        queue_id, quantity, duplicate = cur.lastrowid, 1, False

    _touch_barcode(conn, code)
    template = template_for_code(conn, code)
    return {'queue_id': queue_id, 'code': code, 'quantity': quantity,
            'duplicate': duplicate,
            'known': template is not None,
            'name': template['name'] if template else None,
            **describe_code(code)}


def _touch_barcode(conn, code):
    """Every code seen goes in the local table, resolved or not."""
    row = conn.execute('SELECT id FROM barcodes WHERE code = ?', (code,)).fetchone()
    if row:
        conn.execute('UPDATE barcodes SET scan_count = scan_count + 1 WHERE id = ?',
                     (row['id'],))
    else:
        conn.execute('INSERT INTO barcodes (code, first_scanned_at, scan_count) '
                     'VALUES (?, ?, 1)', (code, db.now()))


def template_for_code(conn, code):
    row = conn.execute("""
        SELECT t.* FROM barcodes b JOIN kit_templates t ON t.id = b.kit_template_id
         WHERE b.code = ?
    """, (normalise_code(code),)).fetchone()
    return dict(row) if row else None


def link_barcode(conn, code, kit_template_id):
    """Teach the local table what a code is. This is the whole trick.

    Once a code points at a template, every future scan of that box — including
    the three other copies still on the shelf — resolves with no typing at all.
    """
    code = normalise_code(code)
    _ensure_barcode(conn, code)
    conn.execute('UPDATE barcodes SET kit_template_id = ? WHERE code = ?',
                 (kit_template_id, code))


def _ensure_barcode(conn, code):
    if not conn.execute('SELECT 1 FROM barcodes WHERE code = ?', (code,)).fetchone():
        conn.execute('INSERT INTO barcodes (code, first_scanned_at, scan_count) '
                     'VALUES (?, ?, 0)', (code, db.now()))


# ── The review queue ─────────────────────────────────────

def queue_rows(conn, include_resolved=False):
    """The enrichment screen's data: what is waiting, and what is known.

    Rows already backed by a template come pre-resolved — Clay confirms rather
    than types. The rest need contents defining once each.
    """
    sql = """
        SELECT q.*, t.id AS template_id, t.name AS template_name, t.year,
               t.contents_source, t.contents_confidence,
               f.name AS faction_name,
               (SELECT COALESCE(SUM(model_count), 0) FROM kit_template_units ktu
                 WHERE ktu.kit_template_id = t.id) AS template_models,
               (SELECT COUNT(*) FROM kit_template_units ktu
                 WHERE ktu.kit_template_id = t.id)  AS template_units,
               k.name AS kit_name
          FROM scan_queue q
          LEFT JOIN barcodes b      ON b.code = q.code
          LEFT JOIN kit_templates t ON t.id = b.kit_template_id
          LEFT JOIN factions f      ON f.id = t.faction_id
          LEFT JOIN kits k          ON k.id = q.kit_id
    """
    if not include_resolved:
        sql += ' WHERE q.resolved_at IS NULL'
    rows = [dict(r) for r in conn.execute(sql + ' ORDER BY q.resolved_at IS NOT NULL, '
                                          'q.id')]
    for row in rows:
        row.update(describe_code(row['code']))
        # A template with no contents cannot be instantiated — it would create a
        # kit holding nothing, which looks like it worked.
        row['ready'] = bool(row['template_id']) and row['template_units'] > 0
    return rows


def queue_summary(conn):
    row = conn.execute("""
        SELECT COUNT(*) AS open_rows,
               COALESCE(SUM(quantity), 0) AS open_boxes
          FROM scan_queue WHERE resolved_at IS NULL
    """).fetchone()
    known = conn.execute("""
        SELECT COUNT(*) AS n FROM scan_queue q
          JOIN barcodes b ON b.code = q.code
         WHERE q.resolved_at IS NULL AND b.kit_template_id IS NOT NULL
    """).fetchone()['n']
    return {'open_rows': row['open_rows'], 'open_boxes': row['open_boxes'],
            'known': known, 'unknown': row['open_rows'] - known}


def resolve_queue_row(conn, queue_id, army_id=None, stage_id=None, **kit_fields):
    """Turn a queued scan into owned kits and models.

    ``quantity`` copies are instantiated, because scanning a box three times
    means three boxes on the shelf. The queue row is kept and marked resolved
    rather than deleted — it is the audit trail for how the collection was
    built.
    """
    row = conn.execute('SELECT * FROM scan_queue WHERE id = ?', (queue_id,)).fetchone()
    if not row:
        raise ValueError(f'no queue row {queue_id}')
    if row['resolved_at']:
        raise ValueError('that scan has already been resolved')

    template = template_for_code(conn, row['code'])
    if not template:
        raise ValueError('no kit template for this barcode yet — define its '
                         'contents first')

    kit_ids = []
    for _ in range(max(1, row['quantity'])):
        kit_id, _units = col.instantiate_template(
            conn, template['id'], army_id=army_id, stage_id=stage_id, **kit_fields)
        kit_ids.append(kit_id)

    conn.execute('UPDATE scan_queue SET resolved_at = ?, kit_id = ? WHERE id = ?',
                 (db.now(), kit_ids[0], queue_id))
    return kit_ids


def shelve_queue_row(conn, queue_id, name=None, **kit_fields):
    """Record the box as owned without saying what is inside it.

    The escape hatch from the only step in onboarding that cannot be made
    cheap. Defining contents costs a form per distinct product, and with ~100
    boxes and no catalogue to seed from that is hours of typing before the app
    has recorded a single thing — which is how the last tracker died.

    So ownership is recorded on its own: the box exists, it is on the shelf, it
    is at "On sprue" honestly, and `collection.adopt_template` fills in the
    contents whenever they become known. No contents are invented, none are
    guessed, and nothing is auto-saved from a lookup — the box simply says what
    it can prove.

    `quantity` copies, same as resolving: three scans of the same box are three
    boxes.
    """
    row = conn.execute('SELECT * FROM scan_queue WHERE id = ?', (queue_id,)).fetchone()
    if not row:
        raise ValueError(f'no queue row {queue_id}')
    if row['resolved_at']:
        raise ValueError('that scan has already been resolved')

    label = (name or '').strip() or f'{col.UNIDENTIFIED_PREFIX} {row["code"]}'
    kit_fields.setdefault('source_ref', row['code'])
    kit_ids = [col.create_kit(conn, label, **kit_fields)
               for _ in range(max(1, row['quantity']))]

    conn.execute('UPDATE scan_queue SET resolved_at = ?, kit_id = ? WHERE id = ?',
                 (db.now(), kit_ids[0], queue_id))
    return kit_ids


def discard_queue_row(conn, queue_id):
    """For a mis-scan — a code that turned out not to be a box Clay owns."""
    conn.execute('DELETE FROM scan_queue WHERE id = ? AND resolved_at IS NULL',
                 (queue_id,))


def set_queue_quantity(conn, queue_id, quantity):
    conn.execute('UPDATE scan_queue SET quantity = ? WHERE id = ? '
                 'AND resolved_at IS NULL', (max(1, quantity), queue_id))


def sweep_queue(conn, army_id=None, stage_id=None, **kit_fields):
    """Every open row in one action: template-backed rows are confirmed into
    kits and models, unknown ones are shelved as owned boxes awaiting contents.

    The per-row buttons survive for the odd box out, but on onboarding day the
    queue holds a hundred rows and a tap per row is a hundred taps — exactly
    the per-box cost that splitting capture from enrichment exists to remove.
    The review screen's defaults apply to everything, same as confirming rows
    one at a time.

    A template with no contents defined counts as unknown here, mirroring
    ``queue_rows``: resolving it would fail, and shelving the box keeps it
    honestly recorded until the contents exist.
    """
    confirmed, shelved = [], []
    for row in queue_rows(conn):
        if row['ready']:
            confirmed += resolve_queue_row(conn, row['id'], army_id=army_id,
                                           stage_id=stage_id, **kit_fields)
        else:
            shelved += shelve_queue_row(conn, row['id'], **kit_fields)
    return {'confirmed': confirmed, 'shelved': shelved}


# ── Kit templates ────────────────────────────────────────
#
# Built manually first, deliberately. Onboarding must never depend on automation
# working: an EAN lookup can return nothing, a vision model can be confidently
# wrong, and either way Clay is standing at a shelf with a box in his hand.

def list_templates(conn, query=None):
    sql = """
        SELECT t.*, f.name AS faction_name,
               (SELECT COUNT(*) FROM kit_template_units u
                 WHERE u.kit_template_id = t.id)            AS unit_count,
               (SELECT COALESCE(SUM(model_count), 0) FROM kit_template_units u
                 WHERE u.kit_template_id = t.id)            AS model_count,
               (SELECT COUNT(*) FROM barcodes b
                 WHERE b.kit_template_id = t.id)            AS barcode_count
          FROM kit_templates t LEFT JOIN factions f ON f.id = t.faction_id
    """
    args = []
    if query:
        sql += ' WHERE t.name LIKE ?'
        args.append(f'%{query.strip()}%')
    return [dict(r) for r in conn.execute(sql + ' ORDER BY t.name, t.year', args)]


def get_template(conn, template_id):
    row = conn.execute(
        'SELECT t.*, f.name AS faction_name FROM kit_templates t '
        'LEFT JOIN factions f ON f.id = t.faction_id WHERE t.id = ?',
        (template_id,)).fetchone()
    if not row:
        return None
    template = dict(row)
    template['contents'] = [dict(r) for r in conn.execute("""
        SELECT u.*, d.name AS datasheet_name, d.effort, f.name AS faction_name
          FROM kit_template_units u
          JOIN datasheets d    ON d.id = u.datasheet_id
          LEFT JOIN factions f ON f.id = d.faction_id
         WHERE u.kit_template_id = ? ORDER BY u.id
    """, (template_id,))]
    template['barcodes'] = [dict(r) for r in conn.execute(
        'SELECT * FROM barcodes WHERE kit_template_id = ? ORDER BY code',
        (template_id,))]
    template['source_urls'] = json.loads(template['contents_source_urls'] or '[]')
    return template


def create_template(conn, name, contents, faction_id=None, year=None,
                    rrp_cents=None, contents_source='manual',
                    contents_confidence='high', source_urls=None, notes=None):
    """Define a box: what it is, and what is inside it.

    ``contents`` is a list of ``{datasheet_id, model_count}``. Every line must
    point at a real imported datasheet — a template is what the shopping list
    later inverts to say "buy this", so a made-up unit name here becomes bad
    purchase advice months from now.
    """
    if not name or not name.strip():
        raise ValueError('a kit template needs a name')
    contents = [c for c in (contents or []) if c.get('datasheet_id')]
    if not contents:
        raise ValueError('a kit template needs at least one unit — an empty one '
                         'would silently create empty kits')

    cur = conn.execute(
        'INSERT INTO kit_templates (name, faction_id, rrp_cents, price_updated_on, '
        'year, contents_source, contents_confidence, contents_source_urls, notes, '
        'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (name.strip(), faction_id, rrp_cents,
         db.now()[:10] if rrp_cents else None, year, contents_source,
         contents_confidence, json.dumps(source_urls) if source_urls else None,
         notes, db.now(), db.now()))
    template_id = cur.lastrowid
    _write_contents(conn, template_id, contents)
    return template_id


def update_template(conn, template_id, name=None, faction_id=None, year=None,
                    rrp_cents=None, notes=None, contents=None):
    template = conn.execute('SELECT * FROM kit_templates WHERE id = ?',
                            (template_id,)).fetchone()
    if not template:
        raise ValueError(f'no kit template {template_id}')
    conn.execute(
        'UPDATE kit_templates SET name = ?, faction_id = ?, year = ?, '
        'rrp_cents = ?, price_updated_on = ?, notes = ?, updated_at = ? WHERE id = ?',
        (name.strip() if name else template['name'],
         faction_id if faction_id is not None else template['faction_id'],
         year if year is not None else template['year'],
         rrp_cents if rrp_cents is not None else template['rrp_cents'],
         db.now()[:10] if rrp_cents is not None else template['price_updated_on'],
         notes if notes is not None else template['notes'],
         db.now(), template_id))
    if contents is not None:
        contents = [c for c in contents if c.get('datasheet_id')]
        if not contents:
            raise ValueError('a kit template needs at least one unit')
        conn.execute('DELETE FROM kit_template_units WHERE kit_template_id = ?',
                     (template_id,))
        _write_contents(conn, template_id, contents)
    return template_id


def _write_contents(conn, template_id, contents):
    for line in contents:
        count = int(line.get('model_count') or 0)
        if count < 1:
            raise ValueError('every unit in a kit needs at least one model')
        conn.execute(
            'INSERT INTO kit_template_units (kit_template_id, datasheet_id, '
            'model_count) VALUES (?, ?, ?)',
            (template_id, int(line['datasheet_id']), count))


# ── External GTIN lookup ─────────────────────────────────

def lookup_code(code):
    """Optional enrichment. Never a dependency.

    One seam, so the provider can be swapped when the current one is retired —
    and they do get retired. Nothing is wired up yet: onboarding works
    identically when this returns nothing, which is the whole design rule, and
    the local barcodes table is the actual source of truth.

    Whatever a provider returns is a *name*, never contents. A name saves typing
    "Combat Patrol: Orks"; it cannot say there are 20 Beast Snagga Boyz inside,
    and no name-keyed lookup ever gets to decide that — Combat Patrol: Orks is
    both a 2021 and a 2024 box and Clay owns both.
    """
    return None
