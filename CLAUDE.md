# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A single-user Warhammer 40,000 collection tracker: every model tracked
individually from sprue to battle ready, across multiple armies, with barcode
scanning for onboarding ~100 boxes. Flask + SQLite, Docker on `bastion` behind
the Cloudflare Tunnel. `OWNER_NAME` is `"Clay"`.

**`warhammer-tracker-spec.md` is the source of truth.** This file is
conventions and hard-won context.

## Status

Build steps 1–2 of the spec's 13-step order are done: schema, migration runner,
stages/factions seed, and the BSData + Munitorum importer. v1 ends at step 5
(armies/kits/units/models CRUD, scanner, collection view). **Do not build past
step 5** — the dashboard, list builder, gap report, shopping list, sale
candidates and export are specced so the schema doesn't paint us into a corner,
not because they're wanted yet.

## Commands

```bash
python3 migrate.py [--status]        # apply / inspect migrations
python3 scripts/fetch_bsdata.py      # fetch BSData at the pinned SHA
python3 scripts/import_bsdata.py [--dry-run]   # import + report
python3 app.py                       # http://localhost:3100
python3 -m pytest                    # tests
```

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
  catalogue from memory — it would be fluent, plausible, and wrong in places
  with no signal about which. A missing template costs two minutes; a wrong one
  corrupts ownership and purchase advice for months.
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

## Scanning (step 4, not built)

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
  **warns, never rejects**.

## Working agreement

- Ask before deleting, moving, or publishing anything. Always.
- At a real design fork, stop and ask rather than picking and moving on.
- Prefer working and boring over clever. This has to still make sense in six
  months.
- End every session by writing `handoff.md`: Goal, Current State, Active Files,
  Changes Made, **Failed Attempts**, Next Steps. Section 5 is not optional.
