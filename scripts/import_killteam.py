#!/usr/bin/env python3
"""Import Kill Team operatives from the BSData Kill Team catalogues.

    python3 scripts/import_killteam.py            # import, then print the report
    python3 scripts/import_killteam.py --dry-run  # report only, write nothing

Why this exists
---------------
Clay owns Kill Team boxes. Their models are miniatures on the same shelf,
going sprue -> battle ready like everything else, but nothing in `datasheets`
could represent them: BSData keeps Kill Team in its own repository, as XML
`.cat` files, and `wh40k-11e` carries no operatives at all. A box whose
contents cannot point at any datasheet cannot be recorded, so those boxes were
simply unrecordable.

What comes across
-----------------
One row per operative — a ``selectionEntry`` of ``type="model"``. Everything
else in a catalogue is weapons, equipment and ploys, which the collection has
no use for: it needs an identity to hang models off, not rules.

``min_models``/``max_models`` are 1 because a Kill Team operative *is* one
model. That matters more than it looks: the contents form pre-fills the model
count from these, so picking an operative fills in 1 rather than nothing.

Editions
--------
The repository holds three: the original catalogues (unprefixed filenames),
``2021 - *`` and ``2024 - *``. The same operative appears in more than one, and
they are not interchangeable — a 2021 box and its 2024 reprint hold different
models. So all three are imported and the edition is kept in ``variant``,
exactly as the 2021/2024 Combat Patrol problem is handled everywhere else in
this app: never guess which one Clay owns, show him both and let him say.

Points
------
None. Kill Team does not use the Munitorum Field Manual, and no points source
here is licensed or official. `datasheet_points` stays empty for these rows,
which is honest rather than incomplete — the gap report and list builder are
40,000-only anyway.
"""

import argparse
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from import_bsdata import slugify

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KILLTEAM_DIR = os.path.join(BASE_DIR, 'data', 'killteam')

IMPORTER = 'killteam'
GAME_SYSTEM = 'killteam'

# An operative is one model, always. Kill Team has no unit sizes.
OPERATIVE_MODELS = 1

EDITION_RE = re.compile(r'^(\d{4})\s+-\s+')


def edition_of(filename):
    """"2024 - Angels of Death.cat" -> "2024"; unprefixed -> the first edition.

    The unprefixed catalogues are the original release. Naming that explicitly
    beats leaving it NULL: NULL would read as "no edition", and the whole point
    of keeping editions is that Clay has to be able to tell them apart.
    """
    m = EDITION_RE.match(filename)
    return m.group(1) if m else '2018'


def team_name(root, filename):
    """The catalogue's own name, falling back to the filename."""
    name = (root.get('name') or '').strip()
    if name:
        return EDITION_RE.sub('', name).strip()
    return EDITION_RE.sub('', os.path.splitext(filename)[0]).strip()


def operatives(root, ns):
    """Every selectionEntry that is a model, in document order."""
    return [e for e in root.findall('.//b:selectionEntry', ns)
            if e.get('type') == 'model']


def parse_catalogue(path):
    """(team, edition, [(bsdata_id, name)]) for one .cat file."""
    filename = os.path.basename(path)
    root = ET.parse(path).getroot()
    ns = {'b': root.tag.split('}')[0].strip('{')}
    found = []
    for entry in operatives(root, ns):
        eid, name = entry.get('id'), (entry.get('name') or '').strip()
        if eid and name:
            found.append((eid, name))
    return team_name(root, filename), edition_of(filename), found


#: Categories in the 2024 game system that are roles rather than allegiances.
#: Everything else there is a faction or an alliance.
_NOT_A_FACTION = {'Operative', 'Configuration', 'Reference', 'Leader',
                  'Gunner', 'Heavy Gunner', 'Psyker', 'Medic', 'Warrior'}


def faction_categories(directory):
    """Which allegiance categories each team's catalogue claims.

    Clay: *"when I filter for orks it filters out my ork kill team."* It did,
    because the importer matched a team's **name** against a 40,000 faction —
    so Orks matched Orks, and Kommandos, Wrecka Krew and Kommando each got a
    faction row of their own that no 40,000 filter would ever reach. 1158 of
    1450 operatives were parked on rows like that.

    The allegiance is in the data, once you know where: the 2024 game system
    defines category entries — Ork, Aeldari, Imperium, Drukhari — and each
    team's catalogue references the ones it belongs to by id. Nothing here is
    recalled; a team that claims no category gets no faction, and says so.

    Returns {team name: {category names}} and {category: how many teams claim
    it}, the second because breadth is how specificity is measured: Drukhari
    appears once and Imperium nineteen times, so when Mandrakes claims both,
    the rarer one is the answer. That ordering is read off the data rather
    than asserted.
    """
    gst = os.path.join(directory, '2024 - Kill Team.gst')
    if not os.path.exists(gst):
        return {}, Counter()
    with open(gst, encoding='utf-8', errors='replace') as fh:
        text = fh.read()
    ids = {m.group(2): m.group(1).replace('&apos;', '\u2019')
           for m in re.finditer(
               r'<categoryEntry\s+name="([^"]*)"\s+id="([^"]+)"', text)
           if m.group(1) not in _NOT_A_FACTION}

    claims, breadth = {}, Counter()
    for filename in sorted(f for f in os.listdir(directory) if f.endswith('.cat')):
        with open(os.path.join(directory, filename), encoding='utf-8',
                  errors='replace') as fh:
            body = fh.read()
        cats = {name for cid, name in ids.items() if cid in body}
        if not cats:
            continue
        claims.setdefault(team_name_from_filename(filename), set()).update(cats)
        for name in cats:
            breadth[name] += 1
    return claims, breadth


def team_name_from_filename(filename):
    """`2024 - Wrecka Krew.cat` -> `Wrecka Krew`."""
    return re.sub(r'^\d{4} - ', '', os.path.splitext(filename)[0])


def _key(name):
    """Comparable form: case, punctuation and curly apostrophes all removed."""
    folded = unicodedata.normalize('NFKD', name).replace('\u2019', "'").lower()
    return re.sub(r'[^a-z]', '', folded)


def resolve_factions(conn, directory=KILLTEAM_DIR):
    """{team name: 40,000 faction id} for every team the data can place.

    Two passes, both derived:

    1. The narrowest category a team claims that names a real 40,000 faction.
       Plurals are tolerated in one direction only — the category is `Ork` and
       the faction is `Orks` — because that is a spelling difference and not a
       different army.
    2. A team with no category of its own inherits from the same team in the
       other edition: `Kommando` (2021) from `Kommandos` (2024). Same team,
       two printings, and the match is on the name rather than on a guess.

    A team that neither pass can place is left alone, on its own faction row.
    It is reported rather than assigned, because the alternative is inventing
    an allegiance and being fluently wrong about which — the one change to
    this repo that would do real damage.
    """
    claims, breadth = faction_categories(directory)
    real = {_key(r['name']): r['id'] for r in conn.execute(
        "SELECT id, name FROM factions WHERE slug NOT LIKE 'kt-%'")}

    placed = {}
    for team, cats in claims.items():
        # Rarest first, then by name. The name is not decoration: `cats` is a
        # set, so breadth alone leaves ties to iteration order and two runs of
        # the same import could place a team differently.
        for cat in sorted(cats, key=lambda c: (breadth[c], c)):
            for candidate in (_key(cat), _key(cat) + 's'):
                if candidate in real:
                    placed[team] = real[candidate]
                    break
            if team in placed:
                break

    inherited = {}
    for filename in sorted(f for f in os.listdir(directory) if f.endswith('.cat')):
        team = team_name_from_filename(filename)
        if team in placed:
            continue
        for other, fid in placed.items():
            if _key(team) in (_key(other), _key(other).rstrip('s')) \
                    or _key(team) + 's' == _key(other):
                inherited[team] = fid
                break
    return {**placed, **inherited}


def import_all(conn, directory=KILLTEAM_DIR, dry_run=False):
    report = {'catalogues': 0, 'teams': 0, 'inserted': 0, 'updated': 0,
              'by_edition': defaultdict(int), 'empty': [], 'unreadable': [],
              'placed': 0, 'unplaced': []}
    seen_factions = set()
    placed = resolve_factions(conn, directory)

    for filename in sorted(f for f in os.listdir(directory) if f.endswith('.cat')):
        path = os.path.join(directory, filename)
        try:
            team, edition, found = parse_catalogue(path)
        except ET.ParseError as exc:
            # Never drop a line silently: a catalogue this cannot read is a
            # whole team missing from the picker, and Clay would only find out
            # holding the box.
            report['unreadable'].append((filename, str(exc)))
            db.record_unresolved(
                conn, IMPORTER, 'catalogue', filename,
                f'could not be parsed as XML: {exc}')
            continue

        report['catalogues'] += 1
        if not found:
            report['empty'].append(filename)
            db.record_unresolved(
                conn, IMPORTER, 'catalogue', filename,
                'parsed, but holds no operatives — nothing to import from it')
            continue

        # Reuse the 40,000 faction when the name matches, rather than making a
        # second row for it.
        #
        # This used to prefix every slug with `kt-` so the two could not
        # collide. The intent was right — "Orks" exists in both games and they
        # are not the same list — but it was aimed at the wrong column. What
        # keeps the unit lists apart is `datasheets.game_system`; a faction row
        # is the label Clay picks when tagging an army, a kit or a list, and
        # there he only ever meant one Orks. The prefix bought nothing and cost
        # a picker that offered the same name twice with no way to choose.
        #
        # A team with no 40,000 counterpart — Wrecka Krew, Battleclade — still
        # gets its own row, prefixed, since it is not a duplicate of anything.
        # The allegiance the catalogue itself claims, resolved above. This
        # is what the name match below could never reach: Kommandos are Orks
        # and say so in their category links, but nothing about the string
        # "Kommandos" matches the string "Orks".
        derived = placed.get(team)
        existing = conn.execute(
            "SELECT id, slug FROM factions WHERE name = ? AND slug NOT LIKE 'kt-%'",
            (team,)).fetchone()
        if derived:
            faction_id = derived
            slug = conn.execute('SELECT slug FROM factions WHERE id = ?',
                                (derived,)).fetchone()['slug']
            report['placed'] += 1
        elif existing:
            faction_id, slug = existing['id'], existing['slug']
        else:
            # Not placed and not named after a faction. It keeps a row of its
            # own and is reported, because assigning one from recall is the
            # change this repo forbids.
            slug = f'kt-{slugify(team)}'
            faction_id = db.upsert_faction(conn, team, slug)
            if team not in report['unplaced']:
                report['unplaced'].append(team)
        if slug not in seen_factions:
            seen_factions.add(slug)
            report['teams'] += 1

        for eid, name in found:
            # Scoped by edition and team, not the bare entry id. BSData reuses
            # ids across catalogues — 20 of them here, 4 spanning editions — so
            # a bare id lets Hunter Clade's Skitarii Ranger Gunner overwrite
            # Forge World (Legends)'s, and a team quietly loses an operative
            # that Clay only misses with the box in his hand. Edition, team and
            # entry id are all stable, so the key survives a re-sync.
            bsdata_id = f'kt:{edition}:{slug}:{eid}'
            existing = conn.execute(
                'SELECT id FROM datasheets WHERE bsdata_id = ?',
                (bsdata_id,)).fetchone()
            if existing:
                conn.execute(
                    'UPDATE datasheets SET name = ?, faction_id = ?, variant = ?, '
                    'game_system = ?, min_models = ?, max_models = ?, '
                    'source_note = ?, updated_at = ? WHERE bsdata_id = ?',
                    (name, faction_id, edition, GAME_SYSTEM, OPERATIVE_MODELS,
                     OPERATIVE_MODELS, filename, db.now(), bsdata_id))
                report['updated'] += 1
            else:
                conn.execute(
                    'INSERT INTO datasheets (bsdata_id, name, faction_id, '
                    'min_models, max_models, variant, game_system, source_note, '
                    'created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (bsdata_id, name, faction_id, OPERATIVE_MODELS,
                     OPERATIVE_MODELS, edition, GAME_SYSTEM, filename,
                     db.now(), db.now()))
                report['inserted'] += 1
            report['by_edition'][edition] += 1

    report['by_edition'] = dict(report['by_edition'])
    return report


def print_report(report, dry_run=False):
    r = report
    print()
    print('─' * 68)
    print(' Kill Team operatives' + ('  [DRY RUN]' if dry_run else ''))
    print('─' * 68)
    print(f"  catalogues read          {r['catalogues']:>6}")
    print(f"  kill teams               {r['teams']:>6}")
    print(f"  operatives inserted      {r['inserted']:>6}")
    print(f"  operatives updated       {r['updated']:>6}")
    for edition, count in sorted(r['by_edition'].items()):
        print(f"    ...{edition} edition      {count:>6}")
    if r['empty']:
        print(f"\n  {len(r['empty'])} catalogue(s) held no operatives "
              '(each is a row in unresolved_imports):')
        for name in r['empty']:
            print(f'     {name}')
    if r['unreadable']:
        print(f"\n  {len(r['unreadable'])} catalogue(s) could not be parsed:")
        for name, err in r['unreadable']:
            print(f'     {name}\n       {err}')
    print(f"\n  teams placed on a 40,000 faction  {r['placed']:>6}")
    if r['unplaced']:
        # Named, never guessed. A team here is one whose catalogue claims no
        # allegiance the data can resolve — the 2021 printings mostly, which
        # carry no category entries at all. Assigning them from recall is the
        # one change to this repo that would do real damage, so they are
        # listed for Clay to place by hand if he owns one.
        print(f"  teams the data could not place    {len(r['unplaced']):>6}")
        print('\n  Not placed — these keep a faction row of their own:')
        for name in r['unplaced']:
            print(f'     {name}')
    if not r['empty'] and not r['unreadable']:
        print('\n  Every catalogue read cleanly.')
    print('─' * 68)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Import Kill Team operatives from the BSData catalogues.')
    ap.add_argument('--dry-run', action='store_true',
                    help='roll back all writes; print the report only')
    ap.add_argument('--db', help='override the database path')
    args = ap.parse_args(argv)

    if args.db:
        db.DB_PATH = args.db
    if not os.path.isdir(KILLTEAM_DIR) or not any(
            f.endswith('.cat') for f in os.listdir(KILLTEAM_DIR)):
        print('No Kill Team catalogues found. Run: '
              'python3 scripts/fetch_killteam.py', file=sys.stderr)
        return 1

    db.init_db()
    conn = db.connect()
    try:
        db.clear_unresolved(conn, IMPORTER)
        report = import_all(conn, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    print_report(report, dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
