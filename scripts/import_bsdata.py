#!/usr/bin/env python3
"""Import datasheets from BSData and points from the Munitorum Field Manual.

    python3 scripts/import_bsdata.py            # import, then print the report
    python3 scripts/import_bsdata.py --dry-run  # report only, write nothing

Two sources, because they are good at different things
------------------------------------------------------
The spec assumed one source (BSData) and warned that flattening its points was
the tricky part: base ``costs`` holds the minimum-size price and larger sizes
are ``modifiers`` that conditionally overwrite it, so the importer would have to
evaluate set/increment against greaterThan/atLeast/equalTo conditions and hope
nothing exotic turned up.

It also said to spend ten minutes on ``BSData/wh40k-11e-mfm`` first. That repo
is the better source and it deletes that entire workstream:

  * It is parsed from GW's official Munitorum Field Manual, not reverse
    engineered — so it is the same numbers Clay's opponent is using.
  * Points arrive **already flattened**: ``{models: 10, points: 75}`` per legal
    unit size. No modifier evaluation, no "exotic case" bucket.
  * MIT licensed, ~600 KB, validated against zod schemas in CI, re-scraped
    daily with a changelog. BSData ``wh40k-11e`` has no licence at all.
  * It carries legal unit sizes correctly. BSData expresses a Boyz mob as
    "1 Boss Nob + 9-19 Boyz"; deriving 10/20 from that means reassembling the
    unit from its parts. MFM just says 10 and 20.

What BSData still owns is the *rules-side identity* MFM has no concept of:
``bsdata_id`` (the stable re-sync key), keywords for the effort heuristic, and
the datasheet list itself. So:

    datasheets  <- BSData      (identity, faction, keywords, effort)
    points      <- MFM         (flat tables, official, per faction)

Joining them
------------
On normalised name, **scoped by faction**. Faction scoping is not optional: 35
unit names carry genuinely different points per faction — a Repulsor
Executioner is 255 points for Black Templars and 230 for Blood Angels — so a
global name join would silently write wrong points for a tenth of the roster.

Measured on the pinned data: 1,459 of 1,466 current units match (99.5%). The
seven that don't are naming variants (MFM "Vyper" vs BSData "Vypers", MFM's one
"Soul Grinder" vs BSData's four god-specific ones). They are reported as
unresolved rows with the near-misses listed, never guessed at. Legends units
are skipped on both sides — they are deprecated and out of scope.
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

import database as db

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BSDATA_DIR = os.path.join(BASE_DIR, 'data', 'bsdata')
MFM_DIR = os.path.join(BASE_DIR, 'data', 'mfm')

IMPORTER = 'bsdata'


# ── Name normalisation ───────────────────────────────────

def norm(s):
    """Fold a unit name to its join key.

    Curly apostrophes, accents and punctuation differ between the two sources
    ("Grot Tanks" vs "Grot Tanks", "Ork Nob" vs "Ork  Nob"), and none of those
    differences mean anything. Case, punctuation and whitespace all collapse.
    """
    s = unicodedata.normalize('NFKD', s or '')
    s = s.replace('’', "'").replace('‘', "'")
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()


def slugify(s):
    return norm(s).replace(' ', '-')


# ── Catalogue -> faction mapping ─────────────────────────
#
# Most BSData catalogue filenames slugify straight onto an MFM faction file
# ("Imperium - Space Marines.json" -> space-marines). These are the ones that
# don't. Every entry is a naming difference between the two projects, checked
# by hand against both file lists — none of it is invented product knowledge.
#
# The second element is MFM's `groupTitle`: the sub-heading a unit is listed
# under inside a faction's manual page. GW gives each first-founding Chapter
# its own BSData catalogue but prices them inside the Space Marines manual
# entry, and an Ultramarines Repulsor is not always a Space Marines Repulsor —
# so the group hint is what keeps those joins honest.
CATALOGUE_ALIASES = {
    'Aeldari - Craftworlds':            ('aeldari', None),
    "Chaos - Emperor's Children":       ('emperors-children', None),
    'Chaos - Titanicus Traitoris':      ('chaos-titan-legions', None),
    'Imperium - Adeptus Titanicus':     ('titan-legions', None),
    'Imperium - Agents of the Imperium': ('imperial-agents', None),
    'Library - Titans':                 ('titan-legions', None),
    "T'au Empire":                      ('tau-empire', None),
    # First-founding Chapters: own catalogue, priced under Space Marines.
    'Imperium - Imperial Fists':        ('space-marines', 'Imperial Fists'),
    'Imperium - Iron Hands':            ('space-marines', 'Iron Hands'),
    'Imperium - Raven Guard':           ('space-marines', 'Raven Guard'),
    'Imperium - Salamanders':           ('space-marines', 'Salamanders'),
    'Imperium - Ultramarines':          ('space-marines', 'Ultramarines'),
    'Imperium - White Scars':           ('space-marines', 'White Scars'),
}

# Catalogues with no MFM counterpart, deliberately. Skipped without a warning
# because their absence is expected, not a data problem.
CATALOGUE_SKIP = {
    'Warhammer 40,000',                 # game-system root, holds no datasheets
    'Unaligned Forces',                 # terrain and fortifications
    'Library - Astartes Heresy Legends',  # Legends only
}

# BSData tags an entry with several "Faction: X" links at once (a Blood Angels
# unit is both 'Adeptus Astartes' and 'Blood Angels'). These map BSData's label
# onto MFM's faction file so library catalogues holding more than one faction —
# Aeldari Library carries Asuryani, Drukhari, Ynnari and Harlequins — split
# correctly instead of all landing under the file's own name.
FACTION_LINK_ALIASES = {
    'Asuryani': 'aeldari',
    'Ynnari': 'aeldari',
    'Harlequins': 'aeldari',
    'Legiones Daemonica': 'chaos-daemons',
    'Plague Legions': 'death-guard',
    'Legions of Excess': 'emperors-children',
    'Scintillating Legions': 'thousand-sons',
    'Blood Legions': 'world-eaters',
    'Heretic Astartes': 'chaos-space-marines',
    'Adeptus Astartes': 'space-marines',
    'Agents of the Imperium': 'imperial-agents',
    'Imperial Fists': 'space-marines',
    'Iron Hands': 'space-marines',
    'Raven Guard': 'space-marines',
    'Salamanders': 'space-marines',
    'Ultramarines': 'space-marines',
    'White Scars': 'space-marines',
}


def mfm_faction_slugs():
    return {fn[:-5] for fn in os.listdir(MFM_DIR)
            if fn.endswith('.yaml') and fn != 'meta.yaml'}


def catalogue_faction(basename, known_slugs):
    """Resolve a catalogue filename to (faction_slug, group_title | None)."""
    if basename in CATALOGUE_SKIP:
        return None, None
    if basename in CATALOGUE_ALIASES:
        return CATALOGUE_ALIASES[basename]
    tail = basename.split(' - ')[-1]
    for candidate in (slugify(basename), slugify(tail),
                      slugify(tail.replace(' Library', '')),
                      slugify(basename.replace(' Library', '').split(' - ')[-1])):
        if candidate in known_slugs:
            return candidate, None
    return None, None


def entry_faction(entry, catalogue_slug, known_slugs):
    """Faction for one datasheet: its own tags first, its catalogue second.

    Preferring the tag is what splits the shared library catalogues. Preferring
    the *catalogue's* slug when it appears among the tags is what stops a Blood
    Angels unit being filed under plain Space Marines just because it also
    carries the Adeptus Astartes keyword.
    """
    tagged = []
    for link in entry.get('categoryLinks') or []:
        name = link.get('name') or ''
        if not name.startswith('Faction: '):
            continue
        label = name[len('Faction: '):]
        slug = FACTION_LINK_ALIASES.get(label) or slugify(label)
        if slug in known_slugs:
            tagged.append(slug)
    if catalogue_slug and catalogue_slug in tagged:
        return catalogue_slug
    if len(set(tagged)) == 1:
        return tagged[0]
    return catalogue_slug


# ── Effort heuristic ─────────────────────────────────────
#
# A Knight Questoris and a Termagant are both "1 model", which makes model-count
# percentages useless as progress. Effort is per model on the datasheet, and
# every bar in the app is effort-weighted.
#
# Seeded from BSData keywords on the spec's scale (infantry 1, elite 2,
# bikes/small vehicles 4, monsters/large vehicles 8, superheavies 10). It is a
# starting point, not a claim — Clay overrides per datasheet, and an override
# sets effort_is_override so re-running this never clobbers it.
EFFORT_RULES = [
    (10, ('Titanic', 'Towering')),          # Knights, Titans
    (8,  ('Monster', 'Vehicle', 'Aircraft', 'Walker', 'Fortification')),
    (4,  ('Mounted', 'Beast')),             # bikes, cavalry, squig riders
    (2,  ('Terminator', 'Epic Hero', 'Character')),
]
DEFAULT_EFFORT = 1


def seed_effort(keywords):
    for score, triggers in EFFORT_RULES:
        if any(t in keywords for t in triggers):
            return score
    return DEFAULT_EFFORT


def entry_keywords(entry):
    return {(link.get('name') or '') for link in entry.get('categoryLinks') or []
            if not (link.get('name') or '').startswith('Faction: ')}


VARIANT_RE = re.compile(r'\s*\[([^\]]+)\]\s*$')


def split_variant(name):
    """"Vypers [Legends]" -> ("Vypers", "legends"); plain names -> (name, None)."""
    m = VARIANT_RE.search(name or '')
    if not m:
        return (name or '').strip(), None
    return VARIANT_RE.sub('', name).strip(), m.group(1).strip().lower()


def is_datasheet(entry):
    """Tell a real datasheet from a model *inside* one.

    BSData's ``type: "model"`` covers both: "Warboss" is a datasheet, but so is
    "Burna Boy" — one of the models you pick inside a Burna Boyz mob. Importing
    the latter would put "Burna Boy" in the picker right next to "Burna Boyz",
    which is precisely the kind of near-identical wrong choice that quietly
    corrupts a collection.

    Only ``type: "model"`` is ambiguous — a ``type: "unit"`` entry is always a
    datasheet. Within the models, a real datasheet is the thing you pay points
    for and the thing that belongs to a faction; a sub-model has neither, its
    price and keywords being the parent unit's.

    The check deliberately does not require a cost of its own for units:
    BSData leaves ``costs`` empty on a handful of real datasheets (Firestrike
    Servo-Turrets, Convergence of Dominion), which is one more reason points
    come from the Munitorum manual instead.
    """
    if entry.get('type') == 'unit':
        return True
    if any(c.get('name') == 'pts' for c in entry.get('costs') or []):
        return True
    return any((link.get('name') or '').startswith('Faction: ')
               for link in entry.get('categoryLinks') or [])


# ── Loading ──────────────────────────────────────────────

def load_bsdata():
    """Yield every unit/model datasheet entry across all catalogues."""
    known = mfm_faction_slugs()
    unmapped = []
    for fn in sorted(os.listdir(BSDATA_DIR)):
        if not fn.endswith('.json'):
            continue
        basename = fn[:-5]
        with open(os.path.join(BSDATA_DIR, fn), encoding='utf-8') as fh:
            catalogue = json.load(fh)
        catalogue = catalogue.get('catalogue', catalogue)
        slug, group = catalogue_faction(basename, known)
        entries = [e for e in catalogue.get('sharedSelectionEntries') or []
                   if e.get('type') in ('unit', 'model') and is_datasheet(e)]
        if slug is None and basename not in CATALOGUE_SKIP and entries:
            unmapped.append((basename, len(entries)))
            continue
        if slug is None:
            continue
        for entry in entries:
            yield {
                'bsdata_id': entry.get('id'),
                'name': entry.get('name') or '',
                'catalogue': basename,
                'faction_slug': entry_faction(entry, slug, known),
                'group_title': group,
                'keywords': entry_keywords(entry),
            }
    load_bsdata.unmapped = unmapped


def load_mfm():
    """Index the manual two ways.

    ``index`` is the exact ``(faction, group, name)`` lookup; ``by_faction``
    collapses the group so a name can be found anywhere inside its faction.
    """
    index = {}
    by_faction = defaultdict(list)
    meta_path = os.path.join(MFM_DIR, 'meta.yaml')
    with open(meta_path, encoding='utf-8') as fh:
        meta = yaml.safe_load(fh)
    for fn in sorted(os.listdir(MFM_DIR)):
        if not fn.endswith('.yaml') or fn == 'meta.yaml':
            continue
        slug = fn[:-5]
        with open(os.path.join(MFM_DIR, fn), encoding='utf-8') as fh:
            doc = yaml.safe_load(fh)
        for unit in doc.get('units') or []:
            if unit.get('legends'):
                continue          # deprecated; BSData has no live counterpart
            group = unit.get('groupTitle')
            index[(slug, group, norm(unit['name']))] = unit
            by_faction[(slug, norm(unit['name']))].append((group, unit))
    return meta, index, by_faction


def index_datasheets(rows):
    """Two lookups over the imported datasheets, keyed on the normalised name.

    ``by_faction`` answers "does this faction have its own datasheet for X",
    ``by_name`` answers "does anyone". A Chapter that inherits a unit from its
    parent faction only shows up in the second.
    """
    by_faction, by_name = defaultdict(list), defaultdict(list)
    for ds_id, name, entry, variant in rows:
        if variant:
            continue          # variants are never a points target
        key = norm(name)
        if entry['faction_slug']:
            by_faction[(entry['faction_slug'], key)].append(ds_id)
        by_name[key].append((entry['faction_slug'], ds_id))
    return by_faction, by_name


def resolve_datasheet(by_faction, by_name, known_slugs, faction_slug, group, name):
    """Map one manual entry onto a datasheet. Returns (ds_id, points_faction, why).

    ``points_faction`` is the faction to tag the resulting points rows with:
    None when the datasheet belongs to this faction already, and the listing
    faction's slug when a Chapter is inheriting someone else's datasheet at its
    own price. That distinction is the whole reason datasheet_points carries a
    faction — there is one Repulsor Executioner, priced 255 for Black Templars
    and 230 for Blood Angels, and both numbers are correct.

    When the faction has no datasheet of its own, the group title names the
    parent to inherit from: a Rhino listed under black-templars / "Space
    Marines" is the Space Marines Rhino, not the identically-named Grey Knights
    one. Falling back to a bare name search across every faction is only safe
    when exactly one datasheet answers to it.
    """
    key = norm(name)
    own = by_faction.get((faction_slug, key)) or []
    if len(own) == 1:
        return own[0], None, 'own datasheet'
    if len(own) > 1:
        return None, None, f'{len(own)} datasheets share this name within the faction'

    parent = slugify(group or '')
    if parent in known_slugs and parent != faction_slug:
        hinted = by_faction.get((parent, key)) or []
        if len(hinted) == 1:
            return hinted[0], faction_slug, f'inherited from "{parent}" via group title'

    shared = by_name.get(key) or []
    if len(shared) == 1:
        parent_slug, ds_id = shared[0]
        return ds_id, faction_slug, f'inherited from "{parent_slug}"'
    if len(shared) > 1:
        owners = sorted({s or "?" for s, _i in shared})
        return None, None, ('ambiguous: datasheets with this name exist in '
                            + ', '.join(owners))
    return None, None, 'no BSData datasheet with this name'


# ── Import ───────────────────────────────────────────────

def import_all(conn, dry_run=False):
    report = {
        'factions': 0,
        'datasheets_inserted': 0,
        'datasheets_updated': 0,
        'datasheets_variant': defaultdict(int),
        'points_rows': 0,
        'points_inherited': 0,
        'points_skipped_addons': 0,
        'effort_preserved': 0,
        'points_preserved': 0,
        'unresolved': [],
        'unmapped_catalogues': [],
    }

    meta, mfm_index, mfm_by_faction = load_mfm()
    source_note = f"MFM v{meta.get('version')} ({meta.get('lastUpdated')})"
    effective_from = str(meta.get('lastUpdated'))

    # 1 · Factions, derived from the manual's own canonical list.
    faction_ids = {}
    for slug in meta.get('factions') or []:
        path = os.path.join(MFM_DIR, f'{slug}.yaml')
        if not os.path.exists(path):
            db.record_unresolved(conn, IMPORTER, 'faction', slug,
                                 'listed in meta.yaml but no faction file present')
            continue
        with open(path, encoding='utf-8') as fh:
            name = (yaml.safe_load(fh) or {}).get('name') or slug
        faction_ids[slug] = db.upsert_faction(conn, name, slug)
        report['factions'] += 1

    # 2 · Datasheets from BSData.
    entries = list(load_bsdata())
    report['unmapped_catalogues'] = getattr(load_bsdata, 'unmapped', [])
    for basename, count in report['unmapped_catalogues']:
        db.record_unresolved(
            conn, IMPORTER, 'faction', basename,
            f'catalogue maps to no MFM faction; {count} datasheets skipped',
            source_ref=basename)

    seen_ids = set()
    datasheet_rows = {}
    for e in entries:
        bsid = e['bsdata_id']
        if not bsid or bsid in seen_ids:
            continue
        seen_ids.add(bsid)
        name, variant = split_variant(e['name'])
        if variant:
            report['datasheets_variant'][variant] += 1
        faction_id = faction_ids.get(e['faction_slug'])
        effort = seed_effort(e['keywords'])
        # Stored rather than discarded: the effort heuristic already depends on
        # them, and nothing could ask "is this a Vehicle" after the import.
        # Note they are NOT a basing signal — a Rhino and a Dreadnought carry
        # the same ones and only one has a base. See migration 004.
        keywords_json = json.dumps(sorted(e['keywords']))

        existing = conn.execute(
            'SELECT id, effort_is_override FROM datasheets WHERE bsdata_id = ?',
            (bsid,)).fetchone()
        if existing:
            # Never clobber a hand-tuned effort score on re-sync.
            if existing['effort_is_override']:
                report['effort_preserved'] += 1
                conn.execute(
                    'UPDATE datasheets SET name = ?, faction_id = ?, '
                    'variant = ?, keywords = ?, source_note = ?, updated_at = ? '
                    'WHERE id = ?',
                    (name, faction_id, variant, keywords_json, e['catalogue'],
                     db.now(), existing['id']))
            else:
                conn.execute(
                    'UPDATE datasheets SET name = ?, faction_id = ?, effort = ?, '
                    'variant = ?, keywords = ?, source_note = ?, updated_at = ? '
                    'WHERE id = ?',
                    (name, faction_id, effort, variant, keywords_json,
                     e['catalogue'], db.now(), existing['id']))
            ds_id = existing['id']
            report['datasheets_updated'] += 1
        else:
            cur = conn.execute(
                'INSERT INTO datasheets (bsdata_id, name, faction_id, effort, '
                'variant, keywords, source_note, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (bsid, name, faction_id, effort, variant, keywords_json,
                 e['catalogue'], db.now(), db.now()))
            ds_id = cur.lastrowid
            report['datasheets_inserted'] += 1
        datasheet_rows[bsid] = (ds_id, name, e, variant)

    # 3 · Points, driven from the manual rather than from the datasheet list.
    #
    #     Iterating the manual is what makes both failure directions visible: a
    #     manual entry with no datasheet is a unit Clay could be told to buy
    #     that the app cannot represent, and a datasheet with no points is a
    #     model he owns that no list can cost. Both need to be seen.
    by_faction, by_name = index_datasheets(datasheet_rows.values())
    known_slugs = mfm_faction_slugs()
    priced_datasheets = set()
    cleared = set()

    for (slug, group, _key), unit in sorted(
            mfm_index.items(), key=lambda kv: (kv[0][0], kv[0][1] or '', kv[0][2])):
        ds_id, points_faction, why = resolve_datasheet(
            by_faction, by_name, known_slugs, slug, group, unit['name'])
        if ds_id is None:
            db.record_unresolved(
                conn, IMPORTER, 'datasheet', unit['name'],
                f'priced in the manual under "{slug}"'
                + (f' / {group}' if group else '') + f' — {why}',
                source_ref=f'mfm:{slug}',
                payload={'faction': slug, 'group_title': group})
            continue

        faction_id = faction_ids.get(points_faction) if points_faction else None
        priced_datasheets.add(ds_id)
        if points_faction:
            report['points_inherited'] += 1

        # Legal unit sizes come from the manual's cost rows: BSData describes a
        # Boyz mob as "1 Boss Nob + 9-19 Boyz", which is the same unit but not
        # the same numbers. Add-on rows ("+ 1 Invader ATV") are priced on top of
        # a base option, so they are not unit sizes and must not set the bounds.
        # Only a faction's own listing may set them — an inherited one is the
        # same unit and would just restate it.
        if points_faction is None:
            counts = [c['models'] for tier in unit['pricing'] for c in tier['costs']
                      if not c.get('addon')]
            if counts:
                conn.execute(
                    'UPDATE datasheets SET min_models = ?, max_models = ? '
                    'WHERE id = ?', (min(counts), max(counts), ds_id))

        protected = {
            (r['model_count'], r['tier_min'], r['composition'])
            for r in conn.execute(
                'SELECT model_count, tier_min, composition FROM datasheet_points '
                'WHERE datasheet_id = ? AND manual_override = 1 '
                'AND faction_id IS ?', (ds_id, faction_id))}

        # Replace this (datasheet, faction) pair's generated rows wholesale, so
        # a points cut removes the stale row instead of leaving both prices in
        # the table. Once per pair, not once per manual entry.
        if (ds_id, faction_id) not in cleared:
            conn.execute(
                'DELETE FROM datasheet_points WHERE datasheet_id = ? '
                'AND faction_id IS ? AND manual_override = 0', (ds_id, faction_id))
            cleared.add((ds_id, faction_id))

        for tier in unit['pricing']:
            tier_min, tier_max = parse_tier_range(tier['range'])
            for cost in tier['costs']:
                if cost.get('addon'):
                    report['points_skipped_addons'] += 1
                    continue
                if (cost['models'], tier_min, cost.get('desc')) in protected:
                    report['points_preserved'] += 1
                    continue
                conn.execute(
                    'INSERT OR REPLACE INTO datasheet_points '
                    '(datasheet_id, faction_id, model_count, points, tier_min, '
                    ' tier_max, tier_label, composition, effective_from, '
                    ' source_note, manual_override) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)',
                    (ds_id, faction_id, cost['models'], cost['points'], tier_min,
                     tier_max, tier.get('label'), cost.get('desc'), effective_from,
                     source_note))
                report['points_rows'] += 1

    # 4 · Current datasheets the manual never priced. Clay can own these models
    #     and see them in his collection, but no list can cost them.
    for ds_id, name, entry, variant in datasheet_rows.values():
        if variant or ds_id in priced_datasheets:
            continue
        if not entry['faction_slug']:
            continue          # already reported as an unmapped catalogue
        db.record_unresolved(
            conn, IMPORTER, 'points', name,
            f'no Munitorum Field Manual entry in faction "{entry["faction_slug"]}"',
            source_ref=entry['catalogue'],
            payload={'bsdata_id': entry['bsdata_id'],
                     'faction': entry['faction_slug']})


    report['unresolved'] = [dict(r) for r in db.open_unresolved(conn, IMPORTER)]
    return report


def parse_tier_range(text):
    """"[1,2]" -> (1, 2);  "[3,)" -> (3, None).

    Requisition Thresholds: the same unit costs more as your 3rd+ copy. v1 reads
    the tier containing 1 and ignores the rest, but the rows are stored because
    dropping them at import time would be unrecoverable.
    """
    m = re.match(r'^\[(\d+),(?:(\d+)\]|\))$', (text or '').strip())
    if not m:
        return 1, None
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


# ── Report ───────────────────────────────────────────────

def print_report(report, dry_run=False):
    r = report
    print()
    print('─' * 68)
    print(' BSData + Munitorum Field Manual import' + ('  [DRY RUN]' if dry_run else ''))
    print('─' * 68)
    print(f"  factions seeded          {r['factions']:>6}")
    print(f"  datasheets inserted      {r['datasheets_inserted']:>6}")
    print(f"  datasheets updated       {r['datasheets_updated']:>6}")
    for variant, count in sorted(r['datasheets_variant'].items()):
        print(f"    ...[{variant}] variants  {count:>6}  "
              '(kept and flagged; the manual does not price these)')
    print(f"  points rows created      {r['points_rows']:>6}")
    if r['points_inherited']:
        print(f"    ...inherited listings  {r['points_inherited']:>6}  "
              "(a Chapter's own price for a datasheet it shares)")
    if r['points_skipped_addons']:
        print(f"  add-on cost rows skipped {r['points_skipped_addons']:>6}  "
              '(priced on top of a unit, not a unit size)')
    if r['effort_preserved']:
        print(f"  effort overrides kept    {r['effort_preserved']:>6}")
    if r['points_preserved']:
        print(f"  points overrides kept    {r['points_preserved']:>6}")

    groups = defaultdict(list)
    for row in r['unresolved']:
        groups[row['kind']].append(row)

    print()
    if not r['unresolved']:
        print('  Nothing unresolved.')
    else:
        print(f"  UNRESOLVED — {len(r['unresolved'])} entries needing a human")
        print('  (nothing below was guessed at or dropped; each is a row in '
              'unresolved_imports)')
        for kind in sorted(groups):
            rows = groups[kind]
            print(f'\n  ── {kind} ({len(rows)}) ' + '─' * (48 - len(kind)))
            for row in rows:
                print(f"     {row['raw_name']}")
                print(f"       {row['detail']}")
    print('─' * 68)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Import BSData datasheets and Munitorum Field Manual points.')
    ap.add_argument('--dry-run', action='store_true',
                    help='roll back all writes; print the report only')
    ap.add_argument('--db', help='override the database path')
    ap.add_argument('--json-report', metavar='PATH',
                    help='also write the full report as JSON')
    args = ap.parse_args(argv)

    if args.db:
        db.DB_PATH = args.db
    if not os.path.isdir(BSDATA_DIR) or not any(
            f.endswith('.json') for f in os.listdir(BSDATA_DIR)):
        print('No BSData catalogues found. Run: python3 scripts/fetch_bsdata.py',
              file=sys.stderr)
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
    if args.json_report:
        with open(args.json_report, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f'JSON report -> {args.json_report}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
