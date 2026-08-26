"""Everything you own and haven't finished — a big push or a quick win.

Spec §5.5, carried over from the original: "every unfinished model, grouped by
unit, sortable by army and acquisition date, effort shown."

`collection.paintable_units` was the nearest thing: unfinished units, most
recently touched first, capped at 40. That is the right picker for a wet brush
— what you worked on last night is what you are about to pick up — and the
wrong screen for deciding what to start. This one is for the deciding.

Why it is not just a count of unfinished models
-----------------------------------------------
Ten Boyz on sprue and ten Boyz that need only basing are both "ten models
left". They are not the same evening. So the number that sorts this screen is
**how much work is actually left**, which means asking how far up the ladder
each model already is:

    effort_left = effort × (steps still to walk) / (steps from the start)

Ten Boyz on sprue is the unit's whole effort — 10.0. The same ten sitting at
Painted, needing basing and a final check, is a third of it: 3.3. A Knight is
effort 8 and a Termagant 1, so one unstarted Knight (8.0) outranks that
nearly-finished mob of ten — which is the entire reason this app
weights by effort rather than counting models (`CLAUDE.md`: a Knight and a
Termagant are both "1 model", which makes model-count percentages meaningless).

**Raw counts show alongside, never instead**, per the same rule. `models_left`
is the honest number of miniatures in front of you; `effort_left` is what it
will cost.

The ladder is per model, not universal
--------------------------------------
A Rhino has no base, so it walks five stages and not seven — `stages_for`
already knows. Using the full ladder for everything would make every vehicle
look permanently two steps from done.

What counts as backlog
----------------------
Owned and unfinished. A wishlist model is not backlog: it is not on the shelf,
and putting it here would mean the screen that exists to say "what can I work
on tonight" answering with things Clay cannot touch. Disposed models are out
for the same reason, through the same `disposed_on IS NULL` join every other
ownership surface uses.
"""

import collection as col

#: What the screen can be ordered by, and what each order is *for*. Kept here
#: rather than in the template so the route, the chips and the tests cannot
#: drift apart.
SORTS = (
    ('push', 'Big push'),
    ('quick', 'Quick win'),
    ('oldest', 'Longest waiting'),
    ('touched', 'Recently touched'),
    ('army', 'By army'),
)
DEFAULT_SORT = 'push'


def backlog(conn, army_id=None, sort=DEFAULT_SORT):
    """Unfinished units, with how much work each has left in it.

    Computed live. Nothing here is stored, for the same reason the gap report
    is not: paint three Meganobz and the order changes, and that feedback is
    the point.
    """
    ladder = col.stage_ladder(conn)
    rows = _units(conn, army_id)
    steps = _steps_by_basing(conn, ladder)

    out = []
    for row in rows:
        # `basing` is None until someone says; `stages_for` treats that as
        # keeping the basing stages, and so does this. Nothing is reclassified
        # behind Clay's back.
        left = _work_left(row.pop('_stages'), steps[row['basing'] or 'based'])
        if not left['models_left']:
            continue                       # nothing owned and unfinished
        row.update(left)
        row['effort_left'] = round(row['effort'] * left['_fraction'], 1)
        row.pop('_fraction')
        row['display_name'] = row['nickname'] or row['datasheet_name']
        out.append(row)

    return sorted(out, key=_ORDER.get(sort, _ORDER[DEFAULT_SORT]))


def totals(rows):
    """The one-line summary above the list.

    `units` and `models_left` are counts; `effort_left` is the weighted figure.
    Showing all three is the rule rather than a flourish — a percentage of
    models means nothing when a Knight and a Termagant are both one.
    """
    return {'units': len(rows),
            'models_left': sum(r['models_left'] for r in rows),
            'effort_left': round(sum(r['effort_left'] for r in rows), 1)}


def _units(conn, army_id):
    """One row per unit, carrying its models' stage positions.

    `_ACTIVE_UNIT` keeps out units whose whole kit was sold, and
    `m.disposed_on IS NULL` sits in the **ON** clause rather than the WHERE:
    these are LEFT JOINs, and a WHERE there would drop every unit whose models
    are all gone instead of leaving it empty.
    """
    clauses = [col._ACTIVE_UNIT]
    args = []
    if army_id is not None:
        clauses.append('u.army_id = ?')
        args.append(army_id)

    rows = {}
    for row in conn.execute(f"""
        SELECT u.id, u.nickname, u.army_id,
               d.name AS datasheet_name, d.effort, d.basing,
               a.name AS army_name, f.name AS faction_name,
               k.acquired_on,
               MAX(m.stage_changed_at) AS last_activity
          FROM units u
          JOIN datasheets d    ON d.id = u.datasheet_id
          LEFT JOIN armies a   ON a.id = u.army_id
          LEFT JOIN factions f ON f.id = d.faction_id
          LEFT JOIN kits k     ON k.id = u.kit_id
          LEFT JOIN models m   ON m.unit_id = u.id AND m.disposed_on IS NULL
         WHERE {' AND '.join(clauses)}
         GROUP BY u.id
    """, args):
        rows[row['id']] = dict(row, _stages=[])

    if not rows:
        return []
    marks = ','.join('?' * len(rows))
    for row in conn.execute(f"""
        SELECT m.unit_id, s.position, s.is_owned, s.is_terminal, COUNT(*) AS n
          FROM models m
          JOIN stages s ON s.id = m.stage_id
         WHERE m.unit_id IN ({marks}) AND m.disposed_on IS NULL
         GROUP BY m.unit_id, s.id
    """, list(rows)):
        rows[row['unit_id']]['_stages'].append(dict(row))
    return list(rows.values())


def _steps_by_basing(conn, ladder):
    """{basing: [positions a model of that kind walks]}.

    Both keys are computed from the ladder rather than written down, so adding
    a stage to the pipeline cannot leave this quietly measuring the old one.
    """
    out = {}
    for basing in ('based', 'unbased'):
        walk = [s['position'] for s in col.stages_for(conn, basing, ladder)
                if s['is_owned']]
        out[basing] = walk
    return out


def _work_left(stage_rows, walk):
    """How many models are unfinished, and what fraction of the effort remains.

    A model contributes `steps still ahead of it / steps from the start`. One
    on sprue contributes all of itself; one already based, needing only the
    final check, contributes a sixth. A model that is battle ready contributes
    nothing and is not counted as left at all.
    """
    total_steps = len(walk) - 1
    models_left = 0
    fraction = 0.0
    for row in stage_rows:
        if not row['is_owned'] or row['is_terminal']:
            continue
        ahead = len([p for p in walk if p > row['position']])
        if not ahead:
            continue
        models_left += row['n']
        if total_steps:
            fraction += row['n'] * ahead / total_steps
    return {'models_left': models_left, '_fraction': fraction}


def _by_army(row):
    # Unassigned last rather than first: it is the bucket, not an army.
    return (row['army_name'] is None, row['army_name'] or '',
            row['display_name'])


#: A missing acquisition date sorts last under "Longest waiting" rather than
#: first. Most kits have none — the field is filled at the till and Clay's
#: shelf predates the app — and letting unknown masquerade as oldest would put
#: the whole collection above the box he actually bought two years ago.
_ORDER = {
    'push':    lambda r: (-r['effort_left'], r['display_name']),
    'quick':   lambda r: (r['effort_left'], r['display_name']),
    'oldest':  lambda r: (r['acquired_on'] is None, r['acquired_on'] or '',
                          r['display_name']),
    'touched': lambda r: (r['last_activity'] is None,
                          _desc(r['last_activity']), r['display_name']),
    'army':    _by_army,
}


def _desc(value):
    """Sort a text timestamp newest-first inside an otherwise ascending key."""
    return [-ord(c) for c in (value or '')]
