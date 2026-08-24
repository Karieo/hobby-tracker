"""What is inside a box, defined by hand.

This was the back half of onboarding. The front half was a barcode scanner —
capture a code, queue it, recognise the box, adopt its contents — and Clay
removed it: "The scanning doesn't work well and I would just rather look up
the contents at the time of purchase and add them in manually. Must faster."

He is right that it was the slow path. Scanning only paid off when the app
already knew the box, which meant researched contents behind every barcode,
which is the thing that could never keep up with what he was actually buying.
Typing what is in a box he has in his hands needs no lookup to answer and no
camera to focus.

So what is left is the part that was always hand-made: a template naming a
box and the units inside it, and `instantiate_template` turning one into a kit
with its models. No codes, no queue, no camera.

`barcodes` outlives its readers here on purpose. The table is inert with
nothing querying it, and dropping it is a migration that destroys the codes
already linked to templates — a separate decision, not a side effect of
deleting a screen.
"""

import json

import database as db


# ── Kit templates ────────────────────────────────────────
#
# Built manually first, deliberately. Onboarding must never depend on automation
# working: an EAN lookup can return nothing, a vision model can be confidently
# wrong, and either way Clay is standing at a shelf with a box in his hand.

def list_templates(conn, query=None, faction_id=None, owned=None,
                   with_contents=False):
    """Boxes the app knows about, and how many of each Clay has.

    The same query serves two screens with different questions. `/templates`
    asks "what have I defined", which is bookkeeping. `/catalogue` asks "what
    exists, and do I have it" — which is the question a catalogue is *for*, and
    the one that turns researched contents into something that pays Clay back
    rather than sitting in a dropdown.

    ``owned`` filters to 'yes' or 'no'. Owning is counted from kits rather than
    models because the unit here is a box: two Combat Patrols are two boxes
    even after the models scatter into different armies. Disposed kits do not
    count — a sold box is one he no longer has, which is the whole point of
    asking.
    """
    sql = """
        SELECT t.*, f.name AS faction_name,
               (SELECT COUNT(*) FROM kit_template_units u
                 WHERE u.kit_template_id = t.id)            AS unit_count,
               (SELECT COALESCE(SUM(model_count), 0) FROM kit_template_units u
                 WHERE u.kit_template_id = t.id)            AS model_count,
               (SELECT COUNT(*) FROM barcodes b
                 WHERE b.kit_template_id = t.id)            AS barcode_count,
               (SELECT COUNT(*) FROM kits k
                 WHERE k.kit_template_id = t.id
                   AND k.status = 'owned')                  AS owned_count,
               (SELECT COUNT(*) FROM models m
                  JOIN units un ON un.id = m.unit_id
                  JOIN stages s ON s.id = m.stage_id AND s.is_owned = 0
                 WHERE un.datasheet_id IN (
                     SELECT datasheet_id FROM kit_template_units
                      WHERE kit_template_id = t.id))        AS wanted_count
          FROM kit_templates t LEFT JOIN factions f ON f.id = t.faction_id
    """
    where, args = [], []
    if query:
        # Name *or* a unit inside it. On a catalogue "Boyz" is the obvious
        # thing to type, and the box that holds them is called Combat Patrol:
        # Orks — a name-only search answers "no such box" to a question the
        # data can answer perfectly well.
        where.append("""(t.name LIKE ? OR EXISTS (
            SELECT 1 FROM kit_template_units u JOIN datasheets d ON d.id = u.datasheet_id
             WHERE u.kit_template_id = t.id AND d.name LIKE ?))""")
        args += [f'%{query.strip()}%'] * 2
    if faction_id:
        where.append('t.faction_id = ?')
        args.append(faction_id)
    if owned == 'yes':
        where.append('owned_count > 0')
    elif owned == 'no':
        where.append('owned_count = 0')
    if where:
        sql += ' WHERE ' + ' AND '.join(where)

    rows = [dict(r) for r in conn.execute(sql + ' ORDER BY t.name, t.year', args)]
    if with_contents:
        by_template = {}
        for row in conn.execute("""
            SELECT u.kit_template_id, u.model_count, d.name AS datasheet_name
              FROM kit_template_units u
              JOIN datasheets d ON d.id = u.datasheet_id
             ORDER BY u.id
        """):
            by_template.setdefault(row['kit_template_id'], []).append(dict(row))
        for row in rows:
            row['contents'] = by_template.get(row['id'], [])
    return rows


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
