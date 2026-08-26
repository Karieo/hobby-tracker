"""The wishlist says what is missing. This says which boxes.

Spec §7, carried over from the original: "invert `kit_templates`, show the
overage".

`lists.wishlist` answers in datasheets and model counts — *20 Boyz, 5 Nobz, a
Trukk*. That is the right answer to "what am I short", and the wrong answer to
the question Clay is actually holding his phone to ask, which is asked standing
in a shop: **which box**. Games Workshop does not sell seven Boyz.

So this inverts the templates. `kit_templates.create_template` has said so from
the first commit — *"a template is what the shopping list later inverts to say
'buy this'"* — and this is the half that was missing.

No money here, on purpose
-------------------------
Clay, 2026-08-26: *"Spend and kits are obsolete… I just need to be able to
track models here."* This screen used to total the basket, compare a bundle
against the single-unit boxes, and carry three price states to keep that total
honest. All of it is gone: the app no longer asks what a box costs, so there is
nothing to add up.

What survives is the part that was never about money — **which boxes, and how
much spare they arrive with**. `rrp_cents` and `cost_cents` stay in the schema,
unread, the same bargain migration 010's disposal columns made: an inert column
costs nothing, and a destructive migration for tidiness costs a restore if it
goes wrong.

Why it buys one box at a time
-----------------------------
The cover is greedy and deliberately dull: take the box that covers the most
models still missing, subtract what it holds, repeat. Twenty Boyz against a box
of ten picks that box twice rather than reasoning about multiples, and repeats
merge into one line with a quantity at the end.

The overage is the point of showing it
--------------------------------------
Buying a box of ten to get seven leaves three spare, and three spare Boyz are a
real thing that arrives in a real box. `spare` is carried per line and totalled,
because a plan that covers the wishlist in four boxes with forty spare models is
worse than one that takes five with six — and now that there is no price on the
screen, it is the only cost anything reports.

Nothing here is stored, like the gap report and the backlog. Buy a box, define
it, and the plan is different on the next load — which is the whole feedback
loop those two exist for.
"""

import lists as army_lists


def plan(conn):
    """Which boxes to buy for everything on the wishlist.

    ``{'wanted', 'best', 'uncovered'}`` — what is missing, the recommended
    basket, and anything no box in the catalogue contains at all.
    """
    wanted = _wanted(conn)
    best = _cover(wanted, _boxes(conn))
    return {'wanted': wanted, 'best': best, 'uncovered': best['uncovered']}


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
        SELECT t.id, t.name, t.year, f.name AS faction_name
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

    Returns ``{'lines', 'uncovered', 'spare', 'boxes'}``. ``lines`` are
    ``{box, qty, covers, spare}`` — ``covers`` maps a datasheet id to how many
    of it this line actually contributes, so the screen can say *"one Combat
    Patrol: 10 Boyz, 1 Trukk, 3 spare"* rather than naming the box and leaving
    Clay to work out what it was for.
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
      With no price on the screen this is the only cost left to weigh, which is
      why it sits directly behind coverage.
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
        key = (-covered, spare, box['name'], box['id'])
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
            line = {'box': box, 'qty': 0, 'covers': {}, 'spare': 0}
            by_box[box['id']] = line
            lines.append(line)
        line['qty'] += 1
        for datasheet_id, n in covers.items():
            line['covers'][datasheet_id] = line['covers'].get(datasheet_id, 0) + n
        line['spare'] += sum(box['contents'].values()) - sum(covers.values())

    for line in lines:
        # Named contents, resolved here so the template holds no lookups.
        line['names'] = [{'name': names.get(d, 'unknown'), 'n': n}
                         for d, n in sorted(line['covers'].items(),
                                            key=lambda kv: names.get(kv[0], ''))]

    return {
        'lines': lines,
        'uncovered': [{'datasheet_id': d, 'name': names[d], 'short': n}
                      for d, n in sorted(need.items(),
                                         key=lambda kv: names.get(kv[0], ''))
                      if n > 0],
        'spare': sum(line['spare'] for line in lines),
        'boxes': sum(line['qty'] for line in lines),
    }
