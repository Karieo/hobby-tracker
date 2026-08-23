# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A single-user tracker for **three hobbies in one — building, painting,
playing**, with buying as the way in — and the handoffs between them. Every model is tracked
individually from sprue to battle ready, across multiple armies and game
systems. Barcode scanning is *onboarding*, not the point: you scan a box once
and never again.

Flask + SQLite, Docker on `bastion` behind the Cloudflare Tunnel. `OWNER_NAME`
is `"Clay"`.

**`warhammer-tracker-spec.md` is the source of truth.** This file is
conventions and hard-won context.

## Status

**The spec was re-scoped on 2026-08-22 around Clay's own description of the
loop.** The old 13-step order described how to *construct* the app and was
mistaken for what it is *about* — which is how barcode scanning ended up first
in the navigation. "Do not build past step 5" is retired with it. Read
`warhammer-tracker-spec.md` §2 for the loop and §6 for the order.

Done: schema and migrations, the BSData + Munitorum importer, Kill Team
operatives, the collection (armies, kits, units, models, stage pipeline,
painting and session mode), the scanner with its queue and review screen, kit
view/edit/delete, the inventory view, the own-it check, basing applicability,
the list builder with its gap report and wishlist, shelf-scale onboarding
(queue sweep, per-barcode box page, identify-mode scanning, adopt-all, the
derived kit catalogue, and paste-import), and list import by paste.

All eleven checks of the loop pass end to end. What is unbuilt is the *other*
doors onto §2.7 — importing a list from a file or a URL — and those stay gated
on a source: every candidate host is refused by egress policy. Pasting never
was. §5 of the spec has the measured state of each step.

## Commands

```bash
python3 migrate.py [--status]        # apply / inspect migrations
python3 scripts/fetch_bsdata.py      # fetch BSData at the pinned SHA
python3 scripts/import_bsdata.py [--dry-run]   # import + report
python3 scripts/fetch_killteam.py    # fetch Kill Team at its pinned SHA
python3 scripts/import_killteam.py [--dry-run] # Kill Team operatives
python3 app.py                       # http://localhost:3100
python3 seed/combat_patrol_magazine.py --status   # magazine seed
python3 seed/derived_kits.py --status            # researched box contents
python3 -m pytest                    # tests
shellcheck backup.sh restore.sh      # the shell half, linted in CI too
```

CI (`.github/workflows/ci.yml`) runs the suite on 3.11 and 3.12, ShellChecks the
backup scripts, and builds and boots the Docker image. Keep it green: it is the
only thing standing between a regression and `main`.

## Architecture

Flat module layout at the repo root, mirroring Remndrs: `app.py` owns HTTP
routes and auth, `database.py` owns connections and queries, one module per
concern as they arrive. Stdlib `sqlite3` with `sqlite3.Row`, a fresh connection
per call, foreign keys ON and WAL. No ORM, no SPA framework, no build step.

**Migrations diverge from Remndrs deliberately.** Remndrs creates its schema
idempotently and patches it with an ad-hoc `_migrate()`. Here they are numbered
SQL files in `migrations/`, applied in order and recorded in
`schema_migrations`, because this data cannot be reconstructed from anywhere.
Migrations are additive and numbered; never rewrite one that has been applied.

## Invariants

These exist because getting them wrong corrupts data silently, months before
anyone notices.

- **EAN-keyed lookups only, never product name.** Combat Patrol: Orks is both a
  2021 and a 2024 box with completely different contents, and Clay owns both.
  `kit_templates.year` and the barcode are what tell them apart.
- **Never auto-save extracted or looked-up contents.** They pre-fill a review
  form; Clay confirms. Record `contents_source`, `contents_confidence` and the
  source URLs so a later "buy this" can be traced back.
- **Never invent a datasheet, never drop a line.** Every extracted or imported
  unit name matches BSData exactly, or becomes an `unresolved_imports` row with
  a manual picker. A silently dropped line is a shortfall Clay discovers at the
  till months later.
- **Seed data is derived and reviewed, or it doesn't ship.** Never write a kit
  catalogue *or a partwork contents list* from memory — it would be fluent,
  plausible, and wrong in places with no signal about which. A missing template
  costs two minutes; a wrong one corrupts ownership and purchase advice for
  months. `seed/data/combat_patrol_issues.yaml` ships empty for this reason and
  the importer refuses to run without provenance. Filling it in from a model's
  recall, rather than from a source, is the one change to this repo that would
  do real damage — `tests/test_combat_patrol_seed.py` asserts it stays empty.
  `seed/data/derived_kits.yaml` is the same bargain kept rather than deferred:
  every entry carries its sources, and **a barcode needs two independent
  sources agreeing** or the entry ships without one. Wrong contents under a
  name show up when the box is opened; a wrong barcode is silent.
- **No scraping** GW for prices, eBay for resale values, or any site for points.
- **`box_state` is not a model stage.** A sealed box and an opened one both hold
  models "On sprue", but only one carries a resale premium. Keep it on the kit.
- **Disposals are status changes, never deletions.** A sold kit stays with its
  models, excluded from ownership counts, retained for spend history.
- **Every progress figure is effort-weighted.** A Knight and a Termagant are
  both "1 model", which makes model-count percentages meaningless. Raw counts
  show alongside, never instead.

## Designing against abandonment

Clay already abandoned a hobby tracker. It didn't fail on features — it failed
because keeping it current cost more than it gave back. This app is *more*
granular, so the friction problem is worse by default. Section 6 of the spec is
requirements, not polish.

- **Whole units are the default interaction; individual models are the
  exception.** Per-model rows are the right storage and the wrong default UI.
  The primary control is "advance all to next stage" — one tap, no selection.
  If Clay has to think about which of ten Boyz he just based, the app has lost.
- **Never let stale paint stages block anything valuable.** Inventory and
  progress degrade independently; the expensive-to-maintain half must never gate
  list building, the gap report, or the shopping list.
- **Drift is recoverable.** Build the reconcile flow before the data drifts, not
  after.
- **No onboarding wizard that asks for 2,000 stages up front.** Scanning
  establishes ownership honestly at "On sprue"; paint stages get corrected
  opportunistically.

## Rules data

`datasheets` from BSData (identity, faction, keywords); `datasheet_points` from
the Munitorum Field Manual (flat per-size tables, official, licensed). They join
on normalised name **scoped by faction** — 35 names carry different points per
faction, so a global join would silently write wrong values. See the module
docstring in `scripts/import_bsdata.py` for the full reasoning and measurements.

Two columns exist to survive re-sync: `datasheet_points.manual_override` and
`datasheets.effort_is_override`. The importer reports them and leaves them
alone. Two columns exist because 11th edition outgrew the spec:
`datasheet_points.tier_min/tier_max` (Requisition Thresholds — your 3rd+ copy
costs more) and `datasheet_points.faction_id` (one Repulsor Executioner
datasheet, 255 points for Black Templars and 230 for Blood Angels).

## Scanning (built)

- **iPhone is the target.** WebKit does not implement `BarcodeDetector`, so it
  fails silently on every iOS browser. Build against ZXing-js as the *primary*
  decoder; feature-detect `BarcodeDetector` for desktop Chrome but never depend
  on it.
- `getUserMedia` needs a secure context — the Cloudflare Tunnel provides it, a
  plain-HTTP Tailscale IP does not.
- Manual digit entry is non-negotiable: glare, damaged boxes and dim shop
  lighting defeat camera scanning regularly.
- Split capture from enrichment. Camera stays open, decodes drop onto
  `scan_queue` with a beep, scanning resumes immediately. Write each scan to the
  server at once — a dead battery must not cost a shelf.
- GW EANs start `5011921`; books use ISBN-derived `978`. The prefix check
  **warns, never rejects** — and so does the check-digit test.
- ZXing 0.23's `decodeFrom*` helpers either want to own the `<video>` element or
  round-trip each frame through a data URL. The frame loop builds a
  `BinaryBitmap` from a canvas and calls `decodeBitmap` directly instead.
- `static/js/*.js` are classic scripts sharing one global scope with `app.js`.
  A second top-level `const $` is a SyntaxError that kills the page, so every
  file after `app.js` is wrapped in an IIFE.
- `app.js` binds `button.advance` globally. Never borrow that class for styling
  — use `.go`. The handlers now also require `data-unit` as a second guard.

## Backups

`backup.sh` snapshots via `sqlite3 .backup`, never `cp` — the app holds a
connection open, so committed data routinely sits in the `-wal` file with the
`.db` not yet containing it, and a plain copy loses it silently. `restore.sh
--check` verifies a snapshot without writing; `restore.sh <snap>` restores and
sets the current database aside first.

Both scripts run under `set -euo pipefail`, so **any helper that can fail must
be guarded**. A non-matching `grep` in `env_value` once took the whole backup
down with exit 1 and no output — under cron that is backups silently never
happening, which is strictly worse than having none. Both scripts now trap and
report failures loudly, and `tests/test_backup.py` guards the regression.

## Working agreement

- Ask before deleting, moving, or publishing anything. Always.
- At a real design fork, stop and ask rather than picking and moving on.
- Prefer working and boring over clever. This has to still make sense in six
  months.
- End every session by writing `handoff.md`: Goal, Current State, Active Files,
  Changes Made, **Failed Attempts**, Next Steps. Section 5 is not optional.
