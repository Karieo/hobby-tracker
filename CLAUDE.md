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

**The wishlist deduplicates across lists on the maximum, never the sum.** Ten
Boyz for Saturday and twenty for Sunday is twenty to buy — the same twenty
field either game, one at a time. It said thirty for months, with a test
asserting it and a comment admitting the test pinned the behaviour rather than
blessing it. `raise_wishlist` tops up `_raised_pool` and then claims out of it.

That makes one model answer several lists, which a single
`models.wishlist_source_list_id` cannot express, so **migration 012 adds
`wishlist_claims`** and the two split the work: the column marks *the pool* —
this row exists because a list asked, as opposed to a standing want of Clay's —
and the table records *which lists need it now*. Keying the pool on the column
rather than on a live claim is what stops a list that shrinks from ejecting
models from the pool and having the next raise buy them again. Both foreign
keys cascade, so `unwant_template` and `remove_models` need no new cleanup.

**Standing wants and box wants stay outside the pool on purpose.** A list's
shortfall and a thing Clay simply wants are different facts; collapsing them
would quietly under-order.

**List validation has three states, and the third is the point.** `problem` is
definite, `review` means a check could not run, `ok` means every check ran and
passed. Two states would have been a lie: 415 of the 1,445 imported 40,000
datasheets carry no `min_models`/`max_models`, an unresolved row has no
datasheet to check against, and an allied detachment looks exactly like a
faction mistake — so a faction stranger is reported and never called a fault. A
badge saying "legal" while a third of the checks were skipped is worse than no
badge, because it gets believed.

Over the limit stays a `problem` even when something is unpriced, since
unpriced entries can only ever *add* points. Under the limit with something
unpriced is `review`, because the missing number could take it over.

**It is not a rules engine and says so on screen.** Unit sizes come in
increments — ten or twenty Boyz, never fifteen — and two columns cannot express
that, so fifteen passes. Detachments and enhancements are not modelled at all.

**The shopping list answers in boxes, because a shop does not sell seven
Boyz.** `shopping.py` and `/shopping`: the wishlist names datasheets and model
counts, which is the right answer to "what am I short" and the wrong one to the
question asked standing in a shop. The cover is greedy and deliberately dull —
most still-missing models first, ties to the smaller overage — and buys one box
at a time, so twenty Boyz against a box of ten is that box twice with no
reasoning about multiples. It is **not an optimiser**: a real minimum-cost cover
needs a price on every box, most have none, and optimising against `rrp_cents`
would mean optimising against whichever boxes Clay happened to have priced.

**The overage is carried because it is the cost nothing else would show.** Four
boxes covering the list with forty spare models is worse than five with six, and
`spare` is the only number on the screen that says so.

**Bundle against à la carte is one function run twice** — once over every box,
once over only the single-unit ones. A comparison computed by different code
from the thing it compares to is a comparison that drifts. No saving is claimed
unless both sides are fully priced *and* both cover the ground; a negative one
is reported as it stands, since a comparison that only ever flatters the bundle
is not a comparison.

**Prices are three-state for the same reason list validation is.** A total that
quietly skipped the unpriced boxes would read **low**, which is the one
direction a shopping total must never be wrong in. `priced` is a figure,
`partial` is a floor the screen shows as "at least", `unpriced` shows none —
and `partial` is the honest common case. Anything no box in the catalogue
contains is named on the page rather than dropped, the same rule the importers
keep.

**`_cover` has two guards against picking a box that covers nothing, and that
is deliberate.** The loop terminates because each pass reduces what is
outstanding, so a zero-coverage pick does not produce a bad plan — it produces
a page that never loads. Either guard alone holds it;
`test_a_box_that_covers_nothing_does_not_hang_the_plan` pins the behaviour
rather than either mechanism.

**The backlog measures work left, not models left.** `backlog.py` and
`/backlog`: `effort_left = effort × steps still ahead / steps from the start`.
Ten Boyz on sprue is 10.0, the same ten needing only a final check is 1.7, and
one untouched Knight is 8.0 and beats both. Counting miniatures would rank
those backwards, which is the whole reason this app weights by effort. The
ladder is per model — `stages_for` — so a Trukk is four steps from done and not
six, and vehicles do not look permanently unfinished. Wishlist models are not
backlog: they are not on the shelf.

**`/paint` and `/backlog` are different moments and stay separate.**
`paintable_units` is freshest-first with no sorting because what Clay touched
last night is what he is about to pick up, and sorting controls are useless
with a wet brush. Deciding *what to start* wants the opposite. `/backlog` has
no nav entry — the nav is five items on a phone and Clay has complained about
clutter — so it is reached from the home tile and from the paint picker.

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

**A Kill Team's faction comes from its catalogue, never its name.** Clay: *"when
I filter for orks it filters out my ork kill team."* It did — the importer
matched a team's *name* against a 40,000 faction, so Orks matched Orks and
Kommandos matched nothing. 1158 of 1450 operatives sat on faction rows no
40,000 filter could reach.

The allegiance was in the data all along: the 2024 game system defines category
entries (Ork, Aeldari, Drukhari, Imperium) and each catalogue references the
ones it claims. `import_killteam.resolve_factions` reads them, takes the
narrowest that names a real faction — breadth measured by how many catalogues
claim it, so Drukhari at one beats Aeldari at six — and lets a 2021 printing
inherit from its 2024 twin by name. 34 teams placed, 679 operatives on real
factions, Ork operatives visible under an Orks filter 23 → 53.

**That rule tops out at 34 teams, and the rest are a reviewed decision, not a
better inference.** `seed/data/killteam_factions.yaml` is Clay's own table of
the 2024 bespoke teams. It layers over the derivation and wins, because the
inference is the fallback and the person is the authority — and it caught three
places where the category rule was confidently wrong: Hand of the Archon
derived to Aeldari (they are Drukhari), Brood Brothers to Tyranids (Genestealer
Cults) and Inquisitorial Agents to Astra Militarum (Imperial Agents). All three
are printed in the report rather than applied silently, since a disagreement
means one of the two is wrong and which is worth knowing. 74 teams placed,
1095 operatives on real factions.

The file is trusted for exactly one reason — a person reviewed it and
`source.reviewed_by` says so — so `load_reviewed` refuses a table without
provenance, the same bargain `seed/data/combat_patrol_issues.yaml` makes. An
entry naming a faction with no row, or a team with no catalogue, is reported
and written to `unresolved_imports` rather than approximated to the nearest
thing.

**A third layer reads the categories a catalogue defines itself.** The 2021
Compendium catalogues were written against a different game system and declare
their categories inline instead of referencing the 2024 ones, so
`faction_categories` saw nothing in them — `2021 - Greenskin.cat` says
`<categoryEntry name="Ork"/>` in its own body and nothing was reading it.
`self_categories` does, for teams the game system could not place, and **only
when exactly one real faction is named**: `Heretic Astartes` lists Iron
Warriors, Night Lords and World Eaters beside Chaos Space Marine, and choosing
the allegiance out of its own legions takes knowledge the data does not carry.
One match is a reading, several is a guess, and a guess is reported instead.

**One row per team, not one per printing.** `2021 - Fellgor Ravager` and
`2024 - Fellgor Ravagers` were making a faction row each — eleven operatives on
one and twelve on the other, so filtering for either showed half the team.
`canonical_names` files every printing under the newest spelling, which is the
box still on sale. Only unplaced teams could split this way; a placed team's
printings already meet on the faction they share.

**The 22 still unplaced keep their own row, and Clay chose that on
2026-08-26.** He was shown the list and what each one looks like it means —
`Asuryani`, `Craftworld`, `Harlequins` reading as Aeldari; `Imperial Guard` and
`Veteran Guardsmen` as Astra Militarum; `Adeptus Astartes` as Space Marines;
`Kroot` and `Hunter Cadre` as T'au — and said to leave them all. So the report
naming them every run is the settled state, not an outstanding gap.

That is the right call and worth defending: the data does not say any of it.
Every one of those readings is a model recognising an older army name, which
is exactly the recall this repo refuses — fluent, plausible, and wrong in
places with no signal about which. An alliance like `Imperium` covers nineteen
teams while naming no faction, and the 2021 Compendium teams are legacy anyway.
**Do not place them later without asking him again.**

**Fellgor Ravagers, Chaos Cult and Blooded stay unplaced on purpose**, and the
report naming them every run is the intended state rather than a gap to close.
Clay was asked and chose it: `Chaos` names no 40,000 army, and filing them
under Chaos Space Marines would be wrong — Blooded are Traitor Guard and
Fellgor are Beastmen. The two questions beside it went the other way, also on
his say-so: `Agents of the Imperium` is BSData's `Imperial Agents`, and
Imperial Navy Breachers go there too. Those are recorded as his decisions in
the YAML, because reading two names and deciding they mean one army is a
judgement and not a lookup.

**A natural key may not contain a derived value.** `datasheets.bsdata_id` was
`kt:{edition}:{faction}:{entry}` while the comment beside it said "edition,
team and entry id are all stable" — and the faction is the one part that is
not. Every time a team's allegiance was worked out, the key moved with it: the
importer could no longer find its own row, inserted a second, and left the
first behind on the old faction. Placing Greenskin under Orks put nine
operatives there and left nine more under `Greenskin`. Clay, after two rounds
of faction work had shipped: *"the filtering on the factions is still not
working properly."* Re-importing made **86 duplicate operatives**. It is keyed
on the team now, and `_legacy_row` finds a row written under the old key by
`source_note` + edition + entry id and corrects it in place, so an existing
database heals on the next import instead of needing a migration to clean up
after it. Extra copies from a re-import that already ran are **reported, never
deleted** — any of them may be carrying Clay's models.

**A faction row nothing points at is a dead end in a filter and a fair choice
in a picker.** They appear on their own: placing a Kill Team under Orks moves
its operatives and leaves the team's own row empty. `list_factions` carries a
`datasheets` count; the collection's filter drops the empty ones (keeping any
already selected, so a bookmarked URL still says what it shows) and the pickers
that *assign* a faction keep them.

**The name match compares normalised names, never raw strings.** It used to use
`name = ?`, so the compendium team `T'au Empire` missed the faction row `T’au
Empire` on the apostrophe alone and got a `kt-t-au-empire` row of its own: 24
operatives on a duplicate the army picker offered twice and no T'au filter
reached. `match_faction` tolerates punctuation and a plural (`Space Marine` →
`Space Marines`) and nothing else — widening it to a fuzzy match is how a team
gets filed under an army it does not belong to.

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

**"To sell" is a shortlist, not a disposal.** Clay: *"Not sold, sell a list of
things to part with."* The models are still on the shelf and still his — they
keep counting as owned, keep advancing through the stages, keep showing in the
collection. `models.for_sale_on` (migration 011) is a flag beside ownership,
never a state inside it. Wishlist is want-and-do-not-have; this is
have-and-would-rather-not.

Migration 010's `disposed_*` columns were the first attempt, built on reading
"sell" as past tense. Nothing writes them now. They are left in place rather
than dropped because dropping a column rewrites the table, and an inert column
costs nothing while a destructive migration for tidiness costs a restore if it
goes wrong. The `m.disposed_on IS NULL` filters in the ownership queries stay
with them, correct and currently inert.

**Every button has its opposite beside it**, which is why nothing here asks for
confirmation. `undispose_models` and `unwishlist_models` are those undos.

The counts repaint from the reply rather than reloading: these get tapped
several times running, and a reload between each throws away the scroll
position.

## Backups

`backup.sh` snapshots via `sqlite3 .backup`, never `cp` — the app holds a
connection open, so committed data routinely sits in the `-wal` file with the
`.db` not yet containing it, and a plain copy loses it silently.

**The home screen says when the last backup finished, because cron's failure
mode is silence.** `backup.sh` runs nightly on bastion; it reports failures
loudly, but at 3am loudly is one line in a log nobody opens — the exact hazard
the `env_value` bug already demonstrated.

`backup_status.last_backup` reads `data/.last-backup`, which `backup.sh` writes
as its **final** act. Final is the whole point: under `set -euo pipefail`,
reaching that line means every step above succeeded, so a run that dies half
way leaves the marker at its old value and the screen keeps saying the backup
is overdue.

**A marker, not the snapshots themselves.** The container mounts `./data` and
`./.env` and nothing else, so `/mnt/t7` does not exist from inside it —
statting `BACKUP_DIR` would work perfectly in development and report "no
backups, ever" on the only machine that matters.

**Three states, and `unknown` is not `never`.** A missing or corrupt marker
means *the app has no record*, which is also what a box with real backups on
the T7 looked like the day this shipped. Every unreadable case falls to
`unknown` rather than to a date: failing open here would put a reassuring line
on the home screen about a backup that never happened, which is the one
outcome worth engineering against.

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
