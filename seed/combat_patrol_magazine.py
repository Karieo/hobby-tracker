#!/usr/bin/env python3
"""Seed the Combat Patrol partwork magazine as kit templates.

    python3 seed/combat_patrol_magazine.py --status
    python3 seed/combat_patrol_magazine.py --dry-run
    python3 seed/combat_patrol_magazine.py --owned-through 75

Ninety issues is ninety manual entries, and the partwork contents are publicly
documented, so this pre-loads them instead. What it will not do is make any of
it up: every unit name is matched against an imported BSData datasheet, and
anything that does not match is reported and written to ``unresolved_imports``
rather than guessed at or silently dropped. A missing issue costs two minutes
at the review screen; a wrong one corrupts ownership and purchase advice for
months, with nothing to prompt anyone to check it.

The contents themselves live in ``seed/data/combat_patrol_issues.yaml``, which
ships empty — see the README beside it for why, and for how to fill it in.

Magazine sprues have no product barcode (the barcode on a cover identifies the
issue, not the kit), so these templates are reached by name rather than by
scanning. That is exactly why they are worth seeding.
"""

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'scripts'))

import yaml

import collection as col
import database as db
import scanning as scan
# The same fold the rules-data importer uses. One definition of "same name",
# so a unit that matched there matches here.
from import_bsdata import norm as normalise_name

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(BASE_DIR, 'seed', 'data', 'combat_patrol_issues.yaml')

IMPORTER = 'combat_patrol'
TOTAL_ISSUES = 90


def load_data(path=None):
    with open(path or DATA_PATH, encoding='utf-8') as fh:
        return yaml.safe_load(fh) or {}


# ── Validation ───────────────────────────────────────────

def check_provenance(data):
    """Refuse to import contents that cannot be traced back to a source.

    Not bureaucracy: when the shopping list later says "buy this", the claim
    has to be checkable. Undated, unattributed seed data is indistinguishable
    from invented seed data once it is in the database.
    """
    source = data.get('source') or {}
    problems = []
    if not source.get('urls'):
        problems.append('source.urls is empty — record where the contents came from')
    if not source.get('retrieved_on'):
        problems.append('source.retrieved_on is not set')
    if source.get('confidence') not in ('high', 'medium', 'low'):
        problems.append('source.confidence must be high, medium or low')
    if not source.get('corroborated_by'):
        problems.append('source.corroborated_by is empty — one source is a guess')
    return problems


def validate_issues(data):
    """Structural problems, found before anything touches the database."""
    collections = data.get('collections') or {}
    problems = []
    seen = set()
    for entry in data.get('issues') or []:
        number = entry.get('issue')
        if not isinstance(number, int) or not 1 <= number <= TOTAL_ISSUES:
            problems.append(f'issue {number!r} is not a number between 1 and {TOTAL_ISSUES}')
            continue
        if number in seen:
            problems.append(f'issue {number} appears more than once')
        seen.add(number)
        if entry.get('collection') not in collections:
            problems.append(f'issue {number}: unknown collection '
                            f'{entry.get("collection")!r}')
        for line in entry.get('contents') or []:
            if not line.get('unit'):
                problems.append(f'issue {number}: a contents line has no unit name')
            if int(line.get('models') or 0) < 1:
                problems.append(f'issue {number}: {line.get("unit")!r} needs at '
                                'least one model')
            spans = line.get('spans')
            if spans and (not isinstance(spans, list) or number not in spans):
                problems.append(f'issue {number}: spans {spans!r} must be a list '
                                'that includes this issue')
    return problems


# ── Matching ─────────────────────────────────────────────

def match_datasheet(conn, unit_name, faction_slug=None):
    """Resolve a unit name to a datasheet. Exact match only, never a guess.

    Scoped to the collection's faction where one is given, because names repeat
    across factions and the wrong Rhino is worse than no Rhino. Variants
    (Legends, Crucible) are excluded — a deprecated printing must never be
    picked up by a seed.
    """
    rows = conn.execute(
        'SELECT d.id, d.name, f.slug FROM datasheets d '
        'LEFT JOIN factions f ON f.id = d.faction_id '
        'WHERE d.variant IS NULL').fetchall()
    key = normalise_name(unit_name)
    exact = [r for r in rows if normalise_name(r['name']) == key]
    if faction_slug:
        scoped = [r for r in exact if r['slug'] == faction_slug]
        if len(scoped) == 1:
            return scoped[0]['id'], None
        if len(scoped) > 1:
            return None, f'{len(scoped)} datasheets named "{unit_name}" in {faction_slug}'
    if len(exact) == 1:
        return exact[0]['id'], None
    if len(exact) > 1:
        owners = sorted({r['slug'] or '?' for r in exact})
        return None, ('ambiguous: datasheets with this name exist in '
                      + ', '.join(owners))
    return None, 'no BSData datasheet with this name'


def template_name(issue, collections, entry):
    label = (collections.get(entry['collection']) or {}).get('name', entry['collection'])
    return f'Combat Patrol Magazine #{issue} — {label}'


# ── Import ───────────────────────────────────────────────

def seed(conn, data, owned_through=0, first_stage_id=None):
    """Create a kit template per issue, and owned kits up to ``owned_through``."""
    collections = data.get('collections') or {}
    source = data.get('source') or {}
    issues = sorted(data.get('issues') or [], key=lambda e: e['issue'])

    report = {'templates_created': 0, 'templates_updated': 0, 'kits_created': 0,
              'parts_only': [], 'unresolved': [], 'issues_present': len(issues),
              'issues_missing': [], 'models_seeded': 0}
    present = {e['issue'] for e in issues}
    report['issues_missing'] = [n for n in range(1, TOTAL_ISSUES + 1)
                                if n not in present]

    if first_stage_id is None:
        first_stage_id = db.first_owned_stage(conn)['id']

    for entry in issues:
        number = entry['issue']
        meta = collections.get(entry['collection']) or {}
        faction_slug = meta.get('faction')
        faction = db.get_faction_by_slug(conn, faction_slug) if faction_slug else None

        contents = []
        for line in entry.get('contents') or []:
            spans = line.get('spans')
            # A sprue split across issues belongs to the issue that completes
            # it — half a Maulerfiend is not a model you own.
            if spans and number != max(spans):
                continue
            datasheet_id, why = match_datasheet(conn, line['unit'], faction_slug)
            if datasheet_id is None:
                db.record_unresolved(
                    conn, IMPORTER, 'unit_line', line['unit'],
                    f'issue {number}: {why}',
                    source_ref=f'Combat Patrol Magazine #{number}',
                    payload={'issue': number, 'collection': entry['collection']})
                report['unresolved'].append((number, line['unit'], why))
                continue
            contents.append({'datasheet_id': datasheet_id,
                             'model_count': int(line['models'])})

        if not contents:
            # Either the issue only carries part of a multi-issue sprue, or
            # nothing in it resolved. Both mean no kit — and an empty template
            # would instantiate an empty kit, which looks like it worked.
            report['parts_only'].append(number)
            continue

        name = template_name(number, collections, entry)
        existing = conn.execute(
            'SELECT id FROM kit_templates WHERE name = ?', (name,)).fetchone()
        if existing:
            scan.update_template(conn, existing['id'], contents=contents)
            template_id = existing['id']
            report['templates_updated'] += 1
        else:
            template_id = scan.create_template(
                conn, name, contents,
                faction_id=faction['id'] if faction else None,
                contents_source='seed',
                contents_confidence=source.get('confidence') or 'low',
                source_urls=(source.get('urls') or []) + (source.get('corroborated_by') or []),
                notes=f'Hachette partwork, issue {number} of {TOTAL_ISSUES}')
            report['templates_created'] += 1

        if number <= owned_through:
            already = conn.execute(
                'SELECT 1 FROM kits WHERE kit_template_id = ? LIMIT 1',
                (template_id,)).fetchone()
            if not already:
                col.instantiate_template(
                    conn, template_id, stage_id=first_stage_id,
                    source='magazine_issue',
                    source_ref=f'Combat Patrol Magazine #{number}',
                    box_state='no_box')
                report['kits_created'] += 1
                report['models_seeded'] += sum(c['model_count'] for c in contents)

    return report


# ── Reporting ────────────────────────────────────────────

def print_report(data, report, dry_run=False):
    print()
    print('─' * 68)
    print(' Combat Patrol magazine seed' + ('  [DRY RUN]' if dry_run else ''))
    print('─' * 68)
    print(f"  issues in the data file    {report['issues_present']:>4} of {TOTAL_ISSUES}")
    print(f"  templates created          {report['templates_created']:>4}")
    print(f"  templates updated          {report['templates_updated']:>4}")
    print(f"  owned kits created         {report['kits_created']:>4}"
          + (f"  ({report['models_seeded']} models)" if report['models_seeded'] else ''))
    if report['parts_only']:
        print(f"  parts-only issues          {len(report['parts_only']):>4}  "
              f"({', '.join('#' + str(n) for n in report['parts_only'][:12])}"
              f"{'…' if len(report['parts_only']) > 12 else ''})")

    missing = report['issues_missing']
    if missing:
        print()
        print(f'  MISSING — {len(missing)} of {TOTAL_ISSUES} issues have no contents yet')
        print('  ' + _ranges(missing))
        print('  See seed/data/README.md. These are not guessed at.')

    if report['unresolved']:
        print()
        print(f"  UNRESOLVED — {len(report['unresolved'])} unit names needing a human")
        print('  (nothing below was invented or dropped; each is a row in '
              'unresolved_imports)')
        for number, unit, why in report['unresolved']:
            print(f'     #{number}  {unit}')
            print(f'       {why}')
    print('─' * 68)


def _ranges(numbers):
    """1,2,3,7,8 -> "1-3, 7-8" — 90 individual numbers is not a report."""
    out, start, prev = [], None, None
    for n in numbers:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f'{start}-{prev}' if start != prev else str(start))
            start = prev = n
    if start is not None:
        out.append(f'{start}-{prev}' if start != prev else str(start))
    return ', '.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--status', action='store_true',
                    help='report what the data file holds; touch nothing')
    ap.add_argument('--dry-run', action='store_true',
                    help='match everything and report, but roll back all writes')
    ap.add_argument('--owned-through', type=int, default=0, metavar='N',
                    help='also create owned kits for issues 1..N (Clay is at 75)')
    ap.add_argument('--data', help='override the contents file')
    ap.add_argument('--db', help='override the database path')
    args = ap.parse_args(argv)

    if args.db:
        db.DB_PATH = args.db
    data = load_data(args.data)

    structural = validate_issues(data)
    if structural:
        print('The contents file has problems:', file=sys.stderr)
        for problem in structural:
            print(f'  ✗ {problem}', file=sys.stderr)
        return 1

    issues = data.get('issues') or []
    if args.status:
        present = {e['issue'] for e in issues}
        missing = [n for n in range(1, TOTAL_ISSUES + 1) if n not in present]
        print(f'{len(present)} of {TOTAL_ISSUES} issues have contents')
        if missing:
            print(f'missing: {_ranges(missing)}')
        for problem in check_provenance(data):
            print(f'  provenance: {problem}')
        return 0

    if not issues:
        print('No issue contents in seed/data/combat_patrol_issues.yaml — nothing '
              'to seed.\nThe list is derived from a published source and reviewed, '
              'never written from\nmemory. See seed/data/README.md.', file=sys.stderr)
        return 1

    provenance = check_provenance(data)
    if provenance and not args.dry_run:
        print('Refusing to seed contents that cannot be traced back:', file=sys.stderr)
        for problem in provenance:
            print(f'  ✗ {problem}', file=sys.stderr)
        print('  (--dry-run works without provenance, for checking a draft.)',
              file=sys.stderr)
        return 1

    db.init_db()
    conn = db.connect()
    try:
        db.clear_unresolved(conn, IMPORTER)
        report = seed(conn, data, owned_through=args.owned_through)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    print_report(data, report, dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
