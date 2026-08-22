#!/usr/bin/env python3
"""Seed boxed-set contents derived from published sources.

    python3 seed/derived_kits.py --status
    python3 seed/derived_kits.py --dry-run
    python3 seed/derived_kits.py

The catalogue problem, stated honestly: there is no open dataset of Games
Workshop box contents keyed by EAN. BSData gives the rules; nobody publishes
the plastic. So the contents of every box have to be looked up one at a time,
and the only place that lookup can happen is outside this repo — by a person,
or by an assistant with a search tool, reading real sources and recording them.

This is the importer for the result. It exists so that lookup happens **once
per product, ever**: contents recorded here become a kit template, and every
copy of that box already sitting on the shelf resolves behind it.

What it will not do is make any of it up. Spec §11 and CLAUDE.md both say it,
and it is the rule that matters most here:

    the catalogue must be derived, not authored. A kit list written from a
    model's memory would be fluent, plausible, and wrong in places, with no
    signal about which places.

So: every entry must carry sources or the import refuses. Every unit name is
matched against an imported BSData datasheet, and anything that does not match
is written to ``unresolved_imports`` rather than guessed at or dropped.

**Barcodes are held to a higher bar than contents.** A barcode needs two
independent sources agreeing before it is attached, because Combat Patrol: Orks
is both a 2021 and a 2024 box with completely different contents. Wrong
contents under a name are visible the moment Clay opens the box; a wrong
barcode silently attaches the wrong contents to a box he scans months later.
An entry with an uncorroborated barcode still ships without it — the template
is reachable by name from the review screen, which is most of the value.
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'scripts'))

import yaml

import database as db
import scanning as scan
# One definition of "the same name", shared with the rules-data importer and
# the magazine seed: a unit that matched there matches here.
from combat_patrol_magazine import match_datasheet

DATA_PATH = os.path.join(_ROOT, 'seed', 'data', 'derived_kits.yaml')
IMPORTER = 'derived_kits'
# The schema's allowed set, and the accurate word for what this is: reviewed
# seed data. The per-template source URLs carry where it actually came from.
CONTENTS_SOURCE = 'seed'


def load_data(path=None):
    with open(path or DATA_PATH, encoding='utf-8') as fh:
        return yaml.safe_load(fh) or {}


# ── Validation ───────────────────────────────────────────

def check_entry(entry, index):
    """Everything that must be true before an entry may touch the database.

    Undated, unattributed seed data is indistinguishable from invented seed
    data once it is in the database, and the shopping list later makes claims
    on top of it. So provenance is a precondition, not a nicety.
    """
    label = entry.get('name') or f'entry {index}'
    problems = []

    if not (entry.get('name') or '').strip():
        problems.append(f'entry {index}: no name')

    contents = entry.get('contents') or []
    if not contents:
        problems.append(f'{label}: no contents — an empty template would '
                        'instantiate an empty kit, which looks like it worked')
    for line in contents:
        if not (line.get('unit') or '').strip():
            problems.append(f'{label}: a contents line has no unit name')
        if int(line.get('models') or 0) < 1:
            problems.append(f'{label}: {line.get("unit")!r} needs at least '
                            'one model')

    sources = entry.get('sources') or {}
    if not sources.get('urls'):
        problems.append(f'{label}: sources.urls is empty — record where the '
                        'contents came from')
    if not sources.get('retrieved_on'):
        problems.append(f'{label}: sources.retrieved_on is not set')
    if sources.get('confidence') not in ('high', 'medium', 'low'):
        problems.append(f'{label}: sources.confidence must be high, medium '
                        'or low')
    if int(sources.get('corroborated_by') or 0) < 2:
        problems.append(f'{label}: sources.corroborated_by must be at least 2 '
                        '— one source is a guess')

    problems += check_barcode(entry, label)
    return problems


def check_barcode(entry, label):
    """A barcode needs two independent sources. See the module docstring.

    Refused rather than dropped: silently importing the entry without its
    barcode would hide the fact that someone tried to attach an unsourced one.
    """
    code = (entry.get('barcode') or '').strip()
    sources = entry.get('barcode_sources') or []
    if not code:
        return [f'{label}: barcode_sources given with no barcode'] if sources else []
    if scan.normalise_code(code) != code:
        return [f'{label}: barcode {code!r} is not plain digits']
    if len(sources) < 2:
        return [f'{label}: barcode {code} has {len(sources)} source(s) — a '
                'barcode needs two independent ones agreeing, or leave it off '
                'and let the template be reached by name']
    return []


# ── Import ───────────────────────────────────────────────

def seed(conn, data, dry_run=False):
    """Create or update a kit template per entry. Idempotent on name+year."""
    report = {'created': 0, 'updated': 0, 'barcodes_linked': 0,
              'unresolved': [], 'skipped': [], 'kits': []}

    for entry in data.get('kits') or []:
        name = entry['name'].strip()
        year = entry.get('year')
        faction_slug = entry.get('faction')
        faction = db.get_faction_by_slug(conn, faction_slug) if faction_slug else None
        sources = entry.get('sources') or {}

        contents = []
        for line in entry.get('contents') or []:
            row, why = match_datasheet(conn, line['unit'], faction_slug)
            if row is None:
                if not dry_run:
                    db.record_unresolved(
                        conn, IMPORTER, 'unit_line', line['unit'],
                        f'{name}: {why}', source_ref=name,
                        payload={'kit': name, 'year': year})
                report['unresolved'].append((name, line['unit'], why))
                continue
            contents.append({'datasheet_id': row['id'],
                             'model_count': int(line['models'])})

        if not contents:
            # Nothing resolved. A template with no contents cannot be adopted,
            # so creating one would only add a dead end to the dropdown.
            report['skipped'].append((name, 'no contents resolved'))
            continue

        report['kits'].append((name, year, len(contents),
                               sum(c['model_count'] for c in contents)))
        if dry_run:
            continue

        existing = find_template(conn, name, year)
        faction_id = faction['id'] if faction else None
        if existing:
            scan.update_template(conn, existing['id'], name=name, year=year,
                                 faction_id=faction_id, contents=contents)
            template_id = existing['id']
            report['updated'] += 1
        else:
            template_id = scan.create_template(
                conn, name, contents, faction_id=faction_id, year=year,
                contents_source=CONTENTS_SOURCE,
                contents_confidence=sources.get('confidence'))
            report['created'] += 1

        # Provenance is written here rather than through update_template,
        # which does not carry these columns. Re-stated on every run so a
        # corrected source URL in the data file actually reaches the database.
        record_provenance(conn, template_id, sources)

        code = (entry.get('barcode') or '').strip()
        if code:
            scan.link_barcode(conn, code, template_id)
            report['barcodes_linked'] += 1

    return report


def record_provenance(conn, template_id, sources):
    """Where these contents came from, kept on the template itself.

    The point of the whole exercise: months from now the shopping list will say
    "buy this", and the claim has to be traceable back to something a person
    can go and read.
    """
    conn.execute(
        'UPDATE kit_templates SET contents_source = ?, contents_confidence = ?, '
        'contents_source_urls = ?, updated_at = ? WHERE id = ?',
        (CONTENTS_SOURCE, sources.get('confidence'),
         json.dumps(sources.get('urls') or []), db.now(), template_id))


def find_template(conn, name, year):
    """Matched on name *and* year, because the year is what tells two boxes
    with the same name apart. Combat Patrol: Orks 2021 and 2024 are different
    products and must never collapse into one template."""
    return conn.execute(
        'SELECT * FROM kit_templates WHERE name = ? AND year IS ?',
        (name, year)).fetchone()


# ── CLI ──────────────────────────────────────────────────

def status(data):
    entries = data.get('kits') or []
    with_code = [e for e in entries if (e.get('barcode') or '').strip()]
    print(f'{len(entries)} kit{"" if len(entries) == 1 else "s"} in '
          f'{os.path.relpath(DATA_PATH, _ROOT)}')
    print(f'{len(with_code)} with a corroborated barcode, '
          f'{len(entries) - len(with_code)} reachable by name only')
    for entry in entries:
        models = sum(int(line.get('models') or 0)
                     for line in entry.get('contents') or [])
        code = (entry.get('barcode') or '').strip() or '—'
        year = entry.get('year') or '—'
        print(f'  {code:<15} {year:<6} {models:>3} models  {entry.get("name")}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--status', action='store_true',
                        help='what is in the data file; touch nothing')
    parser.add_argument('--dry-run', action='store_true',
                        help='match everything and report; write nothing')
    parser.add_argument('--data', help='an alternative YAML file')
    args = parser.parse_args(argv)

    data = load_data(args.data)
    if args.status:
        status(data)
        return 0

    problems = []
    for index, entry in enumerate(data.get('kits') or [], start=1):
        problems += check_entry(entry, index)
    if problems:
        print('Refusing to import — provenance problems:\n')
        for problem in problems:
            print(f'  ✗ {problem}')
        print('\nSee seed/data/README-derived.md. Nothing was written.')
        return 1

    conn = db.connect()
    try:
        report = seed(conn, data, dry_run=args.dry_run)
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    print(f'{"Would create" if args.dry_run else "Created"} '
          f'{report["created"]} template(s), updated {report["updated"]}, '
          f'linked {report["barcodes_linked"]} barcode(s)')
    for name, year, units, models in report['kits']:
        print(f'  {name} ({year or "—"}): {units} unit(s), {models} models')
    for name, reason in report['skipped']:
        print(f'  ! skipped {name}: {reason}')
    if report['unresolved']:
        print(f'\n{len(report["unresolved"])} unresolved line(s) — recorded for '
              'a manual pick, never guessed:')
        for name, unit, why in report['unresolved']:
            print(f'  ? {name}: {unit} — {why}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
