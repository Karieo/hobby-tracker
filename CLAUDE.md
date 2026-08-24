# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A single-user tracker for **three hobbies in one — building, painting,
playing**, with buying as the way in — and the handoffs between them. Every model is tracked
individually from sprue to battle ready, across multiple armies and game
systems. Getting things *in* is onboarding, not the point: you say what is in a
box once and never again.

Flask + SQLite, Docker on `bastion` behind the Cloudflare Tunnel. `OWNER_NAME`
is `"Clay"`.

**`warhammer-tracker-spec.md` is the source of truth.** This file is
conventions and hard-won context.

## Status

**The spec was re-scoped on 2026-08-22 around Clay's own description of the
loop.** The old 13-step order described how to *construct* the app and was
mistaken for what it is *about* — which is how barcode scanning ended up first
in the navigation, years before it was removed for being slower than typing. "Do not build past step 5" is retired with it. Read
`warhammer-tracker-spec.md` §2 for the loop and §6 for the order.

Done: schema and migrations, the BSData + Munitorum importer, Kill Team
operatives, the collection (armies, kits, units, models, stage pipeline,
painting and session mode), kit view/edit/delete, the inventory view with its
filters, the own-it check, basing applicability, the list builder with its gap
report and wishlist, paste-import, list import by paste, and a dated photo log
per unit.

The scanner, the box catalogue and everything between them were built and then
removed — see **Scanning (removed)** below before rebuilding any of it.

All eleven checks of the loop pass end to end. What is unbuilt is the *other*
doors onto §2.7 — importing a list from a file or a URL — and those stay gated
on a source: every candidate host is refused by egress policy. Pasting never
was. §5 of the spec has the measured state of each step.

**The gap checker is built** (spec §8, written as "Section 7"): migration 008,
`list_parse.py`, `list_resolve.py`, `list_allocate.py`, and the report at
`/lists/<id>` — re-run live on every load, never stored. `lists.list_gap` is now
a name over `list_allocate.allocate`, so the wishlist and the list index inherit
the fix too: `raise_wishlist` was reading the same double-counted numbers and
under-asking for exactly the models Clay would have found missing at the
table.

**One name-similarity function, `list_resolve.similarity`**, used by both paste
doors. It sorts the words before comparing (rapidfuzz calls that
`token_sort_ratio`) rather than using §8's `token_set_ratio`, which scores any
strict subset as a perfect match — built that way it resolved "Warboss on
Warbike" to Warboss at 100, a wrong confident match on the very example §8 uses
to explain why aliases exist. It exists because `lists.list_gap` counts ownership per entry with
nothing consuming a model once assigned, so a list asking for two squads of ten
Boyz reports "fieldable" against ten Boyz owned. `list_allocate.allocate` now
answers that correctly — short 10, not fieldable — but nothing renders it yet.

**A column a migration fills is a column some writer has to keep filling.**
Twice now: 008 numbered `list_entries.position` and `add_entry` left new rows
at 0; 008 backfilled `models.datasheet_id` and `add_models` left new models
null, which would have made allocation report a full collection as owning
nothing. Both fixed at the writer. When adding a column in a migration, find
every INSERT into that table in the same commit.

**§9 of the spec lists ten requirements the 2026-08-22 re-scope stopped
mentioning without deciding against.** None is a bug; each is a decision still
owed. The first to be discharged is export: `GET /api/export/inventory` (spec
§9.1) serves an external list optimiser as JSON or CSV, authenticated by a
bearer token from `api_tokens` — the table migration 001 created and nothing
read until now. **A token reaches `/api/export/` and nothing else**; widening
that is one entry in `app.TOKEN_PATHS` and should be a decision rather than a
side effect.

## Commands

```bash
python3 migrate.py [--status]        # apply / inspect migrations
python3 scripts/fetch_bsdata.py      # fetch BSData at the pinned SHA
python3 scripts/import_bsdata.py [--dry-run]   # import + report
python3 scripts/fetch_killteam.py    # fetch Kill Team at its pinned SHA
python3 scripts/import_killteam.py [--dry-run] # Kill Team operatives
python3 app.py                       # http://localhost:3100
python3 seed/combat_patrol_magazine.py --status   # magazine seed
python3 scripts/report_kit_datasheets.py         # what migration 008 could not map
python3 scripts/api_token.py --create "name"     # mint an export token (shown once)
python3 scripts/api_token.py --list|--revoke ID  # ...and manage them
python3 scripts/check_rules_pins.py              # has BSData or the MFM moved?
python3 -m pytest                    # tests
shellcheck backup.sh restore.sh      # the shell half, linted in CI too
```

CI (`.github/workflows/ci.yml`) runs the suite on 3.11 and 3.12, ShellChecks the
backup scripts, and builds and boots the Docker image. Keep it green: it is the
only thing standing between a regression and `main`.

## Architecture

Flat module layout at the repo root, mirroring Remndrs: `app.py` owns HTTP
routes and auth, `database.py` owns connections and queries, one module per
concern as they arrive. No packages — the gap checker's modules sit alongside
the rest rather than under a `gap_checker/`.

**There are two list parsers, deliberately, and they are not interchangeable.**
`bulk_add.parse_lines` reads a shelf typed from memory: it takes stage words
("20 Boyz built") and may skip a line it cannot use. `list_parse.parse` reads
an app's export: it carries points and position, detects the format, and may
never skip anything. `/lists/import` uses the second now, so a real export's preamble
is dropped rather than reported as four unknown units; `/add` still uses the
first, which is right for it. The scaffolding patterns are shared
(`bulk_add.SECTION_RE`, `TOTAL_RE`, `POINTS_RE`) so the two cannot drift. Stdlib `sqlite3` with `sqlite3.Row`, a fresh connection
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
  box catalogue *or a partwork contents list* from memory — it would be fluent,
  plausible, and wrong in places with no signal about which. A missing template
  costs two minutes; a wrong one corrupts ownership and purchase advice for
  months. `seed/data/combat_patrol_issues.yaml` ships empty for this reason and
  the importer refuses to run without provenance. Filling it in from a model's
  recall, rather than from a source, is the one change to this repo that would
  do real damage — `tests/test_combat_patrol_seed.py` asserts it stays empty.
  The researched box catalogue that once made the same bargain is gone with the
  scanner, and the rule outlived it: anything typed into a template is Clay
  looking at the box, never a model recalling one.
- **No scraping** GW for prices, eBay for resale values, or any site for points.
- **`box_state` is not a model stage.** A sealed box and an opened one both hold
  models "On sprue", but only one carries a resale premium. Keep it on the kit.
- **Disposals are status changes, never deletions — at two levels.**
  `kits.status` covers a whole box; `models.disposed_on/_as/_price_cents`
  (migration 010) covers "I sold five of my twenty Boyz". The model keeps its
  `stage_id`, deliberately: a *Sold* stage would have made all thirty ownership
  queries correct for free and destroyed the fact worth recording, which is
  that the five were painted. Ownership excludes them through
  `m.disposed_on IS NULL` in the JOIN — the **ON** clause, never WHERE, since
  these are LEFT JOINs and a WHERE would drop every unit with no models.
  `tests/test_collection.py::test_every_ownership_surface_drops_a_disposed_model`
  walks the surfaces, because a filter half the queries ignore is a collection
  that over-counts quietly for months. A sold kit stays with its
  models, excluded from ownership counts, retained for spend history. A
  *correction* is the other thing: `collection.remove_models` and
  `delete_unit` delete rows outright, because plastic that was never there has
  no history worth keeping. Every screen that offers one has to say which it
  is, or the cheap control becomes the one Clay reaches for and the spend
  history quietly empties.
- **An endpoint with no caller is not a feature.** `DELETE /api/units/<id>`
  shipped in the first commit and nothing ever called it, so "I have no way to
  remove models" was true while the route sat there answering. `POST
  /api/units/<id>/models` is still in that state. Grep the templates and
  `static/js/` before believing a capability exists.
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

**Neither source is ever re-imported on its own.** Both are pinned —
`rules_data.py` holds all three SHAs, and the fetch scripts import them from
there — and `deploy.sh` only imports when the datasheets table is empty. So the
app stays on whatever revision it was first built with until someone bumps a
pin deliberately. `/reference` shows which manual priced the database and warns
when `data/mfm/` is newer than the import; `scripts/check_rules_pins.py` asks
GitHub whether the pins have aged, and the weekly sweep runs it. Nothing bumps
a pin automatically: points moving under a list is something to accept
deliberately, not to wake up to.

Two columns exist to survive re-sync: `datasheet_points.manual_override` and
`datasheets.effort_is_override`. The importer reports them and leaves them
alone. Two columns exist because 11th edition outgrew the spec:
`datasheet_points.tier_min/tier_max` (Requisition Thresholds — your 3rd+ copy
costs more) and `datasheet_points.faction_id` (one Repulsor Executioner
datasheet, 255 points for Black Templars and 230 for Blood Angels).

## Scanning (removed)

The barcode scanner is gone, and so is the browsable catalogue behind it. Clay:
*"The scanning doesn't work well and I would just rather look up the contents
at the time of purchase and add them in manually. Must faster."*

He is right about why. Scanning only paid off when the app already knew the
box, which meant researched contents behind every barcode — and that research
could never keep up with what he was actually buying, so most scans landed on
"unidentified box" and waited for him to type the contents anyway. Typing them
once, at the till, skips the camera and the queue both.

**Do not rebuild it without new information.** The failure was not the decoder
or the review screen; it was that a barcode is only a key, and nobody publishes
the table it opens.

What replaced it: `/add` pastes a list of models, and `/templates` says what is
in a box once so buying another copy is one action. Neither needs a lookup to
answer.

`barcodes` and `scan_queue` survive with nothing reading them. Dropping a table
destroys the codes already linked to templates, which is a decision of its own
rather than a side effect of deleting a screen.

## The Kits screens (removed)

Clay: *"Drop the kits page, it's not helpful."*

Gone: the `Kits` nav entry, `/kits`, `/kits/<id>`, their four API routes and
the JavaScript that drove them. `templates/kits.html` and `kit.html` are
deleted.

**The `kits` table stays, and so does every row in it.** Asked how far to go —
the screens, or the data too — Clay first chose everything, then chose to keep
the data once it was clear what depends on it:

- the journey's *Bought* and *Sold* entries, which are the spend history
- `buildable_from_spare` in the gap report and the export, which matches an
  unbuilt sprue to a datasheet through `kit_datasheets` — keyed on `kits.id`
- the collection's "still sealed" counts, from `box_state`
- `instantiate_template`, which is what "Define a box" creates

So this is a screen removal, not a schema change. There is no migration and
nothing is destroyed; `collection.py`'s kit functions are all still here and
still called.

`update_kit` and `delete_kit` had their only test coverage through the routes,
so those tests moved down to `tests/test_collection.py` rather than leaving
with the screens — `update_kit`'s partial write is the exact bug `update_unit`
shipped, and it would have been left unguarded.

**Rebuilding a kits screen is a decision, not a restoration.** What made this
one unhelpful is that it listed boxes, and a box is not a thing Clay does
anything with once the models are out of it.

## Three piles, and no ledger

Clay: *"This is over complicated, I just want to be able to add/remove one at
a time is fine, I don't care about sell price or purchase price."*

A model is in one of three piles — **owned**, **wishlist**, **gone** — and the
unit page is three rows of plus and minus. That replaced four panels, each
with a count box and a submit button, one of them also asking what the models
went for. Every one of those fields was a decision the app wanted and did not
need.

`POST /api/units/<id>/pile/<pile>` with a `delta` handles all six directions.
`_PILES` in `app.py` is the whole mapping.

**Sold, traded and given away are one pile.** The difference between them is a
story rather than a number, and the models have gone either way. The
`disposed_as` and `disposed_price_cents` columns still exist and still record
`'sold'` — nothing collects a price any more, and re-introducing one means
deciding it is worth a field, not just filling a column that happens to be
there.

**Every button has its opposite beside it**, which is why nothing here asks for
confirmation. `undispose_models` and `unwishlist_models` are those undos.

The counts repaint from the reply rather than reloading: these get tapped
several times running, and a reload between each throws away the scroll
position.

## Backups

`backup.sh` snapshots via `sqlite3 .backup`, never `cp` — the app holds a
connection open, so committed data routinely sits in the `-wal` file with the
`.db` not yet containing it, and a plain copy loses it silently.

**Photos are the one thing whose bytes are not in the database.** A row in
`unit_photos` points at a file under `data/photos/`, so the snapshot and the
directory have to travel together or a restore produces a log of missing
pictures. `backup.sh` rsyncs them into a shared `photos/` beside the snapshots
— shared because the filenames are random and immutable, so thirty snapshots
are not thirty copies — and rotation never touches it. `restore.sh` brings
them back. Anything else stored on disk has to make the same arrangement, or
it is not backed up at all. `restore.sh
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
