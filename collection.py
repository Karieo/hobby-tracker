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
          LEFT JOIN models m     ON m.unit_id = u.id
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
          LEFT JOIN models m     ON m.unit_id = u.id
          LEFT JOIN stages st    ON st.id = m.stage_id
         WHERE u.army_id IS NULL AND {_ACTIVE_UNIT}
    """).fetchone())

    # Per-army stage spread, so a card carries the same readable bar a unit does.
    spread = {}
    for r in conn.execute(f"""
        SELECT u.army_id, m.stage_id, COUNT(*) AS n
          FROM units u JOIN models m ON m.unit_id = u.id
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
          LEFT JOIN models m  ON m.unit_id = u.id
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
          LEFT JOIN models m   ON m.unit_id = u.id
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
    out = []
    for stage in stage_ladder(conn):
        n = counts.get(stage['id'], 0)
        out.append({**stage, 'count': n, 'percent': _pct(n, total),
                    'can_advance': n > 0 and not stage['is_terminal']})
    return out


def unit_models(conn, unit_id):
    return [dict(r) for r in conn.execute("""
        SELECT m.*, s.name AS stage_name, s.position, s.is_terminal, s.is_owned
          FROM models m JOIN stages s ON s.id = m.stage_id
         WHERE m.unit_id = ?
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


def add_models(conn, unit_id, count, stage_id):
    """Append models to an existing unit, all at one stage."""
    stamp = db.now()
    ids = []
    for _ in range(count):
        cur = conn.execute(
            'INSERT INTO models (unit_id, stage_id, stage_changed_at, created_at) '
            'VALUES (?, ?, ?, ?)', (unit_id, stage_id, stamp, stamp))
        ids.append(cur.lastrowid)
        # The starting position is history too: without it, a model that never
        # moves has no record of when it arrived.
        conn.execute(
            'INSERT INTO stage_events (model_id, from_stage_id, to_stage_id, '
            'changed_at) VALUES (?, NULL, ?, ?)', (cur.lastrowid, stage_id, stamp))
    _touch_unit(conn, unit_id)
    return ids


def move_unit_to_army(conn, unit_id, army_id):
    """Allied units get reshuffled; None parks a unit back in Unassigned."""
    conn.execute('UPDATE units SET army_id = ?, updated_at = ? WHERE id = ?',
                 (army_id, db.now(), unit_id))


def update_unit(conn, unit_id, nickname=None, notes=None):
    conn.execute('UPDATE units SET nickname = ?, notes = ?, updated_at = ? '
                 'WHERE id = ?', (nickname or None, notes or None,
                                  db.now(), unit_id))


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
         WHERE m.unit_id = ? AND s.is_terminal = 0
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
         WHERE m.unit_id = ? AND m.stage_id != ?
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
          LEFT JOIN models m   ON m.unit_id = u.id
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

    # The code this box was scanned with now has a meaning, so bank it. This
    # is the only way most barcodes will ever be learned: contents can be
    # researched from published sources, EANs essentially cannot, and the
    # person holding the box is the one with the number. Saying "this is a
    # Combat Patrol" once must teach the scanner, or the next copy of the same
    # box comes up unknown again and the answer is retyped forever.
    #
    # Not a guess: Clay picked this template for this physical box. That is the
    # same confirmation the review form asks for everywhere else.
    code = (kit['source_ref'] or '').strip()
    if code:
        import scanning as scan          # local: scanning imports this module
        if scan.normalise_code(code) == code:
            scan.link_barcode(conn, code, kit_template_id, link_source='scanned')
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


def inventory(conn, query=None, faction_id=None, game_system=None,
              include_unowned=False, limit=200):
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
    having = '' if include_unowned else 'HAVING owned_count > 0 OR wanted_count > 0'

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
               MAX(m.stage_changed_at)                      AS last_activity
          FROM datasheets d
          LEFT JOIN factions f ON f.id = d.faction_id
          LEFT JOIN units u    ON u.datasheet_id = d.id AND {_ACTIVE_UNIT}
          LEFT JOIN kits k     ON k.id = u.kit_id
          LEFT JOIN models m   ON m.unit_id = u.id
          LEFT JOIN stages st  ON st.id = m.stage_id
          {where}
         GROUP BY d.id
         {having}
         ORDER BY (owned_count + wanted_count) = 0, d.name
         LIMIT ?
    """, [first_owned['id'], *args, limit])]

    if not rows:
        return rows

    ids = [r['datasheet_id'] for r in rows]
    marks = ','.join('?' * len(ids))
    spread = {}
    for r in conn.execute(f"""
        SELECT u.datasheet_id, m.stage_id, COUNT(*) AS n
          FROM units u JOIN models m ON m.unit_id = u.id
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


def list_factions(conn):
    return [dict(r) for r in conn.execute(
        'SELECT * FROM factions ORDER BY name')]


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
