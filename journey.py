"""The whole hobby life, in the order it happened.

Clay: *"I want it to be a journey of my whole hobby life across all models."*

The photo log was only ever the visible part of that. `stage_events` has been
append-only since the first commit for exactly this — CLAUDE.md calls it "the
only way 'models finished per month' is ever possible, because history cannot
be reconstructed after the fact" — and until now nothing read it. Every model
that ever arrived, got built, primed, painted or based is in there with a date.

So a journey is four streams merged on date: pictures taken, models moving
forward, boxes bought, boxes gone.

WHY STAGE EVENTS ARE AGGREGATED
-------------------------------
There is one row per *model*, so advancing twenty Boyz writes twenty of them.
Rendered one per line that is twenty identical entries and a screen nobody
scrolls. They collapse to one per (day, unit, stage): "20 × Boyz — Assembled".

The count is the interesting part anyway. "3 × Meganobz — Painted" and "20 ×
Boyz — Painted" are different evenings.

WHY BACKWARD MOVES ARE LEFT OUT, AND TAKE THEIR ADVANCE WITH THEM
-----------------------------------------------------------------
`_apply_stage` writes an event for a retreat as well as an advance, because it
is the same function and the history should be complete. But a −1 is a
correction — a mis-tap with wet hands, per `retreat_unit`'s own reasoning for
existing — and rendering it as "20 × Boyz — Assembled" a second time would read
as having built them twice.

Dropping the retreat alone is not enough, though, and the first seeded run
showed why: eight Boyz advanced and immediately walked back still left "8 ×
Boyz — Base prepared" on the page forever. Showing the mistake and hiding the
fix is the worst of the three options.

So a retreat *out of* a stage cancels an advance *into* that stage on the same
day, for the same unit. Same day is what makes it a correction rather than a
revision: a mis-tap and its undo happen in one sitting, while stripping a squad
back in March to redo it properly is a real thing that happened and keeps its
entry.

Arrivals are never cancelled — `retreat_unit` refuses to un-own a model, so
nothing can retreat out of the stage a model arrived at.

The rows stay in the table. This filters a view; it does not lose history.
"""

import photos

#: Deliberately generous. This is a page you scroll through on purpose, and a
#: collection would need years to reach it — but an unbounded query on a screen
#: nobody paginates is how a page eventually stops loading at all.
LIMIT = 600


def events(conn, limit=LIMIT):
    """Everything dated, oldest first.

    Oldest first is what makes it a journey rather than a feed. Every other
    screen in this app answers a question about now and leads with the newest
    thing; this one is read forwards, because the point is the distance
    travelled.
    """
    out = [*_photos(conn), *_stages(conn), *_kits(conn)]
    # Sorted here rather than in SQL: four queries against different tables,
    # and a UNION would need every column to line up across shapes that have
    # nothing else in common.
    out.sort(key=lambda e: (e['on'], e['kind'], e.get('unit_name') or ''))
    return out[:limit]


def _photos(conn):
    rows = photos.timeline(conn)
    for row in rows:
        row['kind'] = 'photo'
        row['on'] = row['taken_on']
    return rows


def _stages(conn):
    """Forward moves, one entry per day / unit / stage, corrections netted out.

    `from_stage_id IS NULL` is a model arriving — `add_models` writes it so
    that "a model that never moves has no record of when it arrived".
    """
    undone = _retreats(conn)
    out = []
    for row in conn.execute("""
        -- `on` is a keyword to SQLite (JOIN ... ON), so the column is named
        -- and grouped by something it will accept.
        SELECT date(e.changed_at)               AS happened_on,
               u.id                             AS unit_id,
               COALESCE(u.nickname, d.name)     AS unit_name,
               a.name                           AS army_name,
               s.id                             AS stage_id,
               s.name                           AS stage_name,
               s.position                       AS position,
               s.is_terminal                    AS is_terminal,
               e.from_stage_id IS NULL          AS arrived,
               COUNT(*)                         AS n
          FROM stage_events e
          JOIN models m      ON m.id = e.model_id
          JOIN units u       ON u.id = m.unit_id
          JOIN datasheets d  ON d.id = u.datasheet_id
          JOIN stages s      ON s.id = e.to_stage_id
          LEFT JOIN stages fs ON fs.id = e.from_stage_id
          LEFT JOIN armies a ON a.id = u.army_id
         WHERE e.from_stage_id IS NULL OR s.position > fs.position
         GROUP BY happened_on, u.id, e.to_stage_id, arrived
    """):
        count = row['n']
        if not row['arrived']:
            count -= undone.get(
                (row['happened_on'], row['unit_id'], row['stage_id']), 0)
        if count < 1:
            continue
        out.append({
            'kind': 'stage',
            'on': row['happened_on'],
            'unit_id': row['unit_id'],
            'unit_name': row['unit_name'],
            'army_name': row['army_name'],
            'stage_name': row['stage_name'],
            'position': row['position'],
            'is_terminal': bool(row['is_terminal']),
            'arrived': bool(row['arrived']),
            'count': count,
        })
    return out


def _retreats(conn):
    """How many models walked *back out of* each stage, per day and unit.

    Keyed by the stage left rather than the one landed on, because that is the
    advance it cancels: retreating out of Primed undoes an advance into Primed.
    """
    return {(row['happened_on'], row['unit_id'], row['left_stage_id']): row['n']
            for row in conn.execute("""
        SELECT date(e.changed_at)  AS happened_on,
               u.id                AS unit_id,
               e.from_stage_id     AS left_stage_id,
               COUNT(*)            AS n
          FROM stage_events e
          JOIN models m  ON m.id = e.model_id
          JOIN units u   ON u.id = m.unit_id
          JOIN stages s  ON s.id = e.to_stage_id
          JOIN stages fs ON fs.id = e.from_stage_id
         WHERE s.position < fs.position
         GROUP BY happened_on, u.id, e.from_stage_id
    """)}


def _kits(conn):
    """Boxes arriving and boxes leaving.

    A disposal is a status change rather than a deletion, per the invariant, so
    a sold box is still here — and "sold the Land Raider in March" is part of
    the story in a way a deleted row could never be.
    """
    out = []
    for row in conn.execute("""
        SELECT id, name, acquired_on, status, disposed_on
          FROM kits
         WHERE acquired_on IS NOT NULL OR disposed_on IS NOT NULL
    """):
        # No cost carried. Clay, 2026-08-26: "Spend and kits are obsolete."
        # The dates are the story — "bought in March, sold in August" — and the
        # price was never part of what made that worth reading.
        if row['acquired_on']:
            out.append({'kind': 'kit', 'on': row['acquired_on'],
                        'kit_id': row['id'], 'name': row['name']})
        if row['disposed_on']:
            out.append({'kind': 'gone', 'on': row['disposed_on'],
                        'kit_id': row['id'], 'name': row['name'],
                        'status': row['status']})
    return out


def pictures(conn, limit=LIMIT):
    """Just the photographs, for the scrubber.

    Separate from `events` because they answer different questions with the
    same rows: the scrubber is the visual journey and wants only frames it can
    show, while the stream below wants everything that happened. Filtering the
    merged list would also drag along photos whose file has gone missing, and
    a scrubber that lands on "the picture is missing" is a broken control
    rather than an honest one.
    """
    return [row for row in photos.timeline(conn, limit=limit)
            if not row['missing']]


def span(entries):
    """First and last dates, or None. Saves the template two guards."""
    if not entries:
        return None
    return entries[0]['on'], entries[-1]['on']
