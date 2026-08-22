"""Adding models that have no barcode: paste a list, confirm, done.

Scanning is the front door for boxes. It is no door at all for everything
already built, painted or split out of a box years ago — there is nothing left
to scan, and those are the models most likely to be missing from the app.
Recorded one form at a time they never get recorded.

So: paste one line per unit, in the shape people already write these lists.

    20 Boyz built
    Boyz x20
    Trukk primed
    5 Nobz

The parsing is deliberately forgiving about shape and completely unforgiving
about names. A count can lead or trail, a stage word is optional, blank lines
and bullets are ignored — but a unit name either matches an imported BSData
datasheet exactly, or it comes back unresolved with candidates for Clay to pick
from. Nothing is guessed and nothing is silently dropped. Spec §12: a dropped
line is a shortfall discovered at the till months later.

Ambiguity is treated the same as no match. "Boyz" exists in both 40k and Kill
Team, and several names repeat across factions — picking one because it sorted
first is exactly the silent corruption the rules-data importer refuses to do.
"""

import re

import collection as col
import database as db
from names import norm

# Stage words people actually write, mapped onto the ladder. Deliberately not
# the stage names themselves: nobody writes "Base prepared" down a shelf.
STAGE_WORDS = {
    'sprue': 'On sprue', 'onsprue': 'On sprue', 'new': 'On sprue',
    'nib': 'On sprue', 'sealed': 'On sprue', 'unbuilt': 'On sprue',
    'boxed': 'On sprue',
    'built': 'Assembled', 'assembled': 'Assembled', 'made': 'Assembled',
    'glued': 'Assembled',
    'primed': 'Primed', 'undercoated': 'Primed',
    'painted': 'Painted',
    'based': 'Based',
    'done': 'Battle ready', 'finished': 'Battle ready',
    'ready': 'Battle ready', 'battleready': 'Battle ready',
}

_LEADING_COUNT = re.compile(r'^(\d+)\s*[x×]?\s+(.*)$', re.IGNORECASE)
_TRAILING_COUNT = re.compile(r'^(.*?)\s*[x×]\s*(\d+)$', re.IGNORECASE)
_BULLET = re.compile(r'^[\s\-*•·—]+')


def parse_lines(text):
    """Turn pasted text into `{count, name, stage_word, raw}` per line.

    Blank lines and bullets are skipped rather than reported: they are how
    people format lists, not mistakes to be corrected.
    """
    parsed = []
    for raw in (text or '').splitlines():
        line = _BULLET.sub('', raw).strip()
        line = line.rstrip('.,;')
        if not line:
            continue

        stage_word = None
        words = line.split()
        # Only ever the last word: "Primaris Intercessors" must not lose a word
        # to a stage that happens to appear mid-name.
        if len(words) > 1 and norm(words[-1]).replace(' ', '') in STAGE_WORDS:
            stage_word = norm(words[-1]).replace(' ', '')
            line = ' '.join(words[:-1]).rstrip(' -–—,')

        count = 1
        match = _LEADING_COUNT.match(line)
        if match:
            count, line = int(match.group(1)), match.group(2)
        else:
            match = _TRAILING_COUNT.match(line)
            if match:
                line, count = match.group(1), int(match.group(2))

        name = line.strip()
        if not name:
            continue
        parsed.append({'raw': raw.strip(), 'name': name,
                       'count': max(1, count), 'stage_word': stage_word})
    return parsed


def match_lines(conn, parsed, game_system=None):
    """Resolve each parsed line to a datasheet, or report why it could not be.

    Exact match on the folded name, the same fold the rules-data importer uses.
    Ambiguity counts as unresolved: several names exist in more than one faction
    and in more than one game system, and picking one silently is how the wrong
    Rhino ends up in the collection.
    """
    rows = conn.execute("""
        SELECT d.id, d.name, d.game_system, d.min_models, f.name AS faction_name
          FROM datasheets d LEFT JOIN factions f ON f.id = d.faction_id
         WHERE d.variant IS NULL OR d.game_system <> 'wh40k'
    """).fetchall()
    by_key = {}
    for row in rows:
        by_key.setdefault(norm(row['name']), []).append(row)

    matched = []
    for line in parsed:
        candidates = by_key.get(norm(line['name']), [])
        if game_system:
            scoped = [r for r in candidates if r['game_system'] == game_system]
            # Only narrow when it helps. Falling back to the unscoped set keeps
            # a Kill Team name usable from a 40k-scoped paste.
            if scoped:
                candidates = scoped

        result = dict(line, datasheet_id=None, datasheet_name=None,
                      faction_name=None, why=None, candidates=[])
        if len(candidates) == 1:
            row = candidates[0]
            result.update(datasheet_id=row['id'], datasheet_name=row['name'],
                          faction_name=row['faction_name'])
        elif len(candidates) > 1:
            where = ', '.join(sorted({r['faction_name'] or r['game_system']
                                      for r in candidates}))
            result['why'] = f'more than one datasheet named this — {where}'
            result['candidates'] = [dict(r) for r in candidates]
        else:
            result['why'] = 'no datasheet with this name'
            result['candidates'] = _near_misses(conn, line['name'], rows)
        matched.append(result)
    return matched


def _near_misses(conn, name, rows, limit=8):
    """Candidates for a name that matched nothing, for the picker to offer.

    `search_datasheets` is a raw SQL LIKE, so it finds nothing for the case
    that actually needs help: a typo with an extra or missing letter. "Boyzz"
    returns no rows, and an empty picker on a line Clay has to resolve is a
    dead end.

    So fall back to the folded names already loaded, matching in either
    direction, then to a prefix of the fold. Word-level overlap catches the
    other common shape — remembering half a long name.
    """
    found = col.search_datasheets(conn, name, limit=limit)
    if found:
        return found

    key = norm(name)
    if not key:
        return []
    words = set(key.split())

    def score(row):
        other = norm(row['name'])
        if key in other or other in key:
            return 0
        if other[:4] and other[:4] == key[:4]:
            return 1
        overlap = words & set(other.split())
        return 2 if overlap else 99

    ranked = sorted(((score(r), r['name'], r) for r in rows),
                    key=lambda t: (t[0], t[1]))
    return [dict(row) for rank, _name, row in ranked if rank < 99][:limit]


def stage_ids(conn):
    """Stage word → stage id, resolved against the ladder in the database."""
    ladder = {s['name']: s['id'] for s in col.stage_ladder(conn)}
    return {word: ladder[name] for word, name in STAGE_WORDS.items()
            if name in ladder}


def commit(conn, rows, default_stage_id=None, army_id=None):
    """Create a unit per confirmed row.

    ``rows`` is ``{datasheet_id, count, stage_word|stage_id, skip}``. A row with
    no datasheet is refused rather than skipped quietly — the whole point is
    that a line Clay pasted never vanishes without him saying so.
    """
    if default_stage_id is None:
        default_stage_id = db.first_owned_stage(conn)['id']
    words = stage_ids(conn)

    wanted = [r for r in rows if not r.get('skip')]
    unresolved = [r for r in wanted if not r.get('datasheet_id')]
    if unresolved:
        raise ValueError(
            f'{len(unresolved)} line(s) still need a datasheet — pick one for '
            'each, or mark them skipped')

    created = []
    for row in wanted:
        stage_id = (row.get('stage_id')
                    or words.get(row.get('stage_word'))
                    or default_stage_id)
        unit_id = col.create_unit(conn, row['datasheet_id'],
                                  max(1, int(row.get('count') or 1)),
                                  army_id=army_id, stage_id=stage_id)
        created.append(unit_id)
    return created
