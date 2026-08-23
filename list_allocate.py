"""Which physical models cover which entries, and what is genuinely missing.

Section 7's allocation, and the reason the whole gap checker exists.

THE BUG THIS REPLACES
---------------------
`lists.list_gap` counts ownership per entry. Nothing consumes a model once it
has been assigned, so a list with two ten-model Boyz mobs and one twenty-model
mob matches all three against the same twenty Boyz and reports no shortfall.
Reproduced against shipped code before this was written: it needed forty and
said "fieldable". Clay finds that the night before a game.

So: allocate. Walk the requirements in a defined order, hand each one models
out of a pool, and **take them out of the pool as they go**. A model is one
piece of plastic and can only be in one place.

TWO PASSES
----------
**Pass 1** spends what is already the right thing. Per datasheet, requirements
largest first, models battle-ready first — greedy is correct within a datasheet
because all models of one datasheet are interchangeable, and largest-first
means twenty owned fills a twenty-model mob whole rather than leaving three
part-filled ones that are each individually useless on a table.

**Pass 2** fills what is left from plastic that *could become* the datasheet:
an unbuilt sprue from a box whose `kit_datasheets` includes it, or a magnetised
model built as something else. Shortfalls are served **most-constrained first**
— the requirement with the fewest eligible candidates goes before one with
plenty — because a Moirax sprue is the only thing that can ever be a Moirax,
while a Warglaive can come from any Armiger box. Serve the easy one first and
the constrained one starves on a pool that was never really contested.

Most-constrained-first is a heuristic, not provably optimal. At this collection
size it will not be wrong in practice, and the alternative is a bipartite
matching solver for no real gain.

A magnetised model is still one physical model. It can serve exactly one
requirement in a list, and once consumed it is gone — a single magnetised
Armiger cannot be both the Warglaive and the Helverin in the same list.

WHAT THIS DOES NOT DO
---------------------
Nothing here is stored. `GET /lists/<id>` re-runs it against the collection as
it is right now, so painting three Meganobz and reloading moves the numbers.
That feedback loop is the feature, and a cached report would quietly stop being
true the moment Clay picked up a brush.

Allocation is also *within one list*. The original spec is explicit that models
are not allocated *between* lists — "the same Killa Kans appear in three lists
and get swapped on a whim" — so two lists each see the whole collection. Spec
§9 records that the cross-list note it asks for instead is still unbuilt.
"""

import collection as col

# Units whose kit has been sold or traded are out of the collection, so their
# models can never cover anything. Same filter the inventory uses.
_ACTIVE = col._ACTIVE_UNIT


def allocate(conn, list_id, include_unassigned=True):
    """The gap report for one list, computed live.

    ``include_unassigned`` is Section 7's toggle, defaulting to on: Clay keeps
    kits unassigned on purpose, and excluding them by default would make the
    report pessimistic and useless.
    """
    entries = _entries(conn, list_id)
    army_id = conn.execute('SELECT army_id FROM army_lists WHERE id = ?',
                           (list_id,)).fetchone()
    army_id = army_id['army_id'] if army_id else None

    consumed = set()
    _pass_one(conn, entries, army_id, include_unassigned, consumed)
    _pass_two(conn, entries, army_id, include_unassigned, consumed)
    return _summarise(entries)


def _entries(conn, list_id):
    rows = [dict(r) for r in conn.execute("""
        SELECT e.id, e.position, e.raw_name, e.datasheet_id, e.model_count,
               e.points_snapshot, e.points, e.resolved_by,
               d.name AS datasheet_name, f.name AS faction_name
          FROM list_entries e
          LEFT JOIN datasheets d ON d.id = e.datasheet_id
          LEFT JOIN factions f   ON f.id = d.faction_id
         WHERE e.list_id = ?
         ORDER BY e.position, e.id
    """, (list_id,))]
    for row in rows:
        row.update(owned=0, battle_ready=0, swappable=0, buildable=0,
                   short=row['model_count'], assigned=[])
    return rows


def _army_clause(army_id, include_unassigned):
    """Which armies' models this list may draw on.

    Section 7: "models where datasheet_id = X, army_id matches (or is
    unassigned)", with the unassigned half behind a toggle that defaults to on
    — Clay keeps kits unassigned on purpose, and excluding them by default
    would make the report pessimistic and useless.

    Turning the toggle off means "only models I have committed to an army", and
    that is a sentence with a meaning whether or not *this* list names one. So
    it filters in both cases rather than silently doing nothing on a list with
    no army, which would make the control lie on the screen it sits on.
    """
    if army_id is None:
        return ('', []) if include_unassigned else (' AND u.army_id IS NOT NULL', [])
    if include_unassigned:
        return (' AND (u.army_id = ? OR u.army_id IS NULL)', [army_id])
    return (' AND u.army_id = ?', [army_id])


def _pass_one(conn, entries, army_id, include_unassigned, consumed):
    """Spend models that already are the datasheet, largest requirement first."""
    where, args = _army_clause(army_id, include_unassigned)
    wanted = [e for e in entries if e['datasheet_id']]
    by_sheet = {}
    for entry in wanted:
        by_sheet.setdefault(entry['datasheet_id'], []).append(entry)

    for datasheet_id, requirements in by_sheet.items():
        available = [dict(r) for r in conn.execute(f"""
            SELECT m.id, m.is_flexible, s.is_terminal, s.position
              FROM models m
              JOIN units u  ON u.id = m.unit_id
              JOIN stages s ON s.id = m.stage_id AND s.is_owned = 1
             WHERE m.datasheet_id = ? AND {_ACTIVE}{where}
             ORDER BY s.position DESC, m.id
        """, (datasheet_id, *args))]
        available = [m for m in available if m['id'] not in consumed]

        # Largest first: twenty owned should fill a twenty-model mob whole
        # rather than leave three part-filled ones, none of them fieldable.
        for entry in sorted(requirements, key=lambda e: -e['model_count']):
            take = min(entry['model_count'], len(available))
            assigned, available = available[:take], available[take:]
            consumed.update(m['id'] for m in assigned)
            entry['owned'] = take
            entry['battle_ready'] = sum(1 for m in assigned if m['is_terminal'])
            entry['short'] = entry['model_count'] - take
            entry['assigned'].extend(assigned)


def _pass_two(conn, entries, army_id, include_unassigned, consumed):
    """Fill what is left from plastic that could become the datasheet."""
    shortfalls = [e for e in entries if e['datasheet_id'] and e['short'] > 0]
    if not shortfalls:
        return

    pools = {e['id']: _candidates(conn, e['datasheet_id'], army_id,
                                  include_unassigned, consumed)
             for e in shortfalls}

    # Most-constrained first. A Moirax sprue is the only thing that can ever be
    # a Moirax; a Warglaive can come from any Armiger box. Serving the Warglaive
    # first spends the one sprue the Moirax needed.
    for entry in sorted(shortfalls, key=lambda e: len(pools[e['id']])):
        pool = [m for m in pools[entry['id']] if m['id'] not in consumed]
        # Magnetised-and-ready before uncommitted: a swap costs no hobby time,
        # while a sprue is an evening. Then the most finished plastic first.
        pool.sort(key=lambda m: (not (m['is_flexible'] and m['is_terminal']),
                                 -m['position'], m['id']))
        take = min(entry['short'], len(pool))
        assigned = pool[:take]
        consumed.update(m['id'] for m in assigned)
        entry['swappable'] += sum(1 for m in assigned
                                  if m['is_flexible'] and m['is_terminal'])
        entry['buildable'] += sum(1 for m in assigned
                                  if not (m['is_flexible'] and m['is_terminal']))
        entry['short'] -= take
        entry['assigned'].extend(assigned)


def _candidates(conn, datasheet_id, army_id, include_unassigned, consumed):
    """Models that could become this datasheet but are not it yet.

    "Candidates are unconsumed models where the kit's `kit_datasheets` includes
    the required datasheet, and either `datasheet_id IS NULL` or
    `is_flexible = 1`."

    A model already committed to this datasheet is not here — pass 1 had it.
    """
    where, args = _army_clause(army_id, include_unassigned)
    rows = [dict(r) for r in conn.execute(f"""
        SELECT m.id, m.is_flexible, m.datasheet_id, s.is_terminal, s.position
          FROM models m
          JOIN units u   ON u.id = m.unit_id
          JOIN stages s  ON s.id = m.stage_id AND s.is_owned = 1
          JOIN kit_datasheets kd ON kd.kit_id = u.kit_id
                                AND kd.datasheet_id = ?
         WHERE (m.datasheet_id IS NULL OR m.is_flexible = 1)
           AND (m.datasheet_id IS NULL OR m.datasheet_id <> ?)
           AND {_ACTIVE}{where}
    """, (datasheet_id, datasheet_id, *args))]
    return [r for r in rows if r['id'] not in consumed]


def _summarise(entries):
    """The row states and the summary line above them.

    Points come from `points_snapshot` — what this app priced the entry at from
    the Munitorum manual — never from `points`, which is what an export
    claimed. §2.7 settled that and Clay confirmed it: a number out of someone
    else's app never outranks the official one.

    "Points owned is the sum of points for entries where short = 0, counting
    swappable and buildable models toward ownership. A partially-owned unit
    contributes nothing — a 7-of-10 Boyz mob is not 70% of a Boyz mob on the
    table." Battle-ready points count swappable models, since a swap costs no
    hobby time; buildable ones do not.
    """
    owned_points = ready_points = total_points = 0
    for entry in entries:
        entry['state'] = _state(entry)
        if not entry['datasheet_id']:
            # "Unresolved entries are excluded from all totals and the summary
            # says so explicitly. Never let an unresolved row quietly deflate
            # the numbers."
            continue
        points = entry['points_snapshot'] or 0
        total_points += points
        if entry['short'] == 0:
            owned_points += points
            if entry['battle_ready'] + entry['swappable'] >= entry['model_count']:
                ready_points += points

    # Two halves, kept apart because they are different evenings: one is a trip
    # to a shop, the other a night at the desk. Buildable plastic counts as
    # work — a sprue is an evening — while a swap is neither.
    to_paint = sum(max(0, e['owned'] - e['battle_ready']) + e['buildable']
                   for e in entries if e['datasheet_id'])
    return {
        'entries': entries,
        'short': sum(e['short'] for e in entries if e['datasheet_id']),
        'to_buy': sum(e['short'] for e in entries if e['datasheet_id']),
        'to_paint': to_paint,
        'ready': all(e['short'] == 0 for e in entries if e['datasheet_id'])
                 and to_paint == 0,
        'units_short': sum(1 for e in entries if e['short'] > 0),
        'swaps': sum(e['swappable'] for e in entries),
        'to_build': sum(e['buildable'] for e in entries),
        'unresolved': sum(1 for e in entries if not e['datasheet_id']),
        'points_total': total_points,
        'points_owned': owned_points,
        'points_ready': ready_points,
        'fieldable': all(e['short'] == 0 for e in entries if e['datasheet_id']),
    }


def _state(entry):
    """One of Section 7's six row states. Order matters: the worst one wins.

    A row is described by the thing Clay would have to do about it, so an entry
    that is part short and part swappable reads as *short* — the swap is not
    the problem with it.
    """
    if not entry['datasheet_id']:
        return 'unresolved'
    if entry['short'] > 0:
        return 'short'
    if entry['buildable'] > 0:
        return 'buildable'
    if entry['swappable'] > 0:
        return 'swappable'
    if entry['battle_ready'] < entry['model_count']:
        return 'owned'
    return 'ready'
