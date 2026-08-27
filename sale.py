"""What you could part with, and what you had better not.

Spec §8: "sealed + owned kits, with age, price paid, duplicates, and *whether
any list calls for the contents*." That last clause is the whole feature. A
list of expensive things you own is a spreadsheet; a list of things **no game
you have planned needs** is a decision you can act on.

`models.for_sale_on` (migration 011) already holds the shortlist, and the
collection already filters to it. Nothing ever *fed* it — the shortlist was a
thing Clay had to fill in by remembering what he had. This is the screen that
proposes.

Two sections, because they are two different objects
----------------------------------------------------
**Sealed boxes** are the case where the box is the unit of action. `box_state`
exists precisely because "a sealed box and an opened one both hold models 'On
sprue', but only one carries a resale premium". A sealed box has one name, one
price paid and one age, and selling it means selling it shut.

**Surplus models** are the other case, and there the box is irrelevant — Clay
dropped the kits screen for exactly that reason: "a box is not a thing Clay
does anything with once the models are out of it." Here the question is per
datasheet: you own sixty Boyz and no list has ever asked for more than twenty.

Needed is the MAXIMUM any one list asks, never the sum
------------------------------------------------------
The same rule the wishlist deduplicates on, and for the same reason
`list_allocate` gives every list the whole collection: models are not allocated
*between* lists. "The same Killa Kans appear in three lists and get swapped on
a whim." Three lists each wanting twenty Boyz need twenty, not sixty — you play
one game at a time.

Summing would be the dangerous direction here. It would inflate what looks
needed, hide real surplus, and make the screen recommend nothing — which is a
quiet failure nobody notices, unlike a screen that recommends too much.

An unresolved list row makes the whole answer optimistic, and says so
---------------------------------------------------------------------
A `list_entries` row with no `datasheet_id` could be asking for anything. If
one exists, every surplus on this screen is a *possible* surplus, because the
unresolved row might be the very thing you are about to sell.

So the count comes back with the answer and the screen leads with it. This is
the same three-state honesty as list validation, and it matters more here:
being wrong costs models Clay cannot get back at the price he paid.

Nothing here is stored. Sell something, resolve a list row, add a game on
Saturday, and the answer is different on the next load.
"""

import collection as col

#: What the surplus list can be ordered by, and what each is *for*. Kept beside
#: the query rather than in the template so the route, the chips and the tests
#: cannot drift apart — the same arrangement `backlog.SORTS` uses.
SORTS = (
    ('space', 'Most plastic freed'),
    ('untouched', 'Nothing invested'),
    ('oldest', 'Longest owned'),
    ('name', 'By name'),
)
DEFAULT_SORT = 'space'


def candidates(conn, sort=DEFAULT_SORT):
    """``{'boxes', 'surplus', 'held_back', 'unresolved'}``.

    `held_back` is the sealed boxes a list still wants. They are named rather
    than dropped: "I have nothing sealed worth selling" and "I have four sealed
    boxes and every one is spoken for" are different facts, and only one of
    them means stop looking.
    """
    needed = _needed(conn)
    boxes, held_back = _sealed_boxes(conn, needed)
    return {
        'boxes': boxes,
        'held_back': held_back,
        'surplus': _surplus(conn, needed, sort),
        'unresolved': _unresolved(conn),
    }


def _needed(conn):
    """{datasheet_id: the most any single list asks for}.

    `SUM` inside, `MAX` outside: a list naming Boyz on three separate rows
    wants all three rows' worth, and that total is what competes with the other
    lists' totals.
    """
    return {r['datasheet_id']: r['needed'] for r in conn.execute("""
        SELECT datasheet_id, MAX(per_list) AS needed FROM (
            SELECT list_id, datasheet_id, SUM(model_count) AS per_list
              FROM list_entries
             WHERE datasheet_id IS NOT NULL
             GROUP BY list_id, datasheet_id)
         GROUP BY datasheet_id
    """)}


def _unresolved(conn):
    """List rows that never matched a datasheet.

    Any one of them could be asking for the thing on this screen, so their
    existence makes every number here optimistic.
    """
    return conn.execute(
        'SELECT COUNT(*) AS n FROM list_entries '
        ' WHERE datasheet_id IS NULL').fetchone()['n']


def _sealed_boxes(conn, needed):
    """Sealed, owned boxes — the ones with a resale premium still on them.

    Split into what no list wants and what a list does. A box is held back if
    **any** datasheet in it is named by any list: a Combat Patrol is sold whole,
    so one wanted unit inside it is enough to make selling it the wrong move.
    """
    rows = [dict(r) for r in conn.execute("""
        SELECT k.id, k.name, k.acquired_on, k.source,
               f.name AS faction_name,
               t.name AS template_name, t.year, t.rrp_cents
          FROM kits k
          LEFT JOIN factions f      ON f.id = k.faction_id
          LEFT JOIN kit_templates t ON t.id = k.kit_template_id
         WHERE k.box_state = 'sealed' AND k.status = 'owned'
         ORDER BY k.acquired_on IS NULL, k.acquired_on, k.name
    """)]
    if not rows:
        return [], []

    contents = {}
    marks = ','.join('?' * len(rows))
    for row in conn.execute(
            'SELECT kd.kit_id, kd.datasheet_id, d.name '
            '  FROM kit_datasheets kd '
            '  JOIN datasheets d ON d.id = kd.datasheet_id '
            f' WHERE kd.kit_id IN ({marks}) ORDER BY d.name',
            [r['id'] for r in rows]):
        contents.setdefault(row['kit_id'], []).append(
            {'datasheet_id': row['datasheet_id'], 'name': row['name']})

    free, held = [], []
    for row in rows:
        row['contents'] = contents.get(row['id'], [])
        row['wanted_by_a_list'] = [c['name'] for c in row['contents']
                                   if needed.get(c['datasheet_id'])]
        (held if row['wanted_by_a_list'] else free).append(row)
    return free, held


def _surplus(conn, needed, sort):
    """Datasheets you own more of than any one list has ever asked for.

    Counted per model rather than per unit, and through the same ownership
    rules every other surface uses: `_LIVE_MODEL` keeps out disposals and
    `_ACTIVE_UNIT` keeps out units whose whole box was sold. A model already on
    the shortlist still counts as owned — it is still on the shelf — but is
    reported separately so the screen does not propose it twice.
    """
    first = col.stage_ladder(conn)
    unstarted_position = next(s['position'] for s in first if s['is_owned'])

    rows = []
    for row in conn.execute(f"""
        SELECT COALESCE(m.datasheet_id, u.datasheet_id) AS datasheet_id,
               d.name, d.effort, f.name AS faction_name,
               COUNT(*)                                        AS owned,
               SUM(CASE WHEN m.for_sale_on IS NOT NULL
                        THEN 1 ELSE 0 END)                     AS listed,
               SUM(CASE WHEN s.position = ? THEN 1 ELSE 0 END)  AS unstarted,
               SUM(CASE WHEN k.box_state = 'sealed' AND k.status = 'owned'
                        THEN 1 ELSE 0 END)                     AS sealed,
               MIN(k.acquired_on)                              AS oldest,
               COUNT(DISTINCT u.id)                            AS units,
               GROUP_CONCAT(DISTINCT u.id)                     AS unit_ids
          FROM models m
          JOIN stages s        ON s.id = m.stage_id AND s.is_owned = 1
          JOIN units u         ON u.id = m.unit_id
          JOIN datasheets d    ON d.id = COALESCE(m.datasheet_id, u.datasheet_id)
          LEFT JOIN factions f ON f.id = d.faction_id
          LEFT JOIN kits k     ON k.id = u.kit_id
         WHERE {col._LIVE_MODEL} AND {col._ACTIVE_UNIT}
         GROUP BY COALESCE(m.datasheet_id, u.datasheet_id)
    """, (unstarted_position,)):
        row = dict(row)
        row['needed'] = needed.get(row['datasheet_id'], 0)
        row['surplus'] = row['owned'] - row['needed']
        if row['surplus'] <= 0:
            continue
        # What is left to propose, after the two kinds of model that are
        # already accounted for.
        #
        # Models on the shortlist are spoken for, and a screen that keeps
        # suggesting them is one Clay stops reading.
        #
        # Models still inside a sealed box cannot be sold loose at all —
        # getting them out is what destroys the premium the section above is
        # about — so proposing them here would be offering the same plastic
        # twice, once as a box and once as models that cannot leave it.
        row['to_propose'] = max(
            0, row['surplus'] - row['listed'] - row['sealed'])
        if row['to_propose'] <= 0:
            # Nothing left to suggest. The sealed sections name what is
            # sellable about it, and a row proposing zero is noise on a screen
            # whose whole job is proposing.
            continue
        # The number the "most plastic freed" order is about. A surplus Knight
        # is worth more shelf than a surplus Termagant, and this app weights by
        # effort everywhere else for exactly that reason.
        row['weight'] = round(row['surplus'] * (row['effort'] or 1), 1)
        # Where the row's "sell some" link goes. One unit is the common case
        # and lands straight on its pile controls; several means the collection
        # filtered to the name, because picking one of them for Clay would be
        # guessing which mob he meant.
        ids = (row.pop('unit_ids') or '').split(',')
        row['unit_id'] = int(ids[0]) if row['units'] == 1 and ids[0] else None
        rows.append(row)

    return sorted(rows, key=_ORDER.get(sort, _ORDER[DEFAULT_SORT]))


def totals(result):
    """The line above the two sections."""
    return {
        'boxes': len(result['boxes']),
        'datasheets': len(result['surplus']),
        'models': sum(r['to_propose'] for r in result['surplus']),
    }


#: A missing acquisition date sorts last under "Longest owned" rather than
#: first, the same call `backlog._ORDER` makes: most kits have no date, and
#: letting unknown masquerade as oldest would bury the box actually bought
#: three years ago under everything else.
_ORDER = {
    'space':     lambda r: (-r['weight'], r['name']),
    'untouched': lambda r: (-r['unstarted'], -r['weight'], r['name']),
    'oldest':    lambda r: (r['oldest'] is None, r['oldest'] or '', r['name']),
    'name':      lambda r: (r['name'],),
}
