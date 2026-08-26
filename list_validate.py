"""Is this list legal, and can the app actually tell?

Spec §9, carried over from the original: "points against the limit, legal unit
sizes, faction consistency, and the three-state badge that refuses to show a
false green."

The third state is the whole point. Before this, `/lists/<id>` printed the
points total and the points limit next to each other and never compared them —
so a 2,050-point list for a 2,000-point game looked exactly like a legal one,
and Clay would find out at the table. The obvious fix is a green tick. The
obvious fix is also how you get burned: **a badge that says "legal" while a
third of the checks could not run is worse than no badge at all**, because it
gets trusted.

So there are three answers, not two:

    problem   something is definitely wrong, and here is which entry
    review    nothing is wrong that can be seen, but a check could not run
    ok        every check ran, and every check passed

`review` is the honest common case. 415 of the 1,445 imported 40,000
datasheets carry no `min_models`/`max_models` at all, an unresolved entry has
no datasheet to check anything against, and an allied detachment is a real
thing that looks exactly like a faction mismatch.

What this deliberately does NOT do
----------------------------------
**It does not claim to be a rules engine.** Unit sizes come in increments —
ten or twenty Boyz, never fifteen — and `min_models`/`max_models` cannot
express that, so a mob of fifteen passes the size check. Detachment rules,
enhancement limits and the one-of-each-character rule are not modelled at all.
This catches the errors the data can actually see, and says so; it never
implies a list it has passed is tournament-legal.
"""

#: Kill Team operatives are one model each by construction, so a size check
#: against them would flag every entry of more than one. The list builder is
#: 40,000-only anyway — this keeps that true rather than assuming it.
SIZED_SYSTEM = 'wh40k'


def validate(conn, list_id):
    """The three-state verdict for one list, computed live like the gap report.

    Returns ``{state, points, problems, review}``. ``problems`` are definite —
    each names the entry and what is wrong with it. ``review`` is everything
    that stops the answer being a clean yes, whether because a check could not
    run or because what it found is legal often enough not to call it a fault.
    """
    row = conn.execute(
        'SELECT points_limit, faction_id FROM army_lists WHERE id = ?',
        (list_id,)).fetchone()
    if row is None:
        raise ValueError(f'no list {list_id}')

    entries = [dict(r) for r in conn.execute("""
        SELECT e.id, e.raw_name, e.datasheet_id, e.model_count,
               e.points_snapshot,
               d.name AS datasheet_name, d.min_models, d.max_models,
               d.game_system, d.faction_id AS datasheet_faction_id,
               f.name AS faction_name
          FROM list_entries e
          LEFT JOIN datasheets d ON d.id = e.datasheet_id
          LEFT JOIN factions f   ON f.id = d.faction_id
         WHERE e.list_id = ?
         ORDER BY e.position, e.id
    """, (list_id,))]

    problems, review = [], []
    points = _check_points(row['points_limit'], entries, problems, review)
    _check_sizes(entries, problems, review)
    _check_faction(conn, row['faction_id'], entries, review)

    state = 'problem' if problems else ('review' if review else 'ok')
    return {'state': state, 'points': points,
            'problems': problems, 'review': review}


def _note(kind, message, entry=None):
    return {'kind': kind, 'message': message,
            'entry_id': entry['id'] if entry else None}


def _check_points(limit, entries, problems, review):
    """Over the limit is a fact; under it may still be a guess.

    Unpriced entries can only ever *add* points, so a list already over the
    limit is over it whatever they turn out to be — that stays a definite
    problem. A list under the limit with something unpriced is the case that
    has to go to `review`: the missing number could take it over.
    """
    total = sum(e['points_snapshot'] or 0
                for e in entries if e['datasheet_id'])
    unpriced = [e for e in entries
                if e['datasheet_id'] and not e['points_snapshot']]
    unresolved = [e for e in entries if not e['datasheet_id']]
    points = {'total': total, 'limit': limit,
              'over': max(0, total - limit) if limit else 0}

    if not limit:
        # ...but only ask for one where there is a control that can set it.
        # Battle sizes are 40,000's, and the picker offers its two; a Kill Team
        # list has no limit to choose, so demanding one every load is a nag
        # pointing at a door that does not exist. Same reason `_check_sizes`
        # scopes itself to `SIZED_SYSTEM`, and the same test of a check worth
        # printing: it has to be actionable.
        #
        # An empty list still gets asked. Nothing says which game it is yet,
        # and a list about to be filled with 40,000 entries should be told.
        if _any_sized(entries):
            review.append(_note(
                'points', 'No points limit set, so nothing to check the total '
                          'against. Set one on the list to have this checked.'))
        return points

    if total > limit:
        problems.append(_note(
            'points', f'{total} points against a {limit} limit — '
                      f'{total - limit} over.'))
    elif unpriced or unresolved:
        parts = []
        if unpriced:
            parts.append(f'{len(unpriced)} entr'
                         f'{"y has" if len(unpriced) == 1 else "ies have"} '
                         'no points')
        if unresolved:
            parts.append(f'{len(unresolved)} unresolved')
        review.append(_note(
            'points', f'{total} of {limit} points, but {" and ".join(parts)} — '
                      'the real total could be higher.'))
    return points


def _any_sized(entries):
    """Does this list contain anything the battle-size picker can price?

    An entry with no datasheet counts: it might resolve to a 40,000 unit, and
    guessing otherwise would silently stop checking a list that needs it.
    """
    return any(e['game_system'] == SIZED_SYSTEM or not e['datasheet_id']
               for e in entries) or not entries


def _check_sizes(entries, problems, review):
    """A unit outside its datasheet's own minimum and maximum.

    Only catches out-of-range. Sizes come in increments — ten or twenty Boyz,
    never fifteen — and the two columns cannot say so, which is why the module
    docstring refuses the words "rules engine".
    """
    unsized = 0
    for entry in entries:
        if not entry['datasheet_id']:
            continue
        if entry['game_system'] != SIZED_SYSTEM:
            continue
        low, high = entry['min_models'], entry['max_models']
        if low is None or high is None:
            unsized += 1
            continue
        count = entry['model_count'] or 0
        if count < low or count > high:
            problems.append(_note(
                'size',
                f'{entry["datasheet_name"]}: {count} models, but the datasheet '
                f'allows {low}' + (f'–{high}' if high != low else '') + '.',
                entry))
    if unsized:
        review.append(_note(
            'size', f'{unsized} entr{"y" if unsized == 1 else "ies"} could not '
                    'be size-checked — the datasheet carries no unit size.'))


def _check_faction(conn, list_faction_id, entries, review):
    """Entries from another faction.

    Never a `problem`, always a `review`. Allied detachments and Imperial
    Agents are legal and look exactly like a mistake from here, so calling this
    illegal would be the app being confidently wrong about the one thing it
    cannot see. Naming the entries lets Clay tell the two apart in a glance,
    which is all it is for.
    """
    if not list_faction_id:
        review.append(_note(
            'faction', 'No faction set on this list, so entries cannot be '
                       'checked against one.'))
        return
    strangers = [e for e in entries
                 if e['datasheet_id'] and e['datasheet_faction_id']
                 and e['datasheet_faction_id'] != list_faction_id]
    if strangers:
        names = ', '.join(sorted({f'{e["datasheet_name"]} ({e["faction_name"]})'
                                  for e in strangers}))
        review.append(_note(
            'faction', f'From another faction: {names}. Allied detachments are '
                       'legal, so this is worth an eye rather than a fault.'))
