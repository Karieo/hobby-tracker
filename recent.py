"""What you got done, in the last thirty days.

Spec §5.1: "models finished in the last 30 days (from `stage_events`)". The
table has been append-only since the first commit for exactly this — migration
001 calls it "the only way 'models finished per month' is ever possible", since
"history cannot be reconstructed after the fact". `journey.py` was the first
thing to read it back. This is the second, and it asks a narrower question: not
what happened, but how much of it.

Two numbers, because either one alone misleads
----------------------------------------------
**Models finished** is what the spec asks for and the number Clay would say out
loud: miniatures that reached Battle ready. It is also the number that reads
**zero** for a month spent priming sixty Boyz — and a home screen telling him he
did nothing in a month he spent every evening at the desk is the precise failure
this app is built against. `CLAUDE.md`: the predecessor "didn't fail on
features — it failed because keeping it current cost more than it gave back."

So **work done** rides alongside, effort-weighted, in the same currency
`/backlog` reports what is left. The backlog says "effort still to spend"; this
says what was spent:

    effort_done = effort × steps walked / steps from the start

Ten Boyz carried from sprue to based is most of 10.0; the same ten given a final
check is 1.7. The two screens are deliberately readable against each other — 12
spent this month against 87 still to go is a sentence, where "12 models" against
"87 models" is not, because a Knight and a Termagant are both "1 model".

Raw counts show alongside, never instead, per the invariant.

Arrivals are not work
---------------------
A model appearing at Painted is Clay telling the app what was already on the
shelf — `/add` takes a stage word precisely so onboarding does not lie about
where things are. Counting it would mean a first week of typing in a painted
collection reporting hundreds of models "finished", which is both wrong and
wrong in the direction that makes the number worthless exactly when it is new.

The exclusion is structural rather than a filter: `_moves` inner-joins the
from-stage, and `add_models` writes arrivals with `from_stage_id` NULL.

Buying is not work either. The walk starts at On sprue, so Wishlist → On sprue
crosses no step — the same line `backlog._work_left` draws when it refuses to
count a wishlist model as backlog.

Corrections cancel; genuine strip-backs do not
----------------------------------------------
`_apply_stage` records a retreat as faithfully as an advance, and `retreat_unit`
exists for the mis-tap with wet hands. An undone mis-tap must not leave "10
finished" standing on the home screen, so **a retreat out of a stage cancels an
advance into that stage on the same day** — journey's rule, and for journey's
reason: same day is what makes it a correction rather than a revision.

Journey nets those per *unit*, because it renders one row per unit and has no
finer identity to work with. This nets per **model**, which is the rule stated
exactly rather than approximated: it has `model_id` on every row and never
aggregates for display. Where the two differ — one model advancing while
another in the same unit retreats — the model-level answer is the true one.

Stripping a squad back in March to redo it properly keeps its advance, because
it was work and it happened. Nothing here ever subtracts: a month cannot go
negative, and a screen that punished Clay for redoing a unit he was unhappy with
would be arguing with the hobby.

History, not ownership
----------------------
This is the one counting surface that does **not** filter disposed models, and
that is deliberate rather than an oversight of the invariant. Painting twenty
Boyz in March and selling them in April does not un-paint them; the evenings
happened. `journey.py` keeps sold kits in the story for the same reason.

`tests/test_collection.py::test_every_ownership_surface_drops_a_disposed_model`
walks the surfaces that count *what Clay has*. This one counts *what Clay did*,
so it is not on that list — and a future reader tempted to add it should change
the docstring first and see whether the sentence survives.

A correction is the other thing entirely: `remove_models` deletes rows outright,
`stage_events` is ON DELETE CASCADE, and plastic that was never there leaves no
history behind. That is already right and needs nothing here.

Nothing is stored. The window is re-read on every home load, like the gap report
and the shopping plan.
"""

from collections import Counter
from datetime import date, timedelta

import collection as col

#: Spec §5.1 says thirty days. Long enough that a fortnight away from the desk
#: does not read as a dead collection, short enough to still mean "lately".
WINDOW_DAYS = 30


def summary(conn, days=WINDOW_DAYS, today=None):
    """``{'days', 'since', 'finished', 'finished_effort', 'effort_done',
    'models_touched', 'by_stage'}``.

    `by_stage` counts **distinct models** reaching each stage, not events, so a
    model stripped and repainted inside the window is one model painted rather
    than two. These are milestones; the same reasoning makes `finished` a set.

    It stops short of the terminal stage, which `finished` already is — by
    construction the same number, since both count distinct models that reached
    Battle ready. One fact in one place: a screen showing "10 finished" above
    "…· 10 battle ready" is saying it twice, and two copies of a number are two
    things to keep in step.
    """
    ladder = col.stage_ladder(conn)
    walks = _walks(conn, ladder)
    since = _since(days, today)

    reached = {}          # stage position -> {model_id}
    effort_done = 0.0
    touched = set()
    finished_effort = {}  # model_id -> effort, deduplicated by the key

    for move in _survivors(conn, since):
        walk = walks['unbased' if move['basing'] == 'unbased' else 'based']
        effort_done += (move['effort'] or 1) * _fraction(
            move['from_position'], move['to_position'], walk)
        touched.add(move['model_id'])
        reached.setdefault(move['to_position'], set()).add(move['model_id'])
        if move['to_terminal']:
            finished_effort[move['model_id']] = move['effort'] or 1

    by_stage = [{'name': s['name'], 'position': s['position'],
                 'n': len(reached[s['position']])}
                for s in ladder
                if reached.get(s['position']) and not s['is_terminal']]

    return {
        'days': days,
        'since': since,
        'finished': len(finished_effort),
        'finished_effort': round(sum(finished_effort.values()), 1),
        'effort_done': round(effort_done, 1),
        'models_touched': len(touched),
        'by_stage': by_stage,
    }


def _since(days, today=None):
    """The first day inside the window, as a date string.

    Inclusive: with `days=30`, something advanced 30 days ago still counts. The
    off-by-one is worth naming because the alternative silently drops a day of
    work every time the screen is read.
    """
    return str((today or date.today()) - timedelta(days=days - 1))


def _walks(conn, ladder):
    """{basing: [positions a model of that kind actually walks]}.

    Derived from the ladder rather than written down, exactly as
    `backlog._steps_by_basing` does it — adding a stage to the pipeline must not
    leave this measuring the old one.
    """
    return {basing: [s['position']
                     for s in col.stages_for(conn, basing, ladder)
                     if s['is_owned']]
            for basing in ('based', 'unbased')}


def _fraction(from_position, to_position, walk):
    """How much of a model's total effort one move represents.

    The mirror of `backlog._work_left`, and clamped at the bottom for the same
    reason it skips unowned models: the walk begins at On sprue, so buying
    something crosses no step. A move that skips a stage — an unbased model
    going Assembled straight to Primed — crosses the steps its own ladder has
    between them, which is why the walk is per basing.
    """
    total = len(walk) - 1
    if total < 1:
        return 0.0
    low = max(from_position, walk[0])
    return len([p for p in walk if low < p <= to_position]) / total


def _survivors(conn, since):
    """Forward moves in the window, with same-day corrections removed.

    Chronological, so where a model advanced into one stage twice in a day the
    *first* is the one a retreat cancels — the mis-tap and its undo, leaving the
    later deliberate move standing.
    """
    undone = _retreats(conn, since)
    out = []
    for move in _moves(conn, since):
        key = (move['model_id'], move['happened_on'], move['to_stage_id'])
        if undone.get(key):
            undone[key] -= 1
            continue
        out.append(move)
    return out


def _moves(conn, since):
    """Every forward stage change since the window opened, oldest first.

    `JOIN stages fs` rather than a LEFT JOIN is what drops arrivals:
    `add_models` writes them with `from_stage_id` NULL, and an arrival is Clay
    describing the shelf rather than working at it.

    Effort comes through `COALESCE(m.datasheet_id, u.datasheet_id)`, the same
    way `sale.py` reads it — a magnetised model counts as what it was built as,
    not as what its unit is.
    """
    return [dict(r) for r in conn.execute("""
        SELECT e.model_id,
               date(e.changed_at) AS happened_on,
               e.to_stage_id      AS to_stage_id,
               fs.position        AS from_position,
               ts.position        AS to_position,
               ts.is_terminal     AS to_terminal,
               d.effort           AS effort,
               d.basing           AS basing
          FROM stage_events e
          JOIN models m     ON m.id = e.model_id
          JOIN units u      ON u.id = m.unit_id
          JOIN datasheets d ON d.id = COALESCE(m.datasheet_id, u.datasheet_id)
          JOIN stages ts    ON ts.id = e.to_stage_id
          JOIN stages fs    ON fs.id = e.from_stage_id
         WHERE date(e.changed_at) >= ? AND ts.position > fs.position
         ORDER BY e.changed_at, e.id
    """, (since,))]


def _retreats(conn, since):
    """How many times each model walked back out of a stage, per day.

    Keyed on the stage *left*, because that is the advance it cancels —
    retreating out of Primed undoes an advance into Primed. A Counter rather
    than a set: a model can be advanced, walked back and advanced again inside
    one sitting, and only the first of those is the correction.
    """
    return Counter({
        (r['model_id'], r['happened_on'], r['left_stage_id']): r['n']
        for r in conn.execute("""
        SELECT e.model_id,
               date(e.changed_at) AS happened_on,
               e.from_stage_id    AS left_stage_id,
               COUNT(*)           AS n
          FROM stage_events e
          JOIN stages ts ON ts.id = e.to_stage_id
          JOIN stages fs ON fs.id = e.from_stage_id
         WHERE date(e.changed_at) >= ? AND ts.position < fs.position
         GROUP BY e.model_id, happened_on, e.from_stage_id
    """, (since,))})
