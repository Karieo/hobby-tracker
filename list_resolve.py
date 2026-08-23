"""Turning a pasted name into a datasheet, or admitting it could not.

Section 7's resolution step. Four attempts in a fixed order, and the fourth is
giving up on purpose:

    alias  → a name Clay has identified before. Learned, permanent, free.
    exact  → the folded name matches a datasheet exactly.
    fuzzy  → near enough, by a margin wide enough to be sure.
    null   → a row with a picker on it, and no guess anywhere.

**Why the fourth one is the important one.** A wrong confident match is worse
than no match: it puts a unit in the report that Clay never wrote, silently
absorbs the models he owns of the wrong thing, and the number that comes out
the far end still looks like an answer. An unresolved row is visible, costs one
tap, and teaches the alias table so it never costs a tap again. Section 7 says
it plainly — "a wrong confident match is worse than an unresolved row".

NO rapidfuzz, AND NOT ITS token_set_ratio
-----------------------------------------
Fuzzy matching is stdlib `difflib` — the library `bulk_add` already uses for
near misses. Two fuzzy matchers in one app would be worse than either, and this
app has no build step to spend on a C extension.

The *algorithm* Section 7 names is a separate question, and it is wrong for
this job. See `similarity()`: `token_set_ratio` scores any strict subset as a
perfect match, so "Warboss on Warbike" resolved to Warboss at 100 — a wrong
confident match on the very example Section 7 uses to explain why aliases
exist. Sorting the words instead scores it 56 and sends it to the picker.

THE FACTION SCOPE IS LOAD-BEARING
---------------------------------
Measured against the imported data: **66 normalised datasheet names belong to
more than one faction.** Skarbrand, Bloodthirster, Bloodletters and Flesh
Hounds are each two datasheets — Chaos Daemons and World Eaters — and picking
one because it sorted first is precisely the silent wrong answer this module
exists to refuse. When the list names an army, its faction decides; when it
does not, an ambiguous name goes to the picker.
"""

import difflib
import re
from typing import NamedTuple, Optional

import database as db
from names import norm

# Section 7's thresholds, kept as names so the report can explain itself.
MATCH_SCORE = 90     # below this, never a match
MATCH_MARGIN = 10    # ...and never within this of the runner-up
CANDIDATES = 6       # how many near misses the picker offers
CANDIDATE_FLOOR = 50  # ...and how alike one has to be to be worth offering

_TRAILING_PAREN = re.compile(r'\s*[\(\[][^()\[\]]*[\)\]]\s*$')


class ResolvedEntry(NamedTuple):
    """A parsed line with a datasheet attached, or with the reason it has none.

    `parsed_count` is what the export actually said and `model_count` is what
    the report will use. They differ only when the count came back below the
    datasheet's legal minimum — see `_clamp_to_minimum` — and keeping both is
    what lets the screen show its working instead of quietly substituting a
    number.
    """
    raw_name: str
    model_count: int
    parsed_count: int
    points: Optional[int]
    position: int
    datasheet_id: Optional[int] = None
    datasheet_name: Optional[str] = None
    faction_name: Optional[str] = None
    resolved_by: Optional[str] = None
    score: Optional[int] = None
    why: Optional[str] = None
    candidates: tuple = ()


def normalise(name):
    """The fold two names are compared on.

    `names.norm` already lowercases, strips punctuation and collapses
    whitespace — the same fold the rules-data importer uses, and sharing it is
    what stops a name matching in one importer and silently failing in another.
    The trailing parenthetical comes off first, because norm would turn
    "Boyz (Legends)" into "boyz legends" and lose the chance.
    """
    return norm(_TRAILING_PAREN.sub('', name or ''))


def similarity(a, b):
    """How alike two folded names are, 0–100. Word order does not count.

    Sort the words and compare — rapidfuzz calls this `token_sort_ratio`. It
    beats plain sequence similarity on the case that actually turns up:
    "Squad Intercessor" and "Intercessor Squad" are one unit typed two ways and
    score 55 unsorted, 100 sorted.

    NOT `token_set_ratio`, which is what Section 7 asks for, because it is
    **wrong for this job** and measurably so. That algorithm compares the shared
    words against what each name adds, which makes any strict subset a perfect
    match: `token_set_ratio("warboss on warbike", "warboss")` is **100**. Run
    against the real Ork datasheets it resolved "Warboss on Warbike" to Warboss,
    confidently, with no runner-up near enough to trip the margin rule — a
    wrong confident match on the very example Section 7 uses to explain why the
    alias table has to exist.

    Sorting instead scores that pair 56 and sends it to the picker, where Clay
    answers once and the alias table remembers. Every case the spec wanted from
    token_set still works: word order is free, and a one-letter typo
    ("Killa Kanz" against "Killa Kans") still scores 90.
    """
    words_a = ' '.join(sorted(a.split()))
    words_b = ' '.join(sorted(b.split()))
    if not words_a or not words_b:
        return 100 if a == b else 0
    return round(difflib.SequenceMatcher(None, words_a, words_b).ratio() * 100)


def _pool(conn, game_system='wh40k'):
    """Every datasheet a pasted name could legitimately mean.

    Deprecated 40,000 printings stay out for the same reason they stay out of
    the picker: Clay does not own a [Legends] Vyper, he owns a Vyper. The game
    system is a parameter rather than a guess — "Boyz" is a datasheet in both
    40,000 and Kill Team, and an army list with points in it is a 40,000 list.
    """
    sql = """
        SELECT d.id, d.name, d.game_system, d.faction_id, d.min_models,
               d.max_models, f.name AS faction_name
          FROM datasheets d
          LEFT JOIN factions f ON f.id = d.faction_id
         WHERE (d.variant IS NULL OR d.game_system <> 'wh40k')
    """
    args = []
    if game_system:
        sql += ' AND d.game_system = ?'
        args.append(game_system)
    return [dict(r) for r in conn.execute(sql, args)]


def _aliases(conn):
    return {r['alias']: r['datasheet_id']
            for r in conn.execute('SELECT alias, datasheet_id '
                                  'FROM datasheet_aliases')}


def list_faction(conn, list_id):
    """The faction to scope by: the list's own, else its army's.

    Section 7 scopes by `army_id`; this file's lists also carry a `faction_id`
    of their own, set when the list was created. Either is a statement about
    what the list is, so either will do, and the list's own wins because it is
    the more specific.
    """
    row = conn.execute("""
        SELECT l.faction_id, a.primary_faction_id
          FROM army_lists l
          LEFT JOIN armies a ON a.id = l.army_id
         WHERE l.id = ?
    """, (list_id,)).fetchone()
    if not row:
        return None
    return row['faction_id'] or row['primary_faction_id']


def _clamp_to_minimum(count, sheet):
    """Never fewer models than the datasheet legally allows.

    The original spec: "Pull model counts from the text where present, default
    to minimum unit size where not." This is that rule, applied after the
    datasheet is known because that is the first moment the minimum exists.

    It is also the safety net under `list_parse`'s known false negative. A New
    Recruit block that lists its models with no wargear beneath them reads as
    one model; Boyz have a minimum of ten, so the report says ten rather than
    sending Clay to buy nineteen he already owns. Only ever upward, and
    `parsed_count` keeps what the text said so the screen can show its working.
    """
    minimum = sheet.get('min_models') or 0
    return max(count, minimum) if minimum else count


def resolve_entries(conn, entries, faction_id=None, game_system='wh40k'):
    """Attach a datasheet to each parsed line, or a reason it has none."""
    pool = _pool(conn, game_system)
    aliases = _aliases(conn)

    by_name = {}
    for sheet in pool:
        by_name.setdefault(normalise(sheet['name']), []).append(sheet)
    by_id = {sheet['id']: sheet for sheet in pool}

    return [_resolve_one(entry, by_name, by_id, aliases, pool, faction_id)
            for entry in entries]


def _resolve_one(entry, by_name, by_id, aliases, pool, faction_id):
    key = normalise(entry.raw_name)
    blank = ResolvedEntry(raw_name=entry.raw_name, model_count=entry.model_count,
                          parsed_count=entry.model_count, points=entry.points,
                          position=entry.position)
    if not key:
        return blank._replace(why='nothing to look up')

    # 1 · Alias. A name Clay has already identified is never asked about twice.
    sheet = by_id.get(aliases.get(key))
    if sheet:
        return _hit(blank, sheet, 'alias', 100)

    # 2 · Exact, on the same fold the rules-data importer uses.
    matches = by_name.get(key, [])
    if len(matches) == 1:
        return _hit(blank, matches[0], 'exact', 100)
    if len(matches) > 1:
        scoped = [s for s in matches if s['faction_id'] == faction_id]
        if len(scoped) == 1:
            return _hit(blank, scoped[0], 'exact', 100)
        where = ', '.join(sorted({s['faction_name'] or s['game_system']
                                  for s in matches}))
        return blank._replace(
            why=f'more than one datasheet named this — {where}',
            candidates=tuple(matches[:CANDIDATES]))

    # 3 · Fuzzy, scoped to the list's faction when there is one. Section 7:
    #     "Cuts the candidate pool and kills most false positives."
    scoped = [s for s in pool if s['faction_id'] == faction_id] if faction_id else pool
    ranked = _rank(key, scoped or pool)
    if not ranked:
        return blank._replace(why='no datasheet with this name')

    best_score, best = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0
    if best_score >= MATCH_SCORE and best_score - runner_up >= MATCH_MARGIN:
        return _hit(blank, best, 'fuzzy', best_score)

    # 4 · Give up, visibly, with something to tap.
    if best_score >= MATCH_SCORE:
        why = (f'two names score alike — {best["name"]} and '
               f'{ranked[1][1]["name"]}, {best_score} against {runner_up}')
    else:
        why = 'no datasheet with this name'

    # The picker is only worth having if what it offers is plausible. When the
    # faction-scoped pool has nothing close, the name is probably not that
    # faction's at all — an ally, or a list pasted before the faction was set —
    # and offering four unrelated Ork units for "Intercesor Squad" is worse
    # than offering none. So candidates come from everything in that case.
    # Auto-matching stays scoped; only what Clay is shown widens.
    if best_score < CANDIDATE_FLOOR and scoped and len(scoped) < len(pool):
        ranked = _rank(key, pool)
    return blank._replace(
        why=why,
        candidates=tuple(s for score, s in ranked[:CANDIDATES]
                         if score >= CANDIDATE_FLOOR))


def _rank(key, sheets):
    """Every datasheet scored against the name, best first."""
    return sorted(((similarity(key, normalise(s['name'])), s) for s in sheets),
                  key=lambda t: -t[0])


def _hit(blank, sheet, how, score):
    return blank._replace(
        datasheet_id=sheet['id'], datasheet_name=sheet['name'],
        faction_name=sheet['faction_name'], resolved_by=how, score=score,
        model_count=_clamp_to_minimum(blank.parsed_count, sheet))


def learn_alias(conn, name, datasheet_id):
    """Remember that this spelling means this datasheet. Idempotent.

    "Step 5's write-back is the whole point. If you have to re-answer 'which
    datasheet is *Warboss on Warbike*?' every time you paste a list, you'll
    stop pasting lists."

    A later correction wins: the alias is Clay's answer, and his second answer
    is a better one than his first.
    """
    key = normalise(name)
    if not key:
        return None
    conn.execute(
        'INSERT INTO datasheet_aliases (alias, datasheet_id, created_at) '
        'VALUES (?, ?, ?) ON CONFLICT(alias) DO UPDATE SET datasheet_id = ?',
        (key, datasheet_id, db.now(), datasheet_id))
    return key


def resolve_entry(conn, entry_id, datasheet_id):
    """Point a stored entry at a datasheet, and learn the name while doing it.

    The write-back is not optional and does not belong to the caller: every
    route that resolves a row by hand has to teach the alias table, so it
    happens here where it cannot be forgotten.
    """
    entry = conn.execute('SELECT * FROM list_entries WHERE id = ?',
                         (entry_id,)).fetchone()
    if not entry:
        raise ValueError(f'no list entry {entry_id}')
    sheet = conn.execute('SELECT id, min_models FROM datasheets WHERE id = ?',
                         (datasheet_id,)).fetchone()
    if not sheet:
        raise ValueError(f'no datasheet {datasheet_id}')

    if entry['raw_name']:
        learn_alias(conn, entry['raw_name'], datasheet_id)
    count = _clamp_to_minimum(entry['model_count'], dict(sheet))
    conn.execute("UPDATE list_entries SET datasheet_id = ?, model_count = ?, "
                 "resolved_by = 'manual' WHERE id = ?",
                 (datasheet_id, count, entry_id))
    return count
