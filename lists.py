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


def create_list(conn, name, faction_id=None, detachment=None, points_limit=None,
                notes=None):
    if not (name or '').strip():
        raise ValueError('a list needs a name')
    cur = conn.execute(
        'INSERT INTO army_lists (name, faction_id, detachment, points_limit, '
        'notes, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        (name.strip(), faction_id, detachment, points_limit, notes, db.now()))
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
        row['fieldable'] = gap['to_buy'] == 0
        row['ready'] = gap['to_buy'] == 0 and gap['to_paint'] == 0
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


def add_entry(conn, list_id, datasheet_id, model_count, is_proxy=False):
    """Put a unit in the list. Points are snapshotted at the time of adding.

    A snapshot rather than a live lookup because a list is a record of what
    Clay intended to field on a day, and the Munitorum manual changes under it.
    """
    if not get_list(conn, list_id):
        raise ValueError(f'no list {list_id}')
    if model_count < 1:
        raise ValueError('a list entry needs at least one model')
    sheet = conn.execute('SELECT * FROM datasheets WHERE id = ?',
                         (datasheet_id,)).fetchone()
    if not sheet:
        raise ValueError(f'no datasheet {datasheet_id}')

    points = points_for(conn, datasheet_id, model_count, sheet['faction_id'])
    cur = conn.execute(
        'INSERT INTO list_entries (list_id, datasheet_id, model_count, '
        'points_snapshot, is_proxy) VALUES (?, ?, ?, ?, ?)',
        (list_id, datasheet_id, model_count, points, 1 if is_proxy else 0))
    return cur.lastrowid


def remove_entry(conn, entry_id):
    conn.execute('DELETE FROM list_entries WHERE id = ?', (entry_id,))


# ── The gap ──────────────────────────────────────────────

def list_gap(conn, list_id):
    """What stands between this list and the table.

    Per entry: how many Clay needs, how many he owns, how many of those are
    battle ready — and from that, how many to buy and how many to paint.
    """
    entries = [dict(r) for r in conn.execute("""
        SELECT e.*, d.name AS datasheet_name, d.effort, d.game_system,
               d.basing, f.name AS faction_name
          FROM list_entries e
          JOIN datasheets d    ON d.id = e.datasheet_id
          LEFT JOIN factions f ON f.id = d.faction_id
         WHERE e.list_id = ?
         ORDER BY d.name
    """, (list_id,))]

    owned = {r['datasheet_id']: r for r in col.inventory(conn)}

    to_buy = to_paint = points_total = 0
    for entry in entries:
        have = owned.get(entry['datasheet_id'])
        entry['owned_count'] = have['owned_count'] if have else 0
        entry['done_count'] = have['done_count'] if have else 0
        entry['built_count'] = have['built_count'] if have else 0

        need = entry['model_count']
        entry['buy'] = max(0, need - entry['owned_count'])
        # Only the ones he has can be painted; the rest have to be bought
        # first, and counting them twice would double the work on screen.
        entry['paint'] = max(0, min(need, entry['owned_count']) - entry['done_count'])
        entry['ready'] = entry['buy'] == 0 and entry['paint'] == 0
        entry['fieldable'] = entry['buy'] == 0

        to_buy += entry['buy']
        to_paint += entry['paint']
        points_total += entry['points_snapshot'] or 0

    return {
        'entries': entries,
        'to_buy': to_buy,
        'to_paint': to_paint,
        'points_total': points_total,
        'ready': to_buy == 0 and to_paint == 0,
        'fieldable': to_buy == 0,
    }


# ── The wishlist ─────────────────────────────────────────

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
        want = entry['buy'] - raised.get(entry['datasheet_id'], 0)
        if want <= 0:
            continue
        unit_id = col.create_unit(conn, entry['datasheet_id'], want,
                                  stage_id=wishlist['id'])
        conn.execute(
            'UPDATE models SET wishlist_source_list_id = ? WHERE unit_id = ?',
            (list_id, unit_id))
        added += want
    return added


def wishlist(conn):
    """Everything wanted but not owned, and what raised it."""
    return [dict(r) for r in conn.execute("""
        SELECT d.id AS datasheet_id, d.name, d.game_system,
               f.name AS faction_name,
               COUNT(m.id)                                  AS wanted,
               COUNT(m.wishlist_source_list_id)             AS from_lists,
               GROUP_CONCAT(DISTINCT l.name)                AS list_names
          FROM models m
          JOIN stages s        ON s.id = m.stage_id AND s.is_owned = 0
          JOIN units u         ON u.id = m.unit_id
          JOIN datasheets d    ON d.id = u.datasheet_id
          LEFT JOIN factions f ON f.id = d.faction_id
          LEFT JOIN army_lists l ON l.id = m.wishlist_source_list_id
         GROUP BY d.id
         ORDER BY d.name
    """)]
