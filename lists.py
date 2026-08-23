"""Army lists, and the gap between a list and the collection.

Spec §2.6, the keystone. Every other part of the app pushes: a model moves
forward when Clay happens to feel like moving it. A list is the only thing that
*pulls* — it names a target and turns "what should I work on" from a mood into
an answer.

The gap it computes has two halves, and keeping them apart is the point:

    buy    — models Clay does not own at all
    paint  — models he owns that are not battle ready yet

They lead to different evenings. One is a trip to a shop, the other is a night
at the desk, and a single "you are not ready" number would hide which.

A known simplification, recorded rather than hidden: ownership is compared
against the whole collection, so the same ten Boyz satisfy two lists at once.
That is right for how Clay actually plays — one list at a time, models reused —
and wrong for someone building two armies in parallel. When that matters it
becomes an allocation problem, and this is deliberately not that yet.
"""

import collection as col
import database as db
import list_allocate


def create_list(conn, name, faction_id=None, detachment=None, points_limit=None,
                notes=None, raw_text=None, source_format=None,
                points_total=None):
    """A list, and — when it came from a paste — the text it came from.

    `raw_text` is stored deliberately, per spec §8: "When the parser gets
    better, old lists can be re-parsed without re-pasting." It is nullable
    because a list built by hand has no pasted text and is not a defective row.
    `points_total` is what the export declared, kept beside this app's own
    figure and never instead of it.
    """
    if not (name or '').strip():
        raise ValueError('a list needs a name')
    stamp = db.now()
    cur = conn.execute(
        'INSERT INTO army_lists (name, faction_id, detachment, points_limit, '
        'notes, raw_text, source_format, points_total, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (name.strip(), faction_id, detachment, points_limit, notes, raw_text,
         source_format, points_total, stamp, stamp))
    return cur.lastrowid


def get_list(conn, list_id):
    row = conn.execute(
        'SELECT l.*, f.name AS faction_name FROM army_lists l '
        'LEFT JOIN factions f ON f.id = l.faction_id WHERE l.id = ?',
        (list_id,)).fetchone()
    return dict(row) if row else None


def list_lists(conn):
    """Every list with its headline: points, and how ready it is."""
    rows = [dict(r) for r in conn.execute("""
        SELECT l.*, f.name AS faction_name,
               COUNT(e.id)                       AS entry_count,
               COALESCE(SUM(e.points_snapshot), 0) AS points_total,
               COALESCE(SUM(e.model_count), 0)   AS model_count
          FROM army_lists l
          LEFT JOIN factions f    ON f.id = l.faction_id
          LEFT JOIN list_entries e ON e.list_id = l.id
         GROUP BY l.id
         ORDER BY l.created_at DESC, l.id DESC
    """)]
    for row in rows:
        gap = list_gap(conn, row['id'])
        row['to_buy'] = gap['to_buy']
        row['to_paint'] = gap['to_paint']
        row['fieldable'] = gap['fieldable']
        row['ready'] = gap['ready']
        row['unresolved'] = gap['unresolved']
        row['swaps'] = gap['swaps']
        # Staleness, per spec §8's "saved lists with a staleness indicator".
        # The report is recomputed on every load, so what can go stale is the
        # *pricing*: a list snapshotted its points when it was built, and the
        # manual has moved since if the newest points row is newer than it.
        row['priced_before'] = conn.execute(
            'SELECT MAX(effective_from) FROM datasheet_points').fetchone()[0]
        row['stale'] = bool(row['priced_before'] and row['created_at']
                            and row['priced_before'] > row['created_at'][:10])
    return rows


def delete_list(conn, list_id):
    """Entries cascade. Wishlist models raised from this list do not — Clay
    still wants them, and they are his to clear."""
    conn.execute('UPDATE models SET wishlist_source_list_id = NULL '
                 'WHERE wishlist_source_list_id = ?', (list_id,))
    conn.execute('DELETE FROM army_lists WHERE id = ?', (list_id,))


# ── Entries ──────────────────────────────────────────────

def points_for(conn, datasheet_id, model_count, faction_id=None):
    """The Munitorum price for this many models, or None.

    Faction-scoped first: 35 datasheet names carry different points per
    faction, and a Repulsor Executioner is 255 for Black Templars and 230 for
    Blood Angels. Falling back to an unscoped row is better than showing
    nothing, but never in preference to the faction's own.
    """
    for args in ((datasheet_id, model_count, faction_id),
                 (datasheet_id, model_count, None)):
        sheet_id, count, faction = args
        row = conn.execute(
            'SELECT points FROM datasheet_points '
            ' WHERE datasheet_id = ? AND model_count = ? '
            f'   AND faction_id IS {"?" if faction else "NULL"} '
            ' ORDER BY tier_min LIMIT 1',
            (sheet_id, count, faction) if faction else (sheet_id, count)
        ).fetchone()
        if row:
            return row['points']
    return None


def add_entry(conn, list_id, datasheet_id, model_count, is_proxy=False,
              raw_name=None, points=None):
    """Put a unit in the list. Points are snapshotted at the time of adding.

    A snapshot rather than a live lookup because a list is a record of what
    Clay intended to field on a day, and the Munitorum manual changes under it.

    ``position`` is assigned here rather than left to the column default.
    Migration 008 numbered every existing entry by the order it was added, and
    a writer that then left new ones at 0 would make the column true of old
    data and false of new — the worst state for a column the report is about to
    order by. ``raw_name`` and ``points`` are what a paste said, kept beside
    the app's own figures rather than instead of them; both are None when the
    entry came from the builder, where there was no text to disagree with.
    """
    if not get_list(conn, list_id):
        raise ValueError(f'no list {list_id}')
    if model_count < 1:
        raise ValueError('a list entry needs at least one model')
    sheet = conn.execute('SELECT * FROM datasheets WHERE id = ?',
                         (datasheet_id,)).fetchone()
    if not sheet:
        raise ValueError(f'no datasheet {datasheet_id}')

    position = conn.execute(
        'SELECT COALESCE(MAX(position) + 1, 0) FROM list_entries '
        ' WHERE list_id = ?', (list_id,)).fetchone()[0]
    snapshot = points_for(conn, datasheet_id, model_count, sheet['faction_id'])
    cur = conn.execute(
        'INSERT INTO list_entries (list_id, position, raw_name, datasheet_id, '
        'model_count, points_snapshot, points, resolved_by, is_proxy) '
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?)",
        (list_id, position, raw_name, datasheet_id, model_count, snapshot,
         points, 1 if is_proxy else 0))
    return cur.lastrowid


def remove_entry(conn, entry_id):
    conn.execute('DELETE FROM list_entries WHERE id = ?', (entry_id,))


# ── The gap ──────────────────────────────────────────────

def list_gap(conn, list_id, include_unassigned=True):
    """What stands between this list and the table.

    A thin name over `list_allocate.allocate`, kept because three screens and
    the wishlist all say "the gap" and none of them should have to know how it
    is computed.

    **The arithmetic changed under this name and that was the point.** It used
    to count ownership per entry with nothing consuming a model once assigned,
    so a list asking for two squads of ten Boyz reported "fieldable" against
    ten Boyz owned — and `raise_wishlist` read the same numbers, so the
    shopping list was short by exactly the models Clay would have discovered
    missing at the table. Allocation answers both correctly.
    """
    return list_allocate.allocate(conn, list_id,
                                  include_unassigned=include_unassigned)


# ── Importing a pasted list ──────────────────────────────

def import_list(conn, rows, name, raw_text=None, source_format=None,
                points_total=None, **fields):
    """Turn confirmed rows into a list, and learn from the ones Clay resolved.

    Every row carries the line it came from, so a datasheet Clay picked by hand
    teaches `datasheet_aliases` on the way past. That write-back is the whole
    reason the alias table exists — Section 7: "If you have to re-answer 'which
    datasheet is *Warboss on Warbike*?' every time you paste a list, you'll
    stop pasting lists."

    The pasted points are stored beside each entry and never totalled. §2.7
    settled that this app prices a list from the Munitorum manual it imported,
    and Clay confirmed it for the gap report: a number copied out of someone
    else's app never outranks the official one.
    """
    import list_resolve

    confirmed = [r for r in rows if r.get('datasheet_id') and not r.get('skip')]
    if not confirmed:
        raise ValueError('nothing to import — every line was skipped or '
                         'unresolved')

    list_id = create_list(conn, name, raw_text=raw_text,
                          source_format=source_format,
                          points_total=points_total, **fields)
    added = []
    for row in confirmed:
        if row.get('resolved_by') == 'manual' and row.get('raw_name'):
            list_resolve.learn_alias(conn, row['raw_name'], row['datasheet_id'])
        added.append(add_entry(conn, list_id, row['datasheet_id'],
                               max(1, int(row.get('model_count') or 1)),
                               raw_name=row.get('raw_name'),
                               points=row.get('points')))
    return {'list_id': list_id, 'entries': added}


def reparse(conn, list_id, game_system='wh40k'):
    """Read the stored text again with today's parser, and replace the entries.

    "`raw_text` is stored deliberately. When the parser gets better, old lists
    can be re-parsed without re-pasting."

    Manual picks are not lost by this: resolving one wrote an alias, and the
    alias is the first thing resolution consults. That is what makes throwing
    the entries away safe — the knowledge lives in the alias table, not in the
    rows.
    """
    import list_parse
    import list_resolve

    row = get_list(conn, list_id)
    if not row:
        raise ValueError(f'no list {list_id}')
    if not row['raw_text']:
        raise ValueError('this list was not pasted, so there is no text to '
                         're-read')

    parsed = list_parse.parse(row['raw_text'])
    resolved = list_resolve.resolve_entries(
        conn, parsed.entries, faction_id=list_resolve.list_faction(conn, list_id),
        game_system=game_system)

    conn.execute('DELETE FROM list_entries WHERE list_id = ?', (list_id,))
    added = unresolved = 0
    for entry in resolved:
        if not entry.datasheet_id:
            # Kept as a row rather than dropped, exactly as on first import: a
            # line the parser could not place is a unit Clay would otherwise
            # turn up to a game without.
            conn.execute(
                'INSERT INTO list_entries (list_id, position, raw_name, '
                'model_count, points) VALUES (?, ?, ?, ?, ?)',
                (list_id, entry.position, entry.raw_name, entry.model_count,
                 entry.points))
            unresolved += 1
            continue
        add_entry(conn, list_id, entry.datasheet_id, entry.model_count,
                  raw_name=entry.raw_name, points=entry.points)
        added += 1
    conn.execute('UPDATE army_lists SET source_format = ?, updated_at = ? '
                 'WHERE id = ?', (parsed.source_format, db.now(), list_id))
    return {'resolved': added, 'unresolved': unresolved,
            'source_format': parsed.source_format}


# ── The wishlist ─────────────────────────────────────────

def _stamp(conn, column, value, model_ids):
    """Record what raised these models, and only these models.

    Wishlist lines are shared now — two lists short of the same unit raise one
    line between them — so stamping by unit would relabel models a different
    list is still waiting on, and `unwant_template` would then delete them.
    """
    if not model_ids:
        return
    marks = ','.join('?' * len(model_ids))
    conn.execute(f'UPDATE models SET {column} = ? WHERE id IN ({marks})',
                 (value, *model_ids))


def raise_wishlist(conn, list_id):
    """Turn the buy half of the gap into wishlist models.

    Wishlist is a real stage with is_owned = 0, so these count as wanted and
    never as owned. `wishlist_source_list_id` records which list raised them,
    which is what tells "you are seven Boyz short for Saturday" apart from
    something Clay put on the list himself — one is satisfied by buying it, the
    other is a standing want.

    Idempotent per list: re-running tops up to the shortfall rather than
    stacking a second copy on top of the first.
    """
    gap = list_gap(conn, list_id)
    wishlist = db.wishlist_stage(conn)
    if not wishlist:
        raise ValueError('no wishlist stage — migration 002 did not run?')

    raised = {}
    for row in conn.execute("""
        SELECT u.datasheet_id, COUNT(m.id) AS n
          FROM models m JOIN units u ON u.id = m.unit_id
         WHERE m.wishlist_source_list_id = ?
         GROUP BY u.datasheet_id
    """, (list_id,)):
        raised[row['datasheet_id']] = row['n']

    added = 0
    for entry in gap['entries']:
        # `short` is what allocation could not cover from anything Clay owns,
        # including plastic that could still become it — so the wishlist asks
        # for what is genuinely missing rather than for everything unbuilt.
        if not entry['datasheet_id']:
            continue
        want = entry['short'] - raised.get(entry['datasheet_id'], 0)
        if want <= 0:
            continue
        # Two lists wanting the same unit join one wishlist line rather than
        # stacking two identical ones on the collection. The stamp goes on the
        # models this call added and no others, so taking one list back off the
        # wishlist cannot take the other list's models with it.
        raised_now = col.add_or_extend_unit(conn, entry['datasheet_id'], want,
                                            stage_id=wishlist['id'])
        _stamp(conn, 'wishlist_source_list_id', list_id,
               raised_now['model_ids'])
        added += want
    return added


def wishlist(conn):
    """Everything wanted but not owned, and what raised it.

    Two kinds of provenance, and they answer different questions. A list says
    *why* Clay wants it — seven Boyz short for Saturday. A kit template says
    *what to buy*: "11 Boyz, 1 Trukk" is a parts list, "Orks: Trukk Boyz" is a
    thing on a shelf in a shop with a price on it.
    """
    return [dict(r) for r in conn.execute("""
        SELECT d.id AS datasheet_id, d.name, d.game_system,
               f.name AS faction_name,
               COUNT(m.id)                                  AS wanted,
               COUNT(m.wishlist_source_list_id)             AS from_lists,
               COUNT(m.wishlist_source_template_id)         AS from_boxes,
               GROUP_CONCAT(DISTINCT l.name)                AS list_names,
               GROUP_CONCAT(DISTINCT t.name)                AS box_names
          FROM models m
          JOIN stages s        ON s.id = m.stage_id AND s.is_owned = 0
          JOIN units u         ON u.id = m.unit_id
          JOIN datasheets d    ON d.id = u.datasheet_id
          LEFT JOIN factions f ON f.id = d.faction_id
          LEFT JOIN army_lists l ON l.id = m.wishlist_source_list_id
          LEFT JOIN kit_templates t ON t.id = m.wishlist_source_template_id
         GROUP BY d.id
         ORDER BY d.name
    """)]


def want_template(conn, template_id):
    """Put a box's contents on the wishlist, and remember which box.

    The catalogue's payback. Browsing "what exists" is only half useful if
    finding something you want leaves you to type its contents in by hand.

    Idempotent per box, the same bargain `raise_wishlist` makes per list:
    running it again tops up to the box's contents rather than stacking a
    second copy. Counted per box rather than per datasheet on purpose — wanting
    two different boxes that both contain Boyz means wanting both boxes, and
    collapsing them would silently under-order.
    """
    template = conn.execute('SELECT * FROM kit_templates WHERE id = ?',
                            (template_id,)).fetchone()
    if not template:
        raise ValueError(f'no kit template {template_id}')
    contents = conn.execute(
        'SELECT * FROM kit_template_units WHERE kit_template_id = ?',
        (template_id,)).fetchall()
    if not contents:
        raise ValueError(f'kit template "{template["name"]}" has no contents '
                         'defined — there is nothing to want')

    wishlist_stage = db.wishlist_stage(conn)
    already = {}
    for row in conn.execute("""
        SELECT u.datasheet_id, COUNT(m.id) AS n
          FROM models m JOIN units u ON u.id = m.unit_id
         WHERE m.wishlist_source_template_id = ?
         GROUP BY u.datasheet_id
    """, (template_id,)):
        already[row['datasheet_id']] = row['n']

    added = 0
    for line in contents:
        want = line['model_count'] - already.get(line['datasheet_id'], 0)
        if want <= 0:
            continue
        raised_now = col.add_or_extend_unit(conn, line['datasheet_id'], want,
                                            stage_id=wishlist_stage['id'])
        _stamp(conn, 'wishlist_source_template_id', template_id,
               raised_now['model_ids'])
        added += want
    return added


def unwant_template(conn, template_id):
    """Take a box back off the wishlist. Only the models it put there."""
    unit_ids = [r['unit_id'] for r in conn.execute(
        'SELECT DISTINCT unit_id FROM models '
        ' WHERE wishlist_source_template_id = ?', (template_id,))]
    cur = conn.execute('DELETE FROM models WHERE wishlist_source_template_id = ?',
                       (template_id,))
    for unit_id in unit_ids:
        if not conn.execute('SELECT 1 FROM models WHERE unit_id = ?',
                            (unit_id,)).fetchone():
            conn.execute('DELETE FROM units WHERE id = ?', (unit_id,))
    return cur.rowcount
