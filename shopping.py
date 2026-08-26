"""The wishlist says what is missing. This says what to buy.

Spec §7, carried over from the original: "invert `kit_templates`, show the
overage, and always show the à la carte total beside the bundle total."

`lists.wishlist` answers in datasheets and model counts — *20 Boyz, 5 Nobz, a
Trukk*. That is the right answer to "what am I short", and the wrong answer to
the question Clay is actually holding his phone to ask, which is asked standing
in a shop: **which box**. Games Workshop does not sell seven Boyz.

So this inverts the templates. `kit_templates.create_template` has said so from
the first commit — *"a template is what the shopping list later inverts to say
'buy this'"* — and this is the half that was missing.

Why it buys one box at a time
-----------------------------
The cover is greedy and deliberately dull: take the box that covers the most
models still missing, subtract what it holds, repeat. Twenty Boyz against a box
of ten picks that box twice rather than reasoning about multiples, and repeats
merge into one line with a quantity at the end.

It is not an optimiser and does not pretend to be. A true minimum-cost cover
needs a price on every box, and most boxes here have none — Clay types
`rrp_cents` in himself and there is no scraping GW for prices (`CLAUDE.md`), so
optimising against it would mean optimising against whichever boxes he happened
to have priced. Ties break toward the smaller overage first and the cheaper
*known* price second, which is as far as the data honestly reaches.

The overage is the point of showing it
--------------------------------------
Buying a box of ten to get seven leaves three spare, and three spare Boyz are a
real thing that arrives in a real box. `spare` is carried per line and totalled,
because a plan that covers the wishlist in four boxes with forty spare models is
worse than one that takes five with six, and nothing else on the screen would
say so.

Bundle against à la carte, and why it is two runs of one function
-----------------------------------------------------------------
The comparison Clay wants is whether the Combat Patrol is actually a saving over
the individual boxes. So the same cover runs twice: once over every box, once
over only the single-unit ones. Two totals, same mechanism, and no special case
that could compute the bundle one way and its alternative another.

If a single-unit box does not exist for something, the à la carte side simply
cannot be built, and it says that rather than quietly costing less by leaving
the missing part out.

Three price states, for the same reason list validation has three
-----------------------------------------------------------------
Most templates carry no `rrp_cents`. A total that silently skips them is a
number Clay would read as the price and it would be too low — the one direction
a shopping total must never be wrong in. So `priced` means every box in the plan
had a price, `partial` means some did and the figure is a floor, and `unpriced`
means there is no figure to show at all. `partial` is the honest common case,
and the screen says "at least" where it lands.

Nothing here is stored, like the gap report and the backlog. Buy a box, define
it, and the plan is different on the next load — which is the whole feedback
loop those two exist for.
"""

import lists as army_lists

#: Price states, worst-first. `partial` is not a failure — it is what a
#: half-priced catalogue honestly produces, and the screen shows the figure as a
#: floor rather than withholding it.
PRICED, PARTIAL, UNPRICED = 'priced', 'partial', 'unpriced'


def plan(conn):
    """What to buy for everything on the wishlist.

    ``{'wanted', 'best', 'alacarte', 'uncovered'}`` — the recommended basket,
    the same basket restricted to single-unit boxes, and anything no box in the
    catalogue contains at all.
    """
    wanted = _wanted(conn)
    boxes = _boxes(conn)

    best = _cover(wanted, boxes)
    # The same cover over single-unit boxes only. Two runs of one function
    # rather than a per-box special case: a comparison computed by different
    # code than the thing it compares to is a comparison that drifts.
    alacarte = _cover(wanted, [b for b in boxes if len(b['contents']) == 1])

    return {'wanted': wanted, 'best': best, 'alacarte': alacarte,
            'uncovered': best['uncovered'],
            'saving': _saving(best, alacarte)}


def _wanted(conn):
    """What the wishlist screen is showing, in datasheet order.

    Read through `lists.wishlist` rather than with a query of its own. That
    function is where the deduplication across lists lives — two lists wanting
    the same twenty Boyz is twenty, not forty — and a second query here would be
    a second chance to get that wrong.
    """
    return [{'datasheet_id': w['datasheet_id'], 'name': w['name'],
             'faction_name': w['faction_name'], 'wanted': w['wanted']}
            for w in army_lists.wishlist(conn) if w['wanted'] > 0]


def _boxes(conn):
    """Every template that has contents, with them.

    A template with no contents cannot cover anything, and `create_template`
    refuses to make one — but a row could predate that rule or have had its
    last unit removed, and a box in the plan holding nothing would be a shop
    trip for no models.
    """
    out = {}
    for row in conn.execute("""
        SELECT t.id, t.name, t.year, t.rrp_cents,
               f.name AS faction_name
          FROM kit_templates t
          LEFT JOIN factions f ON f.id = t.faction_id
         ORDER BY t.name, t.year
    """):
        out[row['id']] = dict(row, contents={})

    for row in conn.execute(
            'SELECT kit_template_id, datasheet_id, model_count '
            '  FROM kit_template_units'):
        box = out.get(row['kit_template_id'])
        if box is not None and row['model_count'] > 0:
            # Summed rather than assigned: nothing stops a template listing the
            # same datasheet twice, and the last row winning would undercount
            # the box.
            box['contents'][row['datasheet_id']] = (
                box['contents'].get(row['datasheet_id'], 0) + row['model_count'])

    return [b for b in out.values() if b['contents']]


def _cover(wanted, boxes):
    """Greedily cover ``wanted`` out of ``boxes``.

    Returns ``{'lines', 'uncovered', 'cents', 'state', 'spare', 'boxes'}``.
    ``lines`` are ``{box, qty, covers, spare, cents}`` — ``covers`` maps a
    datasheet id to how many of it this line actually contributes, so the screen
    can say *"one Combat Patrol: 10 Boyz, 1 Trukk, 3 spare"* rather than naming
    the box and leaving Clay to work out what it was for.
    """
    need = {w['datasheet_id']: w['wanted'] for w in wanted}
    names = {w['datasheet_id']: w['name'] for w in wanted}
    picks = []

    # Each pass strictly decreases the outstanding total, which is what makes
    # this terminate without a counter — so the one thing that must never get
    # through is a pick covering nothing. `_best_box` already skips those and
    # returns None instead; the second guard is here because the cost of it
    # being wrong is not a bad plan but a page that never finishes loading.
    while any(v > 0 for v in need.values()):
        choice = _best_box(need, boxes)
        if choice is None:
            break
        box, covers = choice
        if not covers:
            break
        for datasheet_id, n in covers.items():
            need[datasheet_id] -= n
        picks.append((box, covers))

    return _summarise(picks, need, names)


def _best_box(need, boxes):
    """The box covering the most still-missing models, or None.

    Tie-breaks, in order and each for a reason:

    - **fewer spare models.** Two boxes covering the same seven Boyz are not
      equally good if one arrives with three extra and the other with thirteen.
    - **a known price, and then a lower one.** Not an optimisation — an unpriced
      box makes the whole total a floor rather than a number, so where the
      choice is otherwise even, the one the plan can stand behind wins.
    - **name, then id.** So the same wishlist and catalogue always produce the
      same plan. A recommendation that reshuffled between two loads would be one
      Clay could not trust or check.
    """
    best = None
    for box in boxes:
        covers = {d: min(need.get(d, 0), n) for d, n in box['contents'].items()}
        covers = {d: n for d, n in covers.items() if n > 0}
        covered = sum(covers.values())
        if not covered:
            continue
        spare = sum(box['contents'].values()) - covered
        key = (-covered, spare, box['rrp_cents'] is None,
               box['rrp_cents'] or 0, box['name'], box['id'])
        if best is None or key < best[0]:
            best = (key, box, covers)
    return None if best is None else (best[1], best[2])


def _summarise(picks, need, names):
    """Merge repeated boxes into one line each and total the result."""
    lines = []
    by_box = {}
    for box, covers in picks:
        line = by_box.get(box['id'])
        if line is None:
            line = {'box': box, 'qty': 0, 'covers': {}, 'spare': 0,
                    'cents': None}
            by_box[box['id']] = line
            lines.append(line)
        line['qty'] += 1
        for datasheet_id, n in covers.items():
            line['covers'][datasheet_id] = line['covers'].get(datasheet_id, 0) + n
        line['spare'] += sum(box['contents'].values()) - sum(covers.values())

    priced = 0
    unpriced = 0
    cents = 0
    for line in lines:
        if line['box']['rrp_cents'] is None:
            unpriced += 1
        else:
            priced += 1
            line['cents'] = line['box']['rrp_cents'] * line['qty']
            cents += line['cents']
        # Named contents, resolved here so the template holds no lookups.
        line['names'] = [{'name': names.get(d, 'unknown'), 'n': n}
                         for d, n in sorted(line['covers'].items(),
                                            key=lambda kv: names.get(kv[0], ''))]

    if not lines or not priced:
        state = UNPRICED
    elif unpriced:
        state = PARTIAL
    else:
        state = PRICED

    return {
        'lines': lines,
        'uncovered': [{'datasheet_id': d, 'name': names[d], 'short': n}
                      for d, n in sorted(need.items(),
                                         key=lambda kv: names.get(kv[0], ''))
                      if n > 0],
        'cents': cents if priced else None,
        'state': state,
        'spare': sum(line['spare'] for line in lines),
        'boxes': sum(line['qty'] for line in lines),
    }


def _saving(best, alacarte):
    """What the recommended basket saves over the single-unit one, or None.

    Refuses to answer unless **both** sides are fully priced and both cover the
    same ground. A partial total is a floor, and subtracting one floor from
    another produces a number that is not a bound on anything — the kind of
    figure that looks like money and means nothing.

    A negative saving is returned as it stands rather than suppressed. If the
    singles really are cheaper, that is the thing worth knowing, and a
    comparison that only ever flatters the bundle is not a comparison.
    """
    if best['state'] != PRICED or alacarte['state'] != PRICED:
        return None
    if best['uncovered'] or alacarte['uncovered']:
        return None
    if not best['lines'] or not alacarte['lines']:
        return None
    # Checked on the values rather than trusting that PRICED implies a figure.
    # That implication is `_summarise`'s to keep, and a subtraction here is the
    # wrong place to find out it stopped being true.
    if best['cents'] is None or alacarte['cents'] is None:
        return None
    return alacarte['cents'] - best['cents']
