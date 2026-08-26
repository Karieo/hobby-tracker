"""Armies, kits, units, models — queries and mutations.

Two ideas run through this whole module.

**Per-model rows, whole-unit interactions.** Storage is one row per miniature,
because that is the only honest way to record six of ten Boyz being primed. But
the default interaction is never per-model: ``advance_unit`` with no count moves
the entire unit forward in one call, and that is what the UI's primary control
binds to. Addressing an individual model is possible and never required.

**Effort, not model count.** A Knight Questoris and a Termagant are both "1
model", so counting models makes every progress bar lie. Every percentage here
is effort-weighted (``datasheets.effort`` per model); raw counts travel
alongside rather than instead.
"""

import json
from datetime import date

import database as db

# A box recorded before anyone said what is in it. Prefixed rather than left
# blank so it reads as a real thing on a shelf, and so adopt_template can tell
# a placeholder from a name Clay chose himself.
UNIDENTIFIED_PREFIX = 'Unidentified box'

# A kit that has been sold, traded or gifted keeps its rows — deleting it would
# corrupt the spend history — but its models stop counting towards ownership and
# effort. `listed` is still owned: it is on the market, not gone.
ACTIVE_KIT_STATUSES = ('owned', 'listed')

# Units whose kit is disposed of, or which have no kit at all (manual adds).
#: A model still on the shelf. Sits beside `_ACTIVE_UNIT` and does the same
#: job one level down: that one excludes models inside a kit Clay sold, this
#: one excludes models he sold out of a kit he kept.
#:
#: Ownership is read in about thirty places. Adding a disposal column and then
#: hand-editing thirty queries is how one gets missed and the collection
#: over-counts quietly for months, so both rules live as fragments that
#: queries interpolate rather than as conditions each query remembers.
#:
#: The disposed row is never removed and keeps its stage: "sold five painted
#: Boyz" stays answerable, which is the only reason to record it.
_LIVE_MODEL = ' m.disposed_on IS NULL '

_ACTIVE_UNIT = """
    (u.kit_id IS NULL OR EXISTS (
        SELECT 1 FROM kits k WHERE k.id = u.kit_id
          AND k.status IN ('owned', 'listed')))
"""


# ── Stage helpers ────────────────────────────────────────

def stage_ladder(conn):
    """Stages in pipeline order, as plain dicts."""
    return [dict(r) for r in db.get_stages(conn)]


# Keywords that suggest a model has no base. Vehicle without Walker: measured
# against the imported data, Rhino / Land Raider / Trukk / Predator /
# Battlewagon are Vehicle alone and have none, while Redemptor Dreadnought /
# Killa Kans / Deff Dread are Vehicle + Walker and do.
#
# A hint, never a decision — nine models checked by hand is a correlation, not
# a rule GW publishes, and one wrong classification is silent and flatters the
# numbers. It pre-fills a control Clay confirms, exactly as box contents do.
def basing_hint(keywords):
    """'unbased' if the keywords suggest no base, else None. Suggestion only."""
    if not keywords:
        return None
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords)
        except (TypeError, ValueError):
            return None
    keywords = set(keywords or ())
    if 'Vehicle' in keywords and 'Walker' not in keywords:
        return 'unbased'
    return None


def set_basing(conn, datasheet_id, basing):
    """Record whether this datasheet's models sit on a base.

    Clay's to set, never inferred. `None` clears it back to "nobody has said",
    which behaves as based.
    """
    if basing not in (None, 'based', 'unbased'):
        raise ValueError(f'unknown basing {basing!r}')
    if not conn.execute('SELECT 1 FROM datasheets WHERE id = ?',
                        (datasheet_id,)).fetchone():
        raise ValueError(f'no datasheet {datasheet_id}')
    conn.execute('UPDATE datasheets SET basing = ?, updated_at = ? WHERE id = ?',
                 (basing, db.now(), datasheet_id))


def stages_for(conn, basing=None, ladder=None):
    """The ladder a model with this basing actually walks.

    `basing` is the datasheet's: 'unbased' drops the basing stages, 'based' and
    None keep them. None means nobody has said yet, and it behaves exactly as
    before — nothing is reclassified behind Clay's back, because the rules data
    cannot tell us and guessing would overstate progress. See migration 004.
    """
    ladder = ladder if ladder is not None else stage_ladder(conn)
    if basing != 'unbased':
        return ladder
    return [s for s in ladder if not s['is_basing']]


def next_stage(conn, stage_id):
    """The stage after this one, or None at the end of the pipeline."""
    row = conn.execute(
        'SELECT s2.* FROM stages s1 JOIN stages s2 ON s2.position > s1.position '
        'WHERE s1.id = ? ORDER BY s2.position LIMIT 1', (stage_id,)).fetchone()
    return dict(row) if row else None


# ── Armies ───────────────────────────────────────────────

def list_armies(conn):
    """Every army with its effort rollup, plus the Unassigned bucket.

    Unassigned is not a real army — it is units Clay has not committed to one,
    which is a deliberate state (a sealed box he may sell). It surfaces as its
    own row rather than being hidden, so nothing goes missing by being
    unfiled.
    """
    rows = [dict(r) for r in conn.execute(f"""
        SELECT a.id, a.name, a.notes, a.sort_order, f.name AS faction_name,
               COUNT(DISTINCT u.id)                                AS unit_count,
               COUNT(m.id)                                         AS model_count,
               COALESCE(SUM(CASE WHEN st.is_owned THEN 1 ELSE 0 END), 0)
                                                                   AS owned_count,
               COALESCE(SUM(d.effort), 0)                          AS effort_total,
               COALESCE(SUM(CASE WHEN st.is_terminal THEN d.effort ELSE 0 END), 0)
                                                                   AS effort_done,
               MAX(m.stage_changed_at)                             AS last_activity
          FROM armies a
          LEFT JOIN units u      ON u.army_id = a.id AND {_ACTIVE_UNIT}
          LEFT JOIN datasheets d ON d.id = u.datasheet_id
          LEFT JOIN models m     ON m.unit_id = u.id AND m.disposed_on IS NULL
          LEFT JOIN stages st    ON st.id = m.stage_id
          LEFT JOIN factions f   ON f.id = a.primary_faction_id
         GROUP BY a.id
         ORDER BY a.sort_order, a.name
    """)]

    unassigned = dict(conn.execute(f"""
        SELECT COUNT(DISTINCT u.id)                                AS unit_count,
               COUNT(m.id)                                         AS model_count,
               COALESCE(SUM(CASE WHEN st.is_owned THEN 1 ELSE 0 END), 0)
                                                                   AS owned_count,
               COALESCE(SUM(d.effort), 0)                          AS effort_total,
               COALESCE(SUM(CASE WHEN st.is_terminal THEN d.effort ELSE 0 END), 0)
                                                                   AS effort_done,
               MAX(m.stage_changed_at)                             AS last_activity
          FROM units u
          JOIN datasheets d      ON d.id = u.datasheet_id
          LEFT JOIN models m     ON m.unit_id = u.id AND m.disposed_on IS NULL
          LEFT JOIN stages st    ON st.id = m.stage_id
         WHERE u.army_id IS NULL AND {_ACTIVE_UNIT}
    """).fetchone())

    # Per-army stage spread, so a card carries the same readable bar a unit does.
    spread = {}
    for r in conn.execute(f"""
        SELECT u.army_id, m.stage_id, COUNT(*) AS n
          FROM units u JOIN models m ON m.unit_id = u.id AND m.disposed_on IS NULL
         WHERE {_ACTIVE_UNIT}
         GROUP BY u.army_id, m.stage_id
    """):
        spread.setdefault(r['army_id'], {})[r['stage_id']] = r['n']

    ladder = stage_ladder(conn)
    for row in rows:
        row['completion'] = _pct(row['effort_done'], row['effort_total'])
        row['segments'] = _segments(ladder, spread.get(row['id'], {}),
                                    row['model_count'])
    if unassigned['unit_count']:
        unassigned.update(id=None, name='Unassigned', notes=None,
                          faction_name=None,
                          completion=_pct(unassigned['effort_done'],
                                          unassigned['effort_total']),
                          segments=_segments(ladder, spread.get(None, {}),
                                             unassigned['model_count']))
        rows.append(unassigned)
    return rows


def get_army(conn, army_id):
    row = conn.execute(
        'SELECT a.*, f.name AS faction_name FROM armies a '
        'LEFT JOIN factions f ON f.id = a.primary_faction_id WHERE a.id = ?',
        (army_id,)).fetchone()
    return dict(row) if row else None


def create_army(conn, name, primary_faction_id=None, notes=None):
    nxt = conn.execute(
        'SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM armies').fetchone()['n']
    cur = conn.execute(
        'INSERT INTO armies (name, primary_faction_id, notes, sort_order, created_at) '
        'VALUES (?, ?, ?, ?, ?)',
        (name, primary_faction_id, notes, nxt, db.now()))
    return cur.lastrowid


def update_army(conn, army_id, name=None, primary_faction_id=None, notes=None):
    army = get_army(conn, army_id)
    if not army:
        return None
    conn.execute(
        'UPDATE armies SET name = ?, primary_faction_id = ?, notes = ? WHERE id = ?',
        (name if name is not None else army['name'],
         primary_faction_id if primary_faction_id is not None
         else army['primary_faction_id'],
         notes if notes is not None else army['notes'], army_id))
    return army_id


def army_stats(conn, army_id):
    """Header figures for the army detail screen."""
    where = 'u.army_id = ?' if army_id else 'u.army_id IS NULL'
    args = (army_id,) if army_id else ()
    row = dict(conn.execute(f"""
        SELECT COUNT(DISTINCT u.id)                              AS unit_count,
               COUNT(m.id)                                       AS model_count,
               COALESCE(SUM(CASE WHEN st.is_owned THEN 1 ELSE 0 END), 0)
                                                                 AS owned_count,
               COALESCE(SUM(CASE WHEN st.is_terminal THEN 1 ELSE 0 END), 0)
                                                                 AS done_count,
               COALESCE(SUM(d.effort), 0)                        AS effort_total,
               COALESCE(SUM(CASE WHEN st.is_terminal THEN d.effort ELSE 0 END), 0)
                                                                 AS effort_done,
               MAX(m.stage_changed_at)                           AS last_activity
          FROM units u
          JOIN datasheets d   ON d.id = u.datasheet_id
          LEFT JOIN models m  ON m.unit_id = u.id AND m.disposed_on IS NULL
          LEFT JOIN stages st ON st.id = m.stage_id
         WHERE {where} AND {_ACTIVE_UNIT}
    """, args).fetchone())
    row['completion'] = _pct(row['effort_done'], row['effort_total'])
    return row


# ── Units ────────────────────────────────────────────────

def list_units(conn, army_id=None, unassigned=False, include_disposed=False,
               kit_id=None):
    """Units with the per-stage counts the stage bar is drawn from."""
    clauses, args = [], []
    if kit_id is not None:
        # A kit's own units, disposed or not: the kit page has to show what is
        # in the box even when the box has been sold, or its contents vanish
        # from the one screen that exists to explain them.
        clauses.append('u.kit_id = ?')
        args.append(kit_id)
        include_disposed = True
    if unassigned:
        clauses.append('u.army_id IS NULL')
    elif army_id is not None:
        clauses.append('u.army_id = ?')
        args.append(army_id)
    if not include_disposed:
        clauses.append(_ACTIVE_UNIT)
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''

    units = [dict(r) for r in conn.execute(f"""
        SELECT u.id, u.army_id, u.kit_id, u.nickname, u.notes,
               d.id AS datasheet_id, d.name AS datasheet_name, d.effort,
               d.min_models, d.max_models,
               f.name AS faction_name, a.name AS army_name,
               k.name AS kit_name, k.status AS kit_status, k.box_state,
               COUNT(m.id)                                       AS model_count,
               COALESCE(SUM(CASE WHEN st.is_terminal THEN 1 ELSE 0 END), 0)
                                                                 AS done_count,
               COALESCE(SUM(CASE WHEN st.is_owned THEN 1 ELSE 0 END), 0)
                                                                 AS owned_count,
               COUNT(m.id) * d.effort                            AS effort_total,
               COALESCE(SUM(CASE WHEN st.is_terminal THEN d.effort ELSE 0 END), 0)
                                                                 AS effort_done,
               MAX(m.stage_changed_at)                           AS last_activity
          FROM units u
          JOIN datasheets d    ON d.id = u.datasheet_id
          LEFT JOIN factions f ON f.id = d.faction_id
          LEFT JOIN armies a   ON a.id = u.army_id
          LEFT JOIN kits k     ON k.id = u.kit_id
          LEFT JOIN models m   ON m.unit_id = u.id AND m.disposed_on IS NULL
          LEFT JOIN stages st  ON st.id = m.stage_id
          {where}
         GROUP BY u.id
         ORDER BY d.name, u.id
    """, args)]

    if not units:
        return units
    ids = [u['id'] for u in units]
    marks = ','.join('?' * len(ids))
    breakdown = {}
    for row in conn.execute(f"""
        SELECT m.unit_id, m.stage_id, COUNT(*) AS n
          FROM models m WHERE m.unit_id IN ({marks})
         GROUP BY m.unit_id, m.stage_id
    """, ids):
        breakdown.setdefault(row['unit_id'], {})[row['stage_id']] = row['n']

    ladder = stage_ladder(conn)
    for unit in units:
        counts = breakdown.get(unit['id'], {})
        unit['stage_counts'] = counts
        unit['segments'] = _segments(ladder, counts, unit['model_count'])
        unit['completion'] = _pct(unit['effort_done'], unit['effort_total'])
        unit['display_name'] = unit['nickname'] or unit['datasheet_name']
    return units


def get_unit(conn, unit_id):
    row = conn.execute("""
        SELECT u.*, d.name AS datasheet_name, d.effort, d.min_models, d.max_models,
               f.name AS faction_name, a.name AS army_name,
               k.name AS kit_name, k.status AS kit_status, k.box_state
          FROM units u
          JOIN datasheets d    ON d.id = u.datasheet_id
          LEFT JOIN factions f ON f.id = d.faction_id
          LEFT JOIN armies a   ON a.id = u.army_id
          LEFT JOIN kits k     ON k.id = u.kit_id
         WHERE u.id = ?
    """, (unit_id,)).fetchone()
    if not row:
        return None
    unit = dict(row)
    unit['display_name'] = unit['nickname'] or unit['datasheet_name']
    return unit


def unit_breakdown(conn, unit_id):
    """One row per stage, including the empty ones.

    Stages with zero models stay in the list so the pipeline reads as a
    pipeline — a gap you can see is information, a row that vanishes is not.
    """
    counts = {r['stage_id']: r['n'] for r in conn.execute(
        'SELECT stage_id, COUNT(*) AS n FROM models WHERE unit_id = ? '
        'GROUP BY stage_id', (unit_id,))}
    total = sum(counts.values())

    # Which stages this unit's models actually walk. A Rhino has no base, so
    # the basing stages are shown struck through rather than hidden or left
    # tappable: hiding them makes the ladder a different length per unit and
    # unreadable at a glance, and leaving them live invites a tap that would
    # inflate progress on a stage that never happened.
    basing = conn.execute(
        'SELECT d.basing FROM units u JOIN datasheets d ON d.id = u.datasheet_id '
        'WHERE u.id = ?', (unit_id,)).fetchone()
    walked = {s['id'] for s in stages_for(conn, basing['basing'] if basing else None)}

    out = []
    for stage in stage_ladder(conn):
        n = counts.get(stage['id'], 0)
        applies = stage['id'] in walked
        out.append({**stage, 'count': n, 'percent': _pct(n, total),
                    'applies': applies,
                    'can_advance': n > 0 and not stage['is_terminal']})
    return out


def unit_models(conn, unit_id):
    return [dict(r) for r in conn.execute("""
        SELECT m.*, s.name AS stage_name, s.position, s.is_terminal, s.is_owned
          FROM models m JOIN stages s ON s.id = m.stage_id
         WHERE m.unit_id = ? AND m.disposed_on IS NULL
         ORDER BY s.position, m.id
    """, (unit_id,))]


def create_unit(conn, datasheet_id, model_count, army_id=None, kit_id=None,
                stage_id=None, nickname=None, notes=None):
    """Create a unit and generate its models in one go.

    Clay never creates models one at a time — a unit is a squad, and typing ten
    rows to record ten Boyz is exactly the friction that kills a tracker.
    """
    if model_count < 1:
        raise ValueError('a unit needs at least one model')
    if stage_id is None:
        stage_id = db.first_owned_stage(conn)['id']
    cur = conn.execute(
        'INSERT INTO units (army_id, kit_id, datasheet_id, nickname, notes, '
        'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (army_id, kit_id, datasheet_id, nickname, notes, db.now(), db.now()))
    unit_id = cur.lastrowid
    add_models(conn, unit_id, model_count, stage_id)
    return unit_id


def add_or_extend_unit(conn, datasheet_id, model_count, army_id=None,
                       kit_id=None, stage_id=None, nickname=None):
    """Add models for a datasheet, extending a unit that already fits.

    Clay's complaint, holding the phone: "if I add more of a model it needs to
    add them, not make 2 lines." Three Killa Kans recorded in two goes showed
    up on the collection as "1 model" and "2 models" with nothing on either
    line to tell them apart — two rows describing one squad, and no single
    control that moved the squad.

    So a second helping joins the first, and the four things that make two
    units genuinely different are the four things that refuse the merge:

    - **A different kit.** Disposals are per kit: sell the box and its models
      go with it. Pouring two boxes into one unit makes that impossible to
      unpick afterwards, so two copies of the same Combat Patrol stay two
      units — and they are labelled with the box, so they read as two.
    - **A different army.** Allied detachments and a second army of the same
      faction are the whole reason the column exists.
    - **A different nickname.** Naming a squad is Clay saying this one is its
      own thing.
    - **Wanted versus owned.** A wishlist line offers "Bought it →" and an
      owned line offers "Advance all →". Merging the two would swallow the
      wishlist entry, and with it the moment the loop closes.

    Everything else merges, *stages included*. One squad of three Kans with one
    painted and two still on sprue is the truth of the shelf, and per-model
    stages exist to say exactly that.

    Returns ``{unit_id, model_ids, extended}``. ``model_ids`` is the models
    this call added and no others, so a caller stamping provenance on them —
    which list wanted them, which box — cannot stamp the ones already there.
    """
    if model_count < 1:
        raise ValueError('a unit needs at least one model')
    if stage_id is None:
        stage_id = db.first_owned_stage(conn)['id']
    nickname = nickname or None

    stage = conn.execute('SELECT is_owned FROM stages WHERE id = ?',
                         (stage_id,)).fetchone()
    adding_owned = bool(stage and stage['is_owned'])

    # `IS` rather than `=` throughout: a unit with no army and no kit is the
    # common case, and `NULL = NULL` is NULL, so `=` would match nothing at all
    # and quietly never merge anything.
    candidates = conn.execute(f"""
        SELECT u.id,
               COALESCE(SUM(CASE WHEN st.is_owned THEN 1 ELSE 0 END), 0) AS owned
          FROM units u
          LEFT JOIN models m  ON m.unit_id = u.id AND m.disposed_on IS NULL
          LEFT JOIN stages st ON st.id = m.stage_id
         WHERE u.datasheet_id = ?
           AND u.army_id IS ? AND u.kit_id IS ? AND u.nickname IS ?
           AND {_ACTIVE_UNIT}
         GROUP BY u.id
         ORDER BY u.id
    """, (datasheet_id, army_id, kit_id, nickname)).fetchall()

    target = next((row['id'] for row in candidates
                   if bool(row['owned']) == adding_owned), None)
    if target is not None:
        return {'unit_id': target,
                'model_ids': add_models(conn, target, model_count, stage_id),
                'extended': True}

    unit_id = create_unit(conn, datasheet_id, model_count, army_id=army_id,
                          kit_id=kit_id, stage_id=stage_id, nickname=nickname)
    return {'unit_id': unit_id,
            'model_ids': [r['id'] for r in conn.execute(
                'SELECT id FROM models WHERE unit_id = ?', (unit_id,))],
            'extended': False}


def add_models(conn, unit_id, count, stage_id):
    """Append models to an existing unit, all at one stage.

    `datasheet_id` is stamped from the unit rather than left null. Migration
    008 backfilled every model that existed when it ran and this is the writer
    that keeps that true afterwards — without it the column would be complete
    for old rows and empty for every model added since, which is worse than not
    having it at all, because allocation resolves ownership *through* it and
    would quietly report a full collection as owning nothing.

    A model is only ever anything other than its unit's datasheet once Clay
    says so — magnetising it, or building a multi-option sprue as one of its
    other options — and that is an update, never a default.
    """
    datasheet_id = conn.execute('SELECT datasheet_id FROM units WHERE id = ?',
                                (unit_id,)).fetchone()['datasheet_id']
    stamp = db.now()
    ids = []
    for _ in range(count):
        cur = conn.execute(
            'INSERT INTO models (unit_id, datasheet_id, stage_id, '
            'stage_changed_at, created_at) VALUES (?, ?, ?, ?, ?)',
            (unit_id, datasheet_id, stage_id, stamp, stamp))
        ids.append(cur.lastrowid)
        # The starting position is history too: without it, a model that never
        # moves has no record of when it arrived.
        conn.execute(
            'INSERT INTO stage_events (model_id, from_stage_id, to_stage_id, '
            'changed_at) VALUES (?, NULL, ?, ?)', (cur.lastrowid, stage_id, stamp))
    _touch_unit(conn, unit_id)
    return ids


def buildable_options(conn, unit_id):
    """What the box this unit came out of can be built as.

    Empty for the overwhelming majority: most kits build one thing, `add_models`
    already stamped it, and there is nothing to ask. It is the Armiger sprue and
    the big Knight kit this exists for — where the unit's datasheet is a default
    rather than a decision Clay made.
    """
    return [dict(r) for r in conn.execute("""
        SELECT d.id, d.name, f.name AS faction_name
          FROM units u
          JOIN kit_datasheets kd ON kd.kit_id = u.kit_id
          JOIN datasheets d      ON d.id = kd.datasheet_id
          LEFT JOIN factions f   ON f.id = d.faction_id
         WHERE u.id = ?
         ORDER BY d.name
    """, (unit_id,))]


def unit_built_as(conn, unit_id):
    """What this unit's models are right now, and whether they are magnetised.

    Returns the common answer rather than a per-model list. Divergence inside
    one unit is possible in the schema and has no UI, because the interaction
    this app is built around is the squad — Clay magnetised the unit, not model
    number three.
    """
    row = conn.execute("""
        SELECT m.datasheet_id, d.name, MAX(m.is_flexible) AS is_flexible,
               COUNT(*) AS n
          FROM models m
          LEFT JOIN datasheets d ON d.id = m.datasheet_id
         WHERE m.unit_id = ?
         GROUP BY m.datasheet_id
         ORDER BY n DESC
    """, (unit_id,)).fetchone()
    return dict(row) if row else None


def set_built_as(conn, unit_id, datasheet_id, flexible=False):
    """Record what this unit's models actually are, and whether that reverses.

    Whole-unit, because that is the interaction the app is built around: Clay
    magnetised the squad, not model number three. Per-model divergence is
    possible in the schema and deliberately has no UI.

    Refused unless the kit can genuinely build it. Letting a unit be set to a
    datasheet its box never contained would put models in the gap report that
    do not exist, which is the failure the whole gap checker is for.
    """
    unit = conn.execute('SELECT * FROM units WHERE id = ?',
                        (unit_id,)).fetchone()
    if not unit:
        raise ValueError(f'no unit {unit_id}')
    if datasheet_id is None:
        # Back to uncommitted: plastic that is no longer claiming to be
        # anything, which is what an unbuilt sprue honestly is.
        conn.execute('UPDATE models SET datasheet_id = NULL, is_flexible = ? '
                     'WHERE unit_id = ?', (1 if flexible else 0, unit_id))
        _touch_unit(conn, unit_id)
        return

    allowed = {row['id'] for row in buildable_options(conn, unit_id)}
    allowed.add(unit['datasheet_id'])
    if datasheet_id not in allowed:
        raise ValueError('that box cannot build that datasheet')

    conn.execute('UPDATE models SET datasheet_id = ?, is_flexible = ? '
                 'WHERE unit_id = ?',
                 (datasheet_id, 1 if flexible else 0, unit_id))
    _touch_unit(conn, unit_id)


def move_unit_to_army(conn, unit_id, army_id):
    """Allied units get reshuffled; None parks a unit back in Unassigned."""
    conn.execute('UPDATE units SET army_id = ?, updated_at = ? WHERE id = ?',
                 (army_id, db.now(), unit_id))


_UNIT_FIELDS = ('nickname', 'notes')


def update_unit(conn, unit_id, **fields):
    """Write only the fields that were actually supplied.

    It used to take nickname and notes as keyword arguments defaulting to None
    and write both every time. That was fine while one form sent both, and a
    quiet data-loss bug the moment a form sent one: dropping the nickname input
    from the unit page would have made every notes save blank the nickname of
    any squad Clay had named. Nothing would have told him — `display_name`
    would simply go back to the datasheet name one day.

    So an absent key means "leave it alone", and an empty string still means
    "clear it", which is what a cleared input has to mean.
    """
    unknown = set(fields) - set(_UNIT_FIELDS)
    assert not unknown, f'update_unit cannot write {sorted(unknown)}'
    if not fields:
        return
    sets = ', '.join(f'{name} = ?' for name in fields)
    conn.execute(f'UPDATE units SET {sets}, updated_at = ? WHERE id = ?',
                 [*(value or None for value in fields.values()),
                  db.now(), unit_id])


def list_for_sale(conn, unit_id, count=1):
    """Earmark some of a unit to part with.

    Clay: *"Not sold, sell a list of things to part with."*

    A flag, not a removal. These models are still on the shelf and still his:
    they keep counting as owned, keep advancing through the stages, keep
    showing in the collection. They just also appear on a list he can work
    from when he next feels like clearing shelf space.

    The most advanced go first, which is the opposite of every other bulk
    operation here. Removing and selling pick the least advanced because those
    are the ones with no work in them; this one is a shortlist for parting
    with, and a finished squad is what is actually worth listing.
    """
    listing = [r['id'] for r in conn.execute("""
        SELECT m.id FROM models m
          JOIN stages s ON s.id = m.stage_id
         WHERE m.unit_id = ? AND s.is_owned = 1 AND m.for_sale_on IS NULL
         ORDER BY s.position DESC, m.id LIMIT ?
    """, (unit_id, max(0, count)))]
    if not listing:
        return 0
    marks = ','.join('?' * len(listing))
    conn.execute(f'UPDATE models SET for_sale_on = ? WHERE id IN ({marks})',
                 (date.today().isoformat(), *listing))
    _touch_unit(conn, unit_id)
    return len(listing)


def unlist_for_sale(conn, unit_id, count=1):
    """Changed your mind. Clears the flag; nothing else ever moved."""
    keeping = [r['id'] for r in conn.execute("""
        SELECT id FROM models
         WHERE unit_id = ? AND for_sale_on IS NOT NULL
         ORDER BY for_sale_on DESC, id DESC LIMIT ?
    """, (unit_id, max(0, count)))]
    if not keeping:
        return 0
    marks = ','.join('?' * len(keeping))
    conn.execute(f'UPDATE models SET for_sale_on = NULL WHERE id IN ({marks})',
                 keeping)
    _touch_unit(conn, unit_id)
    return len(keeping)


def pile_counts(conn, unit_id):
    """How many of this unit are owned, wanted and gone.

    One query for the three numbers the unit page shows beside its plus and
    minus buttons, so a tap can repaint them without reloading the page.
    """
    row = conn.execute("""
        SELECT COALESCE(SUM(CASE WHEN m.disposed_on IS NULL AND s.is_owned
                                 THEN 1 ELSE 0 END), 0) AS owned,
               COALESCE(SUM(CASE WHEN m.disposed_on IS NULL AND NOT s.is_owned
                                 THEN 1 ELSE 0 END), 0) AS wishlist,
               COALESCE(SUM(CASE WHEN m.for_sale_on IS NOT NULL
                                 THEN 1 ELSE 0 END), 0) AS for_sale
          FROM models m JOIN stages s ON s.id = m.stage_id
         WHERE m.unit_id = ? AND m.disposed_on IS NULL
    """, (unit_id,)).fetchone()
    # `for_sale` overlaps `owned` deliberately: a model listed to part with is
    # still on the shelf. It is a shortlist, not a fourth place to be.
    return {'owned': row['owned'], 'wishlist': row['wishlist'],
            'sell': row['for_sale']}


def undispose_models(conn, unit_id, count=1):
    """Put one back. The undo for a mis-tap on Sold.

    Most recently disposed first, so tapping minus straight after plus undoes
    exactly what plus did. The row never left, so this only clears the three
    columns that took it out of the counts.
    """
    coming_back = [r['id'] for r in conn.execute("""
        SELECT id FROM models
         WHERE unit_id = ? AND disposed_on IS NOT NULL
         ORDER BY disposed_on DESC, id DESC LIMIT ?
    """, (unit_id, max(0, count)))]
    if not coming_back:
        return 0
    marks = ','.join('?' * len(coming_back))
    conn.execute(f"""UPDATE models SET disposed_on = NULL, disposed_as = NULL,
                                       disposed_price_cents = NULL
                      WHERE id IN ({marks})""", coming_back)
    _touch_unit(conn, unit_id)
    return len(coming_back)


def unwishlist_models(conn, unit_id, count=1):
    """Stop wanting one. Deletes the wishlist rows — nothing was ever owned,
    so there is no history to keep, which is why this is not a disposal."""
    doomed = [r['id'] for r in conn.execute("""
        SELECT m.id FROM models m JOIN stages s ON s.id = m.stage_id
         WHERE m.unit_id = ? AND s.is_owned = 0 AND m.disposed_on IS NULL
         ORDER BY m.id DESC LIMIT ?
    """, (unit_id, max(0, count)))]
    if not doomed:
        return 0
    conn.executemany('DELETE FROM models WHERE id = ?',
                     [(i,) for i in doomed])
    _touch_unit(conn, unit_id)
    return len(doomed)


def wishlist_models(conn, unit_id, count):
    """Put more of this on the wishlist.

    Clay: *"wishlist more"*. Needs no new storage — Wishlist has been position
    0 of the ladder since the first migration, with `is_owned = 0`, so wanting
    more of something is the same operation as owning more of it aimed one rung
    lower. `/collection?own=wanted` already lists them.
    """
    if count < 1:
        return 0
    wishlist = conn.execute(
        'SELECT id FROM stages WHERE is_owned = 0 ORDER BY position LIMIT 1'
    ).fetchone()
    # A count, not the ids `add_models` hands back: this is the same shape as
    # `dispose_models` returns, and it is what the caller renders.
    return len(add_models(conn, unit_id, count, stage_id=wishlist['id']))


def remove_models(conn, unit_id, count):
    """Take models off a unit. The undo for adding too many.

    This is a **correction, not a disposal**. Models Clay actually owned and
    then sold or traded leave through `dispose_kit`, which keeps every row and
    the spend history. This deletes rows outright, so it is only ever for
    plastic that was never there — a mistyped count, a bulk add that ran twice.

    Which ones go: least advanced first, and within a stage the most recently
    added. Both orderings point at the same models — the extras just typed in,
    still on sprue and untouched — and between them it is hard to delete
    recorded work by accident. Paint six of ten, trim to six, and the four that
    go are the four never started.

    Removing every model deletes the unit with them. A unit with no models is a
    row that shows up in every count as a zero and can never become anything
    else; leaving it behind would make "delete the lot" the one correction that
    does not finish the job.

    `stage_events` is ON DELETE CASCADE, so a model's history leaves with it.
    That is right for a model that never existed and wrong for one that did,
    which is the whole reason a disposal is a different operation.
    """
    total = conn.execute('SELECT COUNT(*) AS n FROM models WHERE unit_id = ?',
                         (unit_id,)).fetchone()['n']
    count = max(0, min(count, total))
    if not count:
        return {'removed': 0, 'remaining': total, 'unit_deleted': False}

    doomed = [row['id'] for row in conn.execute("""
        SELECT m.id FROM models m
          JOIN stages s ON s.id = m.stage_id
         WHERE m.unit_id = ?
         ORDER BY s.position, m.id DESC
         LIMIT ?
    """, (unit_id, count))]
    conn.executemany('DELETE FROM models WHERE id = ?',
                     [(model_id,) for model_id in doomed])

    remaining = total - count
    if not remaining:
        delete_unit(conn, unit_id)
        return {'removed': count, 'remaining': 0, 'unit_deleted': True}
    _touch_unit(conn, unit_id)
    return {'removed': count, 'remaining': remaining, 'unit_deleted': False}


def delete_unit(conn, unit_id):
    """Only for a genuine data-entry mistake.

    Getting rid of models Clay actually had is a kit disposal, which keeps the
    rows. This is the undo for typing the wrong thing thirty seconds ago.
    """
    conn.execute('DELETE FROM units WHERE id = ?', (unit_id,))


# ── Stage movement ───────────────────────────────────────

def advance_unit(conn, unit_id, count=None, from_stage_id=None):
    """Move models forward one stage. Returns the number that moved.

    This is the app's primary interaction, so the no-argument call is the
    important one: ``advance_unit(conn, unit_id)`` walks the whole unit forward
    one step, which is what "I primed the squad" means.

    With ``count``, the *least advanced* models move — "I primed six of the
    ten" means the six that weren't primed yet, and making Clay identify which
    six is the failure this app is built to avoid. ``from_stage_id`` narrows it
    to one stage, for the per-stage increment control.
    """
    # The unit's own ladder, not the universal one: a model with no base steps
    # straight from Primed to Painted, and never lands on a stage that does not
    # apply to it.
    basing = conn.execute(
        'SELECT d.basing FROM units u JOIN datasheets d ON d.id = u.datasheet_id '
        'WHERE u.id = ?', (unit_id,)).fetchone()
    ladder = stages_for(conn, basing['basing'] if basing else None)
    following = {}
    for earlier, later in zip(ladder, ladder[1:]):
        following[earlier['id']] = later['id']

    sql = """
        SELECT m.id, m.stage_id FROM models m
          JOIN stages s ON s.id = m.stage_id
         WHERE m.unit_id = ? AND m.disposed_on IS NULL AND s.is_terminal = 0
    """
    args = [unit_id]
    if from_stage_id is not None:
        sql += ' AND m.stage_id = ?'
        args.append(from_stage_id)
    sql += ' ORDER BY s.position, m.id'
    candidates = conn.execute(sql, args).fetchall()
    if count is not None:
        candidates = candidates[:max(0, count)]

    stamp = db.now()
    moved = 0
    for model in candidates:
        target = following.get(model['stage_id'])
        if target is None:
            continue
        _apply_stage(conn, model['id'], model['stage_id'], target, stamp)
        moved += 1
    if moved:
        _touch_unit(conn, unit_id)
    return moved


def retreat_unit(conn, unit_id, count=None, from_stage_id=None):
    """Move models back one stage. Returns the number that moved.

    The mirror of ``advance_unit``, and the design's ``−1`` control. It exists
    because the primary interaction is a single tap with no confirmation: one
    fat-fingered "advance all" with wet hands would otherwise be unrecoverable,
    and an app you are afraid to tap is one you stop tapping.

    Where advance moves the *least* advanced models, this moves the *most*
    advanced — undoing the step that just happened rather than disturbing
    something further back. Models at the first owned stage have nowhere to go
    and are skipped; retreating never un-owns a model, because "I have not
    started this" and "I do not have this" are different facts.
    """
    basing = conn.execute(
        'SELECT d.basing FROM units u JOIN datasheets d ON d.id = u.datasheet_id '
        'WHERE u.id = ?', (unit_id,)).fetchone()
    ladder = stages_for(conn, basing['basing'] if basing else None)
    owned = [s for s in ladder if s['is_owned']]
    preceding = {}
    for earlier, later in zip(owned, owned[1:]):
        preceding[later['id']] = earlier['id']

    sql = """
        SELECT m.id, m.stage_id FROM models m
          JOIN stages s ON s.id = m.stage_id
         WHERE m.unit_id = ? AND m.disposed_on IS NULL AND s.is_owned = 1
    """
    args = [unit_id]
    if from_stage_id is not None:
        sql += ' AND m.stage_id = ?'
        args.append(from_stage_id)
    sql += ' ORDER BY s.position DESC, m.id DESC'
    candidates = conn.execute(sql, args).fetchall()
    if count is not None:
        candidates = candidates[:max(0, count)]

    stamp = db.now()
    moved = 0
    for model in candidates:
        target = preceding.get(model['stage_id'])
        if target is None:
            continue
        _apply_stage(conn, model['id'], model['stage_id'], target, stamp)
        moved += 1
    if moved:
        _touch_unit(conn, unit_id)
    return moved


def set_models_stage(conn, model_ids, stage_id):
    """Put a hand-picked set of models at one stage. Returns how many changed.

    The escape hatch behind the bulk selector, and the only place a specific
    model is addressed. Models already at the target stage are skipped rather
    than writing a no-op event — history should record changes, not clicks.
    """
    if not model_ids:
        return 0
    marks = ','.join('?' * len(model_ids))
    rows = conn.execute(
        f'SELECT id, unit_id, stage_id FROM models WHERE id IN ({marks})',
        list(model_ids)).fetchall()
    stamp = db.now()
    moved, units = 0, set()
    for model in rows:
        if model['stage_id'] == stage_id:
            continue
        _apply_stage(conn, model['id'], model['stage_id'], stage_id, stamp)
        units.add(model['unit_id'])
        moved += 1
    for unit_id in units:
        _touch_unit(conn, unit_id)
    return moved


def set_unit_stage_counts(conn, unit_id, stage_id, target_count):
    """Make ``target_count`` of a unit's models sit at ``stage_id``.

    What the reconcile-style "6 of these 10 are primed" input means. Models
    already at the stage stay put; the rest are drawn in until the count is met.

    Which models get drawn in matters, because this can move a model backwards
    and that is destructive if it picks badly. So it fills from behind first —
    the least advanced models, the same rule ``advance_unit`` uses — and only
    reaches past the target stage once there is nothing left behind it. When it
    does have to reach forward it takes the *closest* model first, so a finished
    one is the last thing disturbed rather than the first.

    Correcting downwards is legitimate: Clay saying "6 are primed" when the app
    thinks 8 are painted is him fixing the app, not the other way round.
    """
    at_stage = conn.execute(
        'SELECT COUNT(*) AS n FROM models WHERE unit_id = ? AND stage_id = ?',
        (unit_id, stage_id)).fetchone()['n']
    if at_stage >= target_count:
        return 0
    need = target_count - at_stage
    target_position = conn.execute(
        'SELECT position FROM stages WHERE id = ?', (stage_id,)).fetchone()['position']
    candidates = [r['id'] for r in conn.execute("""
        SELECT m.id FROM models m JOIN stages s ON s.id = m.stage_id
         WHERE m.unit_id = ? AND m.disposed_on IS NULL AND m.stage_id != ?
         ORDER BY CASE WHEN s.position < ? THEN 0 ELSE 1 END, s.position, m.id
    """, (unit_id, stage_id, target_position))]
    return set_models_stage(conn, candidates[:need], stage_id)


def _apply_stage(conn, model_id, from_stage_id, to_stage_id, stamp):
    conn.execute(
        'UPDATE models SET stage_id = ?, stage_changed_at = ? WHERE id = ?',
        (to_stage_id, stamp, model_id))
    # Append-only, and never skipped: "models finished per month" cannot be
    # reconstructed after the fact.
    conn.execute(
        'INSERT INTO stage_events (model_id, from_stage_id, to_stage_id, '
        'changed_at) VALUES (?, ?, ?, ?)',
        (model_id, from_stage_id, to_stage_id, stamp))


def _touch_unit(conn, unit_id):
    conn.execute('UPDATE units SET updated_at = ? WHERE id = ?',
                 (db.now(), unit_id))


# ── Kits ─────────────────────────────────────────────────

def list_kits(conn, include_disposed=True):
    sql = """
        SELECT k.*, f.name AS faction_name,
               COUNT(DISTINCT u.id) AS unit_count,
               COUNT(m.id)          AS model_count
          FROM kits k
          LEFT JOIN factions f ON f.id = k.faction_id
          LEFT JOIN units u    ON u.kit_id = k.id
          LEFT JOIN models m   ON m.unit_id = u.id AND m.disposed_on IS NULL
    """
    if not include_disposed:
        sql += " WHERE k.status IN ('owned', 'listed')"
    return [dict(r) for r in conn.execute(sql + ' GROUP BY k.id '
                                          'ORDER BY k.acquired_on DESC, k.id DESC')]


def get_kit(conn, kit_id):
    row = conn.execute(
        'SELECT k.*, f.name AS faction_name FROM kits k '
        'LEFT JOIN factions f ON f.id = k.faction_id WHERE k.id = ?',
        (kit_id,)).fetchone()
    return dict(row) if row else None


def create_kit(conn, name, **fields):
    cols = ('kit_template_id', 'faction_id', 'source', 'source_ref', 'acquired_on',
            'cost_cents', 'box_state', 'status', 'photo_url', 'notes')
    data = {c: fields.get(c) for c in cols}
    data['box_state'] = data['box_state'] or 'sealed'
    data['status'] = data['status'] or 'owned'
    cur = conn.execute(
        'INSERT INTO kits (name, kit_template_id, faction_id, source, source_ref, '
        'acquired_on, cost_cents, box_state, status, photo_url, notes, '
        'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (name, *[data[c] for c in cols], db.now(), db.now()))
    return cur.lastrowid


def update_kit(conn, kit_id, **fields):
    """Correct a kit's own details. Contents are edited through its units.

    Only the keys passed are touched, so a form that submits three fields
    cannot blank the other seven. `name` is refused empty rather than accepted
    and rendered as a nameless row.
    """
    kit = get_kit(conn, kit_id)
    if not kit:
        raise ValueError(f'no kit {kit_id}')

    editable = ('name', 'faction_id', 'source', 'source_ref', 'acquired_on',
                'cost_cents', 'box_state', 'notes', 'photo_url')
    updates = {k: v for k, v in fields.items() if k in editable}
    if 'name' in updates and not (updates['name'] or '').strip():
        raise ValueError('a kit needs a name')
    if not updates:
        return kit_id

    assignments = ', '.join(f'{k} = ?' for k in updates)
    conn.execute(f'UPDATE kits SET {assignments}, updated_at = ? WHERE id = ?',
                 (*updates.values(), db.now(), kit_id))
    return kit_id


def delete_kit(conn, kit_id):
    """Only for a genuine data-entry mistake — a mis-scan, a duplicate.

    Getting rid of models Clay actually had is `dispose_kit`, which keeps every
    row: a sold kit leaves ownership counts but stays queryable, which is what
    makes the spend history honest and "didn't I used to have one of those?"
    answerable. This is the undo for recording something that was never true.

    Its units go with it, and their models and stage history cascade from
    there. A scan that produced this kit keeps its own row — the scan really
    did happen, and the queue is the audit trail of how the collection was
    built — but stops pointing at a kit that no longer exists.
    """
    if not get_kit(conn, kit_id):
        raise ValueError(f'no kit {kit_id}')
    conn.execute('UPDATE scan_queue SET kit_id = NULL WHERE kit_id = ?', (kit_id,))
    conn.execute('DELETE FROM units WHERE kit_id = ?', (kit_id,))
    conn.execute('DELETE FROM kits WHERE id = ?', (kit_id,))


def instantiate_template(conn, kit_template_id, army_id=None, stage_id=None,
                         **kit_fields):
    """Turn a kit template into an owned kit plus every model inside it.

    This is what makes onboarding ~100 boxes survivable: one action produces the
    kit, its units, and all their model rows at "On sprue". Without it, the
    Ork Combat Patrol is twenty-six manual entries.
    """
    template = conn.execute('SELECT * FROM kit_templates WHERE id = ?',
                            (kit_template_id,)).fetchone()
    if not template:
        raise ValueError(f'no kit template {kit_template_id}')
    contents = conn.execute(
        'SELECT * FROM kit_template_units WHERE kit_template_id = ?',
        (kit_template_id,)).fetchall()
    if not contents:
        raise ValueError(
            f'kit template "{template["name"]}" has no contents defined — '
            'instantiating it would silently create an empty kit')

    kit_fields.setdefault('faction_id', template['faction_id'])
    kit_id = create_kit(conn, template['name'],
                        kit_template_id=kit_template_id, **kit_fields)
    if stage_id is None:
        stage_id = db.first_owned_stage(conn)['id']
    unit_ids = [create_unit(conn, row['datasheet_id'], row['model_count'],
                            army_id=army_id, kit_id=kit_id, stage_id=stage_id)
                for row in contents]
    return kit_id, unit_ids


def list_templates_with_contents(conn):
    """Templates that can actually be adopted — ones with contents defined.

    A template with no contents rows would adopt into nothing and look like it
    worked, so it is filtered out here rather than offered and then refused.
    """
    return [dict(r) for r in conn.execute("""
        SELECT t.id, t.name, t.year, f.name AS faction_name,
               COUNT(ktu.id)                     AS unit_count,
               COALESCE(SUM(ktu.model_count), 0) AS model_count
          FROM kit_templates t
          LEFT JOIN factions f ON f.id = t.faction_id
          JOIN kit_template_units ktu ON ktu.kit_template_id = t.id
         GROUP BY t.id
        HAVING unit_count > 0
         ORDER BY t.name, t.year
    """)]


def kits_awaiting_contents(conn):
    """Boxes recorded as owned whose contents nobody has defined yet.

    Ownership and contents are established separately on purpose: a scan can
    honestly record "this box is on my shelf" in one tap, months before anyone
    knows or cares which datasheets are inside it. That is the same bargain the
    paint stages make, and for the same reason — a step that must be completed
    before anything is saved is a step that stops the shelf being recorded at
    all.

    This is the backlog that bargain creates, and it has to stay visible or it
    becomes silent debt.
    """
    # source_ref carries the scanned code, set when the box was shelved. The
    # join back through barcodes is what makes a box defined later pay for
    # every copy already on the shelf: define Combat Patrol once and all three
    # recorded copies know what they are. A box added by hand has no code and
    # simply gets no suggestion.
    return [dict(r) for r in conn.execute("""
        SELECT k.*, k.source_ref AS code, f.name AS faction_name,
               t.id AS suggested_template_id, t.name AS suggested_template_name,
               t.year AS suggested_template_year
          FROM kits k
          LEFT JOIN factions f      ON f.id = k.faction_id
          LEFT JOIN barcodes b      ON b.code = k.source_ref
          LEFT JOIN kit_templates t ON t.id = b.kit_template_id
           AND EXISTS (SELECT 1 FROM kit_template_units ktu
                        WHERE ktu.kit_template_id = t.id)
         WHERE k.kit_template_id IS NULL
           AND k.status = 'owned'
           AND NOT EXISTS (SELECT 1 FROM units u WHERE u.kit_id = k.id)
         ORDER BY k.created_at DESC, k.id DESC
    """)]


def adopt_all_for_code(conn, code, kit_template_id=None, army_id=None,
                       stage_id=None):
    """Fill in every recorded box carrying this barcode, in one action.

    The payoff for defining contents once. Three copies of the same Combat
    Patrol were three taps before this, and the third tap is where a hundred-box
    onboarding stops being finished.

    Kits that already hold units are skipped rather than refused: a partly
    filled-in shelf is the normal state halfway through, and failing the whole
    action because one box is already done would be its own dead end.
    """
    if kit_template_id is None:
        row = conn.execute(
            'SELECT kit_template_id FROM barcodes WHERE code = ?', (code,)
        ).fetchone()
        kit_template_id = row['kit_template_id'] if row else None
    if not kit_template_id:
        raise ValueError('no kit template linked to that barcode yet')

    kits = conn.execute("""
        SELECT k.id FROM kits k
         WHERE k.source_ref = ? AND k.kit_template_id IS NULL
           AND k.status = 'owned'
           AND NOT EXISTS (SELECT 1 FROM units u WHERE u.kit_id = k.id)
         ORDER BY k.id
    """, (code,)).fetchall()

    filled = []
    for kit in kits:
        adopt_template(conn, kit['id'], kit_template_id, army_id=army_id,
                       stage_id=stage_id)
        filled.append(kit['id'])
    return filled


def adopt_template(conn, kit_id, kit_template_id, army_id=None, stage_id=None):
    """Give a recorded-but-empty box its contents, without creating a new kit.

    The other half of recording ownership first. Without this, "fill it in
    later" has no mechanism and every shelved box is permanently a mystery —
    which is the drift the spec says to build the recovery for *before* it
    happens, not after.

    Refuses a kit that already has units rather than adding a second set. A
    kit silently holding two copies of its contents overstates the collection
    in every count that matters, and nothing about the UI would show it.
    """
    kit = conn.execute('SELECT * FROM kits WHERE id = ?', (kit_id,)).fetchone()
    if not kit:
        raise ValueError(f'no kit {kit_id}')
    if conn.execute('SELECT 1 FROM units WHERE kit_id = ?', (kit_id,)).fetchone():
        raise ValueError('that kit already has contents — adopting a template '
                         'would give it a second set')

    template = conn.execute('SELECT * FROM kit_templates WHERE id = ?',
                            (kit_template_id,)).fetchone()
    if not template:
        raise ValueError(f'no kit template {kit_template_id}')
    contents = conn.execute(
        'SELECT * FROM kit_template_units WHERE kit_template_id = ?',
        (kit_template_id,)).fetchall()
    if not contents:
        raise ValueError(f'kit template "{template["name"]}" has no contents '
                         'defined — adopting it would change nothing')

    if stage_id is None:
        stage_id = db.first_owned_stage(conn)['id']
    unit_ids = [create_unit(conn, row['datasheet_id'], row['model_count'],
                            army_id=army_id, kit_id=kit_id, stage_id=stage_id)
                for row in contents]

    # The box keeps whatever Clay called it if he named it himself; a
    # placeholder gives way to the template's real name.
    name = kit['name']
    if name.startswith(UNIDENTIFIED_PREFIX):
        name = template['name']
    conn.execute('UPDATE kits SET kit_template_id = ?, name = ?, '
                 'faction_id = COALESCE(faction_id, ?), updated_at = ? '
                 'WHERE id = ?',
                 (kit_template_id, name, template['faction_id'], db.now(), kit_id))

    return unit_ids


def dispose_kit(conn, kit_id, status, disposed_on=None, price_cents=None,
                note=None):
    """Record a sale, trade or gift.

    A status change, never a delete. The kit and its models stay queryable —
    they leave ownership counts and effort totals, but "didn't I used to have
    one of those?" stays answerable and the spend history stays correct.
    """
    if status not in ('sold', 'traded', 'gifted', 'listed', 'owned'):
        raise ValueError(f'unknown kit status {status!r}')
    if status in ('owned', 'listed'):
        conn.execute(
            'UPDATE kits SET status = ?, disposed_on = NULL, '
            'disposed_price_cents = NULL, updated_at = ? WHERE id = ?',
            (status, db.now(), kit_id))
        return
    conn.execute(
        'UPDATE kits SET status = ?, disposed_on = ?, disposed_price_cents = ?, '
        'disposed_note = ?, updated_at = ? WHERE id = ?',
        (status, disposed_on or db.now()[:10], price_cents, note,
         db.now(), kit_id))


# ── Painting session ─────────────────────────────────────

def paintable_units(conn, army_id=None, limit=40):
    """Units with something left to do, freshest first.

    The picker for session mode. Sorted by most recently touched because the
    thing Clay worked on last night is overwhelmingly the thing he is about to
    pick up again.
    """
    units = list_units(conn, army_id=army_id)
    pending = [u for u in units if u['done_count'] < u['model_count']]
    pending.sort(key=lambda u: (u['last_activity'] or '', u['id']), reverse=True)
    return pending[:limit]


# ── Presentation helpers ─────────────────────────────────

def _pct(part, whole):
    return round(part / whole * 100) if whole else 0


def _segments(ladder, counts, total):
    """Stage bar segments — only the stages actually present, in order."""
    if not total:
        return []
    return [{'stage_id': s['id'], 'name': s['name'], 'position': s['position'],
             'count': counts[s['id']],
             'width': counts[s['id']] / total * 100,
             'is_terminal': bool(s['is_terminal']),
             'is_owned': bool(s['is_owned'])}
            for s in ladder if counts.get(s['id'])]


def export_inventory(conn, army_id=None, include_unassigned=False,
                    include_capability=True):
    """The inventory as a program needs it: join keys, points, and capability.

    A sibling of `inventory()` rather than an edit to it. The collection screen
    depends on that function's shape, and this one answers a different
    question — an external list optimiser asking "which detachment turns on the
    most of what Clay already owns, and what is the shortest paint queue to a
    better list?" It needs three things the screen does not:

    - **`bsdata_id`**, the join key. The local integer id means nothing outside
      this database and would silently mismatch after a re-sync.
    - **Army grouping.** Ork inventory must not leak into a Knights list.
    - **Points**, all tiers, uncollapsed.

    Everything else is the same aggregation and the same two traps handled the
    same way: disposed kits leave the counts, and wishlist models are counted
    apart from owned ones because a model Clay wants is not a model he has.
    """
    first_owned = db.first_owned_stage(conn)
    ladder = stage_ladder(conn)
    where, args = _export_scope(army_id, include_unassigned)

    rows = {r['datasheet_id']: dict(r) for r in conn.execute(f"""
        SELECT d.id AS datasheet_id, d.bsdata_id, d.name, d.effort,
               d.min_models, d.max_models, d.game_system,
               f.name AS faction_name,
               COALESCE(SUM(CASE WHEN st.is_owned THEN 1 ELSE 0 END), 0) AS owned,
               COALESCE(SUM(CASE WHEN NOT st.is_owned THEN 1 ELSE 0 END), 0)
                                                                   AS wishlist,
               COALESCE(SUM(CASE WHEN st.is_terminal THEN 1 ELSE 0 END), 0)
                                                               AS battle_ready,
               COALESCE(SUM(CASE WHEN st.is_owned AND st.position > ?
                                 THEN 1 ELSE 0 END), 0)          AS assembled
          FROM models m
          JOIN units u        ON u.id = m.unit_id
          -- Joined on the *model's* datasheet, not the unit's. Post-008 they
          -- agree unless Clay has said otherwise, and where they disagree the
          -- model is right: a magnetised Armiger built as a Warglaive is a
          -- Warglaive right now, and an uncommitted sprue is not anything yet.
          -- Counting it as its unit's datasheet would report the same plastic
          -- as both owned and buildable, and the optimiser would build a list
          -- on models that do not exist.
          JOIN datasheets d   ON d.id = m.datasheet_id
          JOIN stages st      ON st.id = m.stage_id
          LEFT JOIN factions f ON f.id = d.faction_id
         WHERE {_ACTIVE_UNIT} AND m.disposed_on IS NULL
               AND (d.variant IS NULL OR d.game_system <> 'wh40k')
               {where}
         GROUP BY d.id
    """, [first_owned['position'], *args])}

    for row in rows.values():
        row['by_stage'] = {}
        row['flexible'] = 0
        row['buildable_from_spare'] = 0

    for r in conn.execute(f"""
        SELECT m.datasheet_id, s.name AS stage, COUNT(*) AS n
          FROM models m
          JOIN units u  ON u.id = m.unit_id
          JOIN stages s ON s.id = m.stage_id
         WHERE m.datasheet_id IS NOT NULL AND {_ACTIVE_UNIT}
               AND m.disposed_on IS NULL {where}
         GROUP BY m.datasheet_id, s.id
    """, args):
        if r['datasheet_id'] in rows:
            rows[r['datasheet_id']]['by_stage'][r['stage']] = r['n']

    _export_capability(conn, rows, where, args, include_capability)
    _export_flexible(conn, rows, where, args)
    _export_points(conn, rows, army_id)

    # "A datasheet with zero owned models is omitted unless it has wishlist or
    # buildable-from-spare counts. This is an inventory, not a catalogue dump."
    # Magnetised models join that list: a Helverin Clay can field in two
    # minutes by swapping arms is exactly what this export exists to surface,
    # and it owns no Helverin at all by any other measure.
    out = [r for r in rows.values()
           if r['owned'] or r['wishlist'] or r['flexible']
           or r.get('buildable_from_spare')]
    out.sort(key=lambda r: (r['faction_name'] or '', r['name']))

    army = None
    if army_id:
        found = conn.execute("""
            SELECT a.id, a.name, f.name AS primary_faction
              FROM armies a LEFT JOIN factions f ON f.id = a.primary_faction_id
             WHERE a.id = ?""", (army_id,)).fetchone()
        army = dict(found) if found else None

    return {
        'generated_at': db.now(),
        'army': army,
        'stages': [{'name': s['name'], 'position': s['position'],
                    'is_owned': bool(s['is_owned']),
                    'is_terminal': bool(s['is_terminal'])} for s in ladder],
        'datasheets': [_export_row(r, include_capability) for r in out],
    }


def _export_scope(army_id, include_unassigned):
    """Which units the export may see.

    `units.army_id` is nullable by design — a sealed box not yet committed to
    an army is real plastic — so "include the unassigned" is a real question
    rather than an edge case. Off by default here (unlike the gap report, where
    it is on) because an army query that quietly swept in everything unfiled
    would not be an army query at all.
    """
    if army_id is None:
        return ('', []) if include_unassigned else (' AND u.army_id IS NOT NULL', [])
    if include_unassigned:
        return (' AND (u.army_id = ? OR u.army_id IS NULL)', [army_id])
    return (' AND u.army_id = ?', [army_id])


def _ensure_row(conn, rows, datasheet_id):
    """Give a datasheet a row even though no model *is* one yet.

    Capability alone earns a place in the inventory: three uncommitted Armiger
    sprues mean Clay can field a Helverin tonight, and a report that omitted it
    because he owns none would be answering a different question than the one
    the optimiser asked. Spec: omitted only when there is nothing at all —
    no models, no wishlist, no spare plastic, no magnets.
    """
    if datasheet_id in rows:
        return rows[datasheet_id]
    found = conn.execute("""
        SELECT d.id AS datasheet_id, d.bsdata_id, d.name, d.effort,
               d.min_models, d.max_models, d.game_system,
               f.name AS faction_name
          FROM datasheets d LEFT JOIN factions f ON f.id = d.faction_id
         WHERE d.id = ? AND (d.variant IS NULL OR d.game_system <> 'wh40k')
    """, (datasheet_id,)).fetchone()
    if not found:
        # Never invent a datasheet: a capability row pointing at a [Legends]
        # printing, or at nothing, is simply not emitted.
        return None
    rows[datasheet_id] = dict(found, owned=0, wishlist=0, battle_ready=0,
                              assembled=0, by_stage={}, flexible=0,
                              buildable_from_spare=0)
    return rows[datasheet_id]


def _export_capability(conn, rows, where, args, include_capability):
    """Magnetised models, and plastic that could still become something.

    Both answer "you already have this" without the model being it yet, and
    they are kept apart because the work differs: a swap is two minutes, a
    sprue is an evening.

    Neither is added to `by_stage`. A magnetised Armiger built as a Warglaive
    is counted once under Warglaive's stage breakdown and reported as
    *capability* under Helverin — so `sum(by_stage) == owned + wishlist` stays
    true for every row, which is the assertion that catches a double count
    before the optimiser turns it into a list Clay cannot field.
    """
    if not include_capability:
        return

    for r in conn.execute(f"""
        SELECT kd.datasheet_id, COUNT(*) AS n
          FROM models m
          JOIN units u ON u.id = m.unit_id
          JOIN kit_datasheets kd ON kd.kit_id = u.kit_id
          -- a sold sprue builds nothing
          JOIN stages s ON s.id = m.stage_id AND s.is_owned = 1
                       AND m.disposed_on IS NULL
         WHERE m.datasheet_id IS NULL AND {_ACTIVE_UNIT} {where}
         GROUP BY kd.datasheet_id
    """, args):
        row = _ensure_row(conn, rows, r['datasheet_id'])
        if row is not None:
            row['buildable_from_spare'] = r['n']


def _export_flexible(conn, rows, where, args):
    """Magnetised models, against every datasheet they could be.

    Reported per datasheet with the consumer deduplicating by model, per the
    spec. If that turns out awkward the fix is a top-level array of flexible
    models with their candidate datasheets — never silently picking one.
    """
    for r in conn.execute(f"""
        SELECT kd.datasheet_id, COUNT(DISTINCT m.id) AS n
          FROM models m
          JOIN units u ON u.id = m.unit_id
          JOIN kit_datasheets kd ON kd.kit_id = u.kit_id
         WHERE m.is_flexible = 1 AND {_ACTIVE_UNIT}
               AND m.disposed_on IS NULL {where}
         GROUP BY kd.datasheet_id
    """, args):
        row = _ensure_row(conn, rows, r['datasheet_id'])
        if row is not None:
            row['flexible'] = r['n']


def _export_points(conn, rows, army_id):
    """Every tier, uncollapsed.

    "Requisition Thresholds are exactly the thing a list optimizer has to
    reason about, and flattening here would hide the third-copy surcharge that
    changes list decisions."

    Faction-scoped prices are the other half: one Repulsor Executioner
    datasheet costs a Black Templar 255 and a Blood Angel 230. With an army
    naming a faction, only that faction's price is emitted; without one, every
    row goes out with its faction labelled rather than the app guessing.
    """
    faction_id = None
    if army_id:
        found = conn.execute('SELECT primary_faction_id FROM armies WHERE id = ?',
                             (army_id,)).fetchone()
        faction_id = found['primary_faction_id'] if found else None

    for row in rows.values():
        row['points'] = []
    if not rows:
        return
    marks = ','.join('?' * len(rows))
    for r in conn.execute(f"""
        SELECT p.datasheet_id, p.model_count, p.points, p.tier_min, p.tier_max,
               p.faction_id, f.name AS faction_name
          FROM datasheet_points p
          LEFT JOIN factions f ON f.id = p.faction_id
         WHERE p.datasheet_id IN ({marks})
         ORDER BY p.model_count, p.tier_min
    """, list(rows)):
        if faction_id and r['faction_id'] and r['faction_id'] != faction_id:
            continue
        entry = {'model_count': r['model_count'], 'points': r['points'],
                 'tier_min': r['tier_min'], 'tier_max': r['tier_max']}
        if r['faction_id'] and not faction_id:
            entry['faction'] = r['faction_name']
        rows[r['datasheet_id']]['points'].append(entry)


#: Every key an export row can carry, in the order they are written. The one
#: source of truth for `fields=`: the route validates against it, the CSV takes
#: its columns from it, and the 400 message lists it. Three copies of this
#: tuple would drift the first time a column was added.
#:
#: `buildable_from_spare` is last because it is the one that is conditional —
#: `include_capability=0` means it was never computed, so asking for it then is
#: refused rather than answered with a blank column.
EXPORT_FIELDS = (
    'bsdata_id', 'name', 'faction', 'game_system', 'min_models', 'max_models',
    'effort', 'owned', 'battle_ready', 'assembled', 'wishlist', 'by_stage',
    'flexible', 'points', 'buildable_from_spare',
)


def _export_row(row, include_capability):
    out = {
        'bsdata_id': row['bsdata_id'], 'name': row['name'],
        'faction': row['faction_name'], 'game_system': row['game_system'],
        'min_models': row['min_models'], 'max_models': row['max_models'],
        'effort': row['effort'],
        'owned': row['owned'], 'battle_ready': row['battle_ready'],
        'assembled': row['assembled'], 'wishlist': row['wishlist'],
        'by_stage': row['by_stage'], 'flexible': row['flexible'],
        'points': row['points'],
    }
    if include_capability:
        out['buildable_from_spare'] = row['buildable_from_spare']
    return out


#: What ``sort`` accepts, and the ORDER BY each one means. A dict rather than
#: string interpolation because the value arrives from a query string: an
#: unknown key falls back to 'name' and never reaches SQL.
INVENTORY_SORTS = {
    # Owned first, then alphabetical — the default, and the only one that reads
    # as an inventory rather than a report.
    'name':       '(owned_count + wanted_count) = 0, d.name',
    'owned':      'owned_count DESC, d.name',
    # "What should I paint next" — the most models with the least finished.
    'unfinished': '(owned_count - done_count) DESC, d.name',
    'points':     'points_low IS NULL, points_low, d.name',
    'expensive':  'points_high IS NULL, points_high DESC, d.name',
    # Stalest first: the shelf Clay has not touched in longest.
    'stale':      'last_activity IS NULL, last_activity, d.name',
    'recent':     'last_activity IS NULL, last_activity DESC, d.name',
}

#: The same keys with something a person can read, in the order they should be
#: offered. Kept apart from the SQL above so no template ever renders a
#: fragment of a query.
INVENTORY_SORT_LABELS = [
    ('name',       'Name'),
    ('owned',      'Most owned'),
    ('unfinished', 'Most left to do'),
    ('points',     'Cheapest first'),
    ('expensive',  'Priciest first'),
    ('stale',      'Untouched longest'),
    ('recent',     'Recently touched'),
]


def inventory(conn, query=None, faction_id=None, game_system=None,
              include_unowned=False, limit=200, stage_id=None,
              points_min=None, points_max=None, only_wanted=False,
              only_for_sale=False, sort='name'):
    """What Clay owns, one row per datasheet: how many, and what state.

    Grouped by datasheet rather than by army or by box, because the questions
    this answers are about the miniature and not where it happens to live:
    "how many Boyz do I have, and how many are built?"

    ``include_unowned`` is what makes this the own-it check as well as the
    inventory. Searching from a shop has to answer "you own none of these"
    just as clearly as "you own two", so with a query the walk starts at
    `datasheets` and ownership is a LEFT JOIN. Without one it starts from the
    collection, because a bare list of 2,895 datasheets is not an inventory.

    Two things are deliberately not merged:

    - **Disposed kits leave the counts.** A sold box keeps its rows, per the
      invariant, but Clay does not own it any more.
    - **Wishlist models are counted apart from owned ones.** They are things
      he wants, not things on the shelf.
    """
    first_owned = db.first_owned_stage(conn)
    clauses, args = [], []
    if query and query.strip():
        clauses.append('d.name LIKE ?')
        args.append(f'%{query.strip()}%')
    if faction_id:
        clauses.append('d.faction_id = ?')
        args.append(faction_id)
    if game_system:
        clauses.append('d.game_system = ?')
        args.append(game_system)
    # Deprecated 40,000 printings stay out of the picker and out of here, for
    # the same reason: Clay does not own a [Legends] Vyper, he owns a Vyper.
    clauses.append("(d.variant IS NULL OR d.game_system <> 'wh40k')")
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''

    # Points are a subquery, not a join: `datasheet_points` has a row per unit
    # size and per Requisition Threshold tier, so joining it would multiply
    # every ownership count by however many prices the datasheet has.
    #
    # `faction_id IS NULL OR = d.faction_id` is the scoping the importer's
    # docstring insists on. One Repulsor Executioner datasheet costs a Black
    # Templar 255 and a Blood Angel 230; an unscoped MIN would quietly answer
    # with whichever faction happened to sort first.
    points_scope = """
        SELECT %s(dp.points) FROM datasheet_points dp
         WHERE dp.datasheet_id = d.id
           AND (dp.faction_id IS NULL OR dp.faction_id = d.faction_id)
    """
    points_low = points_scope % 'MIN'
    points_high = points_scope % 'MAX'

    havings = [] if include_unowned else ['(owned_count > 0 OR wanted_count > 0)']
    if only_wanted:
        havings.append('wanted_count > 0')
    # The shortlist of things to part with. Still owned — this narrows the
    # collection to them rather than showing something outside it.
    if only_for_sale:
        havings.append('for_sale_count > 0')
    if stage_id:
        havings.append('at_stage > 0')
    # A datasheet with no points row has NULL for both, and NULL fails either
    # comparison — so asking a question about points never answers with rows
    # that have none. No explicit IS NOT NULL guard: one was here and did
    # nothing, which is worse than absent because it reads as load-bearing.
    if points_min is not None:
        havings.append('points_high >= ?')
    if points_max is not None:
        havings.append('points_low <= ?')
    having = ('HAVING ' + ' AND '.join(havings)) if havings else ''
    having_args = [v for v in (points_min, points_max) if v is not None]
    order = INVENTORY_SORTS.get(sort) or INVENTORY_SORTS['name']

    rows = [dict(r) for r in conn.execute(f"""
        SELECT d.id AS datasheet_id, d.name, d.effort, d.game_system, d.variant,
               f.name AS faction_name,
               COALESCE(SUM(CASE WHEN st.is_owned THEN 1 ELSE 0 END), 0)
                                                            AS owned_count,
               COALESCE(SUM(CASE WHEN st.id IS NOT NULL AND NOT st.is_owned
                                 THEN 1 ELSE 0 END), 0)     AS wanted_count,
               COALESCE(SUM(CASE WHEN st.is_owned AND st.id <> ?
                                 THEN 1 ELSE 0 END), 0)     AS built_count,
               COALESCE(SUM(CASE WHEN st.is_terminal THEN 1 ELSE 0 END), 0)
                                                            AS done_count,
               COUNT(DISTINCT CASE WHEN k.box_state = 'sealed' THEN k.id END)
                                                            AS sealed_boxes,
               COUNT(DISTINCT u.id)                         AS unit_count,
               COUNT(DISTINCT k.id)                         AS kit_count,
               d.basing, d.keywords,
               COALESCE(SUM(CASE WHEN st.id = ? THEN 1 ELSE 0 END), 0)
                                                            AS at_stage,
               COALESCE(SUM(CASE WHEN m.for_sale_on IS NOT NULL
                                 THEN 1 ELSE 0 END), 0)     AS for_sale_count,
               ({points_low})                               AS points_low,
               ({points_high})                              AS points_high,
               MAX(m.stage_changed_at)                      AS last_activity
          FROM datasheets d
          LEFT JOIN factions f ON f.id = d.faction_id
          LEFT JOIN units u    ON u.datasheet_id = d.id AND {_ACTIVE_UNIT}
          LEFT JOIN kits k     ON k.id = u.kit_id
          LEFT JOIN models m   ON m.unit_id = u.id AND m.disposed_on IS NULL
          LEFT JOIN stages st  ON st.id = m.stage_id
          {where}
         GROUP BY d.id
         {having}
         ORDER BY {order}
         LIMIT ?
    """, [first_owned['id'], stage_id or 0, *args, *having_args, limit])]

    if not rows:
        return rows

    ids = [r['datasheet_id'] for r in rows]
    marks = ','.join('?' * len(ids))
    spread = {}
    for r in conn.execute(f"""
        SELECT u.datasheet_id, m.stage_id, COUNT(*) AS n
          FROM units u JOIN models m ON m.unit_id = u.id AND m.disposed_on IS NULL
         WHERE u.datasheet_id IN ({marks}) AND {_ACTIVE_UNIT}
         GROUP BY u.datasheet_id, m.stage_id
    """, ids):
        spread.setdefault(r['datasheet_id'], {})[r['stage_id']] = r['n']

    # The units behind each row, so the collection can be acted on rather than
    # only read. Without these it renders a stage bar and offers no way to move
    # anything — the app's front door became a dead end.
    units_by_sheet = {}
    for unit in list_units(conn):
        units_by_sheet.setdefault(unit['datasheet_id'], []).append(unit)

    ladder = stage_ladder(conn)
    for row in rows:
        counts = spread.get(row['datasheet_id'], {})
        row['units'] = units_by_sheet.get(row['datasheet_id'], [])
        row['stage_counts'] = counts
        row['segments'] = _segments(ladder, counts,
                                    row['owned_count'] + row['wanted_count'])
        row['effort_total'] = row['owned_count'] * row['effort']
        row['effort_done'] = row['done_count'] * row['effort']
        row['completion'] = _pct(row['effort_done'], row['effort_total'])
        row['owns_any'] = row['owned_count'] > 0
        # Only worth asking about a model Clay actually has, and only while
        # nobody has answered.
        row['basing_hint'] = (basing_hint(row['keywords'])
                              if row['basing'] is None and row['owns_any']
                              else None)
    return rows


def home_summary(conn):
    """The one number the Home screen leads with, and what sits under it.

    Effort-weighted, per the invariant: a Knight and a Termagant are both
    "1 model", which makes a model-count percentage meaningless. The raw counts
    ride alongside so the percentage can be checked, never instead of it.

    ``sealed`` is counted from kits rather than models on purpose. A sealed box
    and an opened one both hold models "On sprue" — box_state is a fact about
    the box, and it is the one that carries a resale premium and the one that
    means "there is work here you have not started".
    """
    row = conn.execute(f"""
        SELECT COUNT(m.id)                                          AS models,
               COALESCE(SUM(CASE WHEN st.is_owned THEN 1 ELSE 0 END), 0) AS owned,
               COALESCE(SUM(CASE WHEN NOT st.is_owned THEN 1 ELSE 0 END), 0)
                                                                    AS wanted,
               COALESCE(SUM(CASE WHEN st.is_terminal THEN 1 ELSE 0 END), 0) AS done,
               COALESCE(SUM(CASE WHEN st.is_owned THEN d.effort ELSE 0 END), 0)
                                                                    AS effort_total,
               COALESCE(SUM(CASE WHEN st.is_terminal THEN d.effort ELSE 0 END), 0)
                                                                    AS effort_done
          FROM units u
          JOIN datasheets d ON d.id = u.datasheet_id
          JOIN models m     ON m.unit_id = u.id
          JOIN stages st    ON st.id = m.stage_id
         WHERE {_ACTIVE_UNIT}
    """).fetchone()

    sealed = conn.execute(
        "SELECT COUNT(*) AS n FROM kits "
        " WHERE status = 'owned' AND box_state = 'sealed'").fetchone()['n']

    # The shortlist, for the homepage's quick glance.
    for_sale = conn.execute(
        'SELECT COUNT(*) AS n FROM models WHERE for_sale_on IS NOT NULL'
    ).fetchone()['n']

    counts = {r['stage_id']: r['n'] for r in conn.execute(f"""
        SELECT m.stage_id, COUNT(*) AS n
          FROM units u JOIN models m ON m.unit_id = u.id AND m.disposed_on IS NULL
         WHERE {_ACTIVE_UNIT}
         GROUP BY m.stage_id
    """)}
    ladder = stage_ladder(conn)

    return {
        'models': row['owned'],
        'done': row['done'],
        'completion': _pct(row['effort_done'], row['effort_total']),
        'sealed': sealed,
        'wanted': row['wanted'],
        'for_sale': for_sale,
        'segments': _segments(ladder, counts, row['owned']),
    }


def stalled_unit(conn, days=14):
    """The thing to pick back up: furthest from done, longest untouched.

    Anti-abandonment, not statistics. A tracker that only shows totals gives
    Clay nothing to do when he opens it; this names one unit and offers the
    one action that moves it. Finished units are excluded — they are not work.
    """
    row = conn.execute(f"""
        SELECT u.id, u.nickname, d.name AS datasheet_name, a.name AS army_name,
               COUNT(m.id)                                          AS model_count,
               COALESCE(SUM(CASE WHEN st.is_terminal THEN 1 ELSE 0 END), 0) AS done,
               MAX(m.stage_changed_at)                              AS last_touched,
               julianday('now') - julianday(MAX(m.stage_changed_at)) AS idle_days
          FROM units u
          JOIN datasheets d ON d.id = u.datasheet_id
          JOIN models m     ON m.unit_id = u.id
          JOIN stages st    ON st.id = m.stage_id
          LEFT JOIN armies a ON a.id = u.army_id
         WHERE {_ACTIVE_UNIT} AND st.is_owned = 1
         GROUP BY u.id
        HAVING done < model_count
         ORDER BY idle_days DESC
         LIMIT 1
    """).fetchone()
    if not row:
        return None
    out = dict(row)
    out['display_name'] = out['nickname'] or out['datasheet_name']
    out['idle_days'] = int(out['idle_days'] or 0)
    out['stale'] = out['idle_days'] >= days
    return out


def owned_summary(conn, datasheet_id):
    """The own-it check: one datasheet, answered before you reach the till.

    Deliberately its own function rather than a filter over inventory(). This
    is the fastest question in the app and the one asked standing in a shop,
    and it has to answer for a datasheet Clay owns *none* of — where inventory,
    which walks from `units`, returns no row at all.
    """
    sheet = conn.execute("""
        SELECT d.*, f.name AS faction_name FROM datasheets d
          LEFT JOIN factions f ON f.id = d.faction_id WHERE d.id = ?
    """, (datasheet_id,)).fetchone()
    if not sheet:
        return None

    rows = inventory(conn, include_unowned=True)
    match = next((r for r in rows if r['datasheet_id'] == datasheet_id), None)
    summary = {
        'datasheet_id': datasheet_id,
        'name': sheet['name'],
        'faction_name': sheet['faction_name'],
        'game_system': sheet['game_system'],
        'variant': sheet['variant'],
        'owned_count': 0, 'wanted_count': 0, 'built_count': 0,
        'done_count': 0, 'sealed_boxes': 0, 'unit_count': 0, 'kit_count': 0,
        'stage_counts': {}, 'segments': [], 'completion': 0,
    }
    if match:
        summary.update(match)
    summary['owns_any'] = summary['owned_count'] > 0
    return summary


# Kill Team factions are slug-prefixed by their importer so they cannot merge
# with a 40,000 faction of the same name. That is deliberate and correct — a
# Kill Team of Sisters is a ten-operative team, not the Adepta Sororitas army —
# but it means several names appear twice, and a picker that prints the name
# alone gives Clay two identical options and no way to choose.
KILL_TEAM_SLUG_PREFIX = 'kt-'


def list_factions(conn):
    """Every faction, tagged with the game system it belongs to.

    The system comes from the slug rather than from the datasheets that
    reference it, because the slug is the contract the importer actually
    establishes — a faction with nothing imported against it yet still knows
    which game it is from.

    Each row also carries `datasheets`, how many point at it. A faction with
    none is a real choice when *tagging* — Clay can start an army for one
    before importing a thing — and a dead end when *filtering*, where picking
    it can only ever return nothing. The collection's filter drops those; the
    pickers that assign a faction keep them. Rows fall empty on their own: when
    a Kill Team is worked out to be Orks its operatives move to the Orks row,
    and the team's own row is left behind holding nothing.
    """
    rows = [dict(r) for r in conn.execute("""
        SELECT f.*, COUNT(d.id) AS datasheets
          FROM factions f
          LEFT JOIN datasheets d ON d.faction_id = f.id
         GROUP BY f.id
         ORDER BY f.name
    """)]
    for row in rows:
        kill_team = (row['slug'] or '').startswith(KILL_TEAM_SLUG_PREFIX)
        row['game_system'] = 'killteam' if kill_team else 'wh40k'
        row['system_label'] = 'Kill Team' if kill_team else 'Warhammer 40,000'
        # What a picker should print. The bare name is ambiguous exactly when
        # both games have it, so qualify only then — labelling every Kill Team
        # entry would add noise to the ones that were never ambiguous.
        row['label'] = row['name']
    seen = {}
    for row in rows:
        seen.setdefault(row['name'], []).append(row)
    for name, group in seen.items():
        if len(group) > 1:
            for row in group:
                row['label'] = f"{name} ({row['system_label']})"
    return rows


def search_datasheets(conn, query, limit=25):
    """Datasheet picker for manual add.

    Current 40,000 datasheets only — Legends and Crucible variants are excluded
    so a deprecated printing never gets picked by accident in a hurry.

    That exclusion is `variant IS NULL`, which is why the game system has to be
    part of it. Kill Team operatives carry their *edition* in the same column,
    deliberately: a 2021 box and its 2024 reprint hold different models, and
    which one Clay owns is his to say. Filtering them out as though they were
    deprecated 40,000 printings would import 1,450 operatives and show none of
    them — the box would still be unrecordable, with nothing on screen to
    explain why.
    """
    like = f'%{(query or "").strip()}%'
    return [dict(r) for r in conn.execute("""
        SELECT d.id, d.name, d.effort, d.min_models, d.max_models,
               d.game_system, d.variant, f.name AS faction_name
          FROM datasheets d LEFT JOIN factions f ON f.id = d.faction_id
         WHERE (d.variant IS NULL OR d.game_system <> 'wh40k')
           AND d.name LIKE ?
         ORDER BY CASE WHEN d.name LIKE ? THEN 0 ELSE 1 END,
                  d.game_system = 'wh40k' DESC, d.name
         LIMIT ?
    """, (like, f'{(query or "").strip()}%', limit))]
