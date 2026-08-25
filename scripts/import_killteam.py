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

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from import_bsdata import slugify

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KILLTEAM_DIR = os.path.join(BASE_DIR, 'data', 'killteam')

#: Clay's reviewed team -> faction table. See the file's own header for why it
#: exists and why it is not derived. Absent is not an error: the catalogues
#: still place what they can without it.
REVIEWED_PATH = os.path.join(BASE_DIR, 'seed', 'data', 'killteam_factions.yaml')

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


def _singular(key):
    """`kommandos` -> `kommando`, `legionaries` -> `legionary`.

    The two printings of a team differ by a plural far more often than by
    anything else: BSData names the 2021 catalogues in the singular and the
    2024 ones in the plural, so Novitiate/Novitiates and Legionary/Legionaries
    are the same team twice. Folding both to one form is a spelling rule, not
    a judgement about which teams are related \u2014 the names still have to agree
    on every other letter.
    """
    if key.endswith('ies'):
        return key[:-3] + 'y'
    return key[:-1] if key.endswith('s') else key


def catalogue_names(directory):
    """{singular key: {every catalogue name sharing it}}.

    The 2021 and 2024 printings of a team are two catalogues and one team, so
    grouping them here is what lets a placement found on either reach both.
    """
    out = {}
    for filename in sorted(f for f in os.listdir(directory) if f.endswith('.cat')):
        team = team_name_from_filename(filename)
        out.setdefault(_singular(_key(team)), set()).add(team)
    return out


def real_factions(conn):
    """{comparable name: row} for the 40,000 faction rows, never the kt- ones.

    One definition, used by both the category rule and the name match. They
    disagreed before: the name match compared raw strings, so the compendium
    team `T'au Empire` missed the faction row `T’au Empire` on the apostrophe
    alone and got a `kt-t-au-empire` row of its own — 24 operatives on a
    duplicate the picker showed twice and no T'au filter reached.
    """
    return {_key(r['name']): r for r in conn.execute(
        "SELECT id, name, slug FROM factions WHERE slug NOT LIKE 'kt-%'")}


def match_faction(real, name):
    """The faction row `name` refers to, tolerating punctuation and a plural.

    `Space Marine` is the compendium team and `Space Marines` the faction;
    `T'au Empire` and `T’au Empire` are the same words. Both are spelling
    differences, and neither makes it a different army.
    """
    key = _key(name)
    return (real.get(key) or real.get(key + 's')
            or real.get(_singular(key)) or None)


def load_reviewed(path=REVIEWED_PATH):
    """Clay's reviewed team -> faction table, or None when it is not there.

    Refuses a table with no provenance, exactly as the Combat Patrol seed
    does: the whole reason this file may be trusted is that a person reviewed
    it and said so. An unattributed table is indistinguishable from one a
    model wrote from memory, which is the thing this repo will not import.
    """
    # `None` is not the same as missing by accident: it is how a caller says
    # "derive only", which is what the tests of the category rule need in
    # order to measure that rule rather than the table sitting beside it.
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as fh:
        data = yaml.safe_load(fh) or {}
    source = data.get('source') or {}
    if not source.get('reviewed_by') or not source.get('retrieved_on'):
        raise ValueError(
            f'{path}: refusing to import a faction table with no provenance — '
            '`source.reviewed_by` and `source.retrieved_on` are both required.')
    return data


def reviewed_placements(reviewed, by_key, real, report=None):
    """{catalogue team name: faction id} for the entries that resolve.

    An entry resolves when its faction names a real 40,000 faction row *and*
    its team names a catalogue. Neither is assumed: a faction name with no row
    and a team name with no catalogue are both collected for the report rather
    than approximated, because a team quietly assigned the wrong army is worse
    than one visibly unassigned.
    """
    placed, no_faction, no_catalogue = {}, [], []
    for entry in (reviewed or {}).get('teams') or []:
        name, faction = entry.get('name'), entry.get('faction')
        if not name or not faction:
            continue
        row = match_faction(real, faction)
        if row is None:
            no_faction.append((name, faction))
            continue
        fid = row['id']
        # `catalogue` only appears when the names differ by more than a
        # plural, which _singular already handles on its own.
        lookup = entry.get('catalogue') or name
        matches = by_key.get(_singular(_key(lookup)))
        if not matches:
            no_catalogue.append(name)
            continue
        for match in matches:
            placed[match] = fid
    if report is not None:
        report['reviewed_no_faction'] = no_faction
        report['reviewed_no_catalogue'] = no_catalogue
    return placed


def resolve_factions(conn, directory=KILLTEAM_DIR, reviewed_path=REVIEWED_PATH,
                     report=None):
    """{team name: 40,000 faction id} for every team that can be placed.

    Three layers, in increasing order of authority:

    1. **Derived.** The narrowest category a team claims that names a real
       40,000 faction. Plurals are tolerated in one direction only — the
       category is `Ork` and the faction is `Orks` — because that is a
       spelling difference and not a different army.
    2. **Reviewed.** Clay's table (`seed/data/killteam_factions.yaml`). It
       wins, and where it disagrees with the derivation the disagreement is
       reported rather than swallowed. Both current disagreements are the
       table correcting the inference: Hand of the Archon are Drukhari, not
       Aeldari, and Brood Brothers are Genestealer Cults, not Tyranids.
    3. **Every printing.** A placement found on one catalogue reaches the
       other printing of the same team: `Kommando` (2021) from `Kommandos`
       (2024). The 2021 catalogues carry no categories at all, so without
       this they could never be placed by derivation.

    A team no layer can place is left alone, on its own faction row, and
    named in the report. Assigning one from recall is the one change to this
    repo that would do real damage.
    """
    claims, breadth = faction_categories(directory)
    real = real_factions(conn)
    by_key = catalogue_names(directory)

    derived = {}
    for team, cats in claims.items():
        # Rarest first, then by name. The name is not decoration: `cats` is a
        # set, so breadth alone leaves ties to iteration order and two runs of
        # the same import could place a team differently.
        for cat in sorted(cats, key=lambda c: (breadth[c], c)):
            row = match_faction(real, cat)
            if row is not None:
                derived[team] = row['id']
                break

    reviewed = reviewed_placements(
        load_reviewed(reviewed_path), by_key, real, report)

    if report is not None:
        names = {r['id']: r['name'] for r in conn.execute(
            'SELECT id, name FROM factions')}
        report['disagreed'] = sorted(
            (team, names.get(derived[team]), names.get(fid))
            for team, fid in reviewed.items()
            if team in derived and derived[team] != fid)

    # Reviewed applied second so it overwrites the whole twin group, not just
    # the printing it happened to name.
    placed = {}
    for layer in (derived, reviewed):
        for team, fid in layer.items():
            for name in by_key.get(_singular(_key(team)), {team}):
                placed[name] = fid
    return placed


def import_all(conn, directory=KILLTEAM_DIR, dry_run=False,
               reviewed_path=REVIEWED_PATH):
    report = {'catalogues': 0, 'teams': 0, 'inserted': 0, 'updated': 0,
              'by_edition': defaultdict(int), 'empty': [], 'unreadable': [],
              'placed': 0, 'unplaced': [], 'disagreed': [],
              'reviewed_no_faction': [], 'reviewed_no_catalogue': []}
    seen_factions = set()
    real = real_factions(conn)
    placed = resolve_factions(conn, directory, reviewed_path, report=report)

    # A line the reviewed table names and this cannot use is a team Clay
    # thinks is filed and is not. Recorded where the other import failures
    # go, so it survives the scrollback.
    for name, faction in report['reviewed_no_faction']:
        db.record_unresolved(
            conn, IMPORTER, 'team', name,
            f'reviewed table says faction "{faction}", which names no row in '
            '`factions` — left on its own row rather than approximated')
    for name in report['reviewed_no_catalogue']:
        db.record_unresolved(
            conn, IMPORTER, 'team', name,
            'named in the reviewed table, but no catalogue matches it')

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
        existing = match_faction(real, team)
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
    if r.get('disagreed'):
        # Never swallowed. The reviewed table wins, but a place where a person
        # and the category rule reach different answers is the most
        # interesting line in this report: one of the two is wrong, and which
        # is worth knowing before the next team is added.
        print(f"\n  {len(r['disagreed'])} team(s) where the reviewed table "
              'overrode what the catalogue derived:')
        for team, was, now in r['disagreed']:
            print(f'     {team}: derived {was} -> reviewed {now}')
    if r.get('reviewed_no_faction'):
        print(f"\n  {len(r['reviewed_no_faction'])} reviewed entr(y/ies) name "
              'a faction with no row (each is a row in unresolved_imports):')
        for name, faction in r['reviewed_no_faction']:
            print(f'     {name}  ->  "{faction}"')
    if r.get('reviewed_no_catalogue'):
        print(f"\n  {len(r['reviewed_no_catalogue'])} reviewed entr(y/ies) "
              'match no catalogue:')
        for name in r['reviewed_no_catalogue']:
            print(f'     {name}')
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
