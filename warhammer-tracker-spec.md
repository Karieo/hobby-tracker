# Warhammer Tracker — the spec

The source of truth for what this app is for. `CLAUDE.md` is conventions and
hard-won context; this is the goal.

Written from Clay's own description of the loop, 2026-08-22, replacing the
13-step build order that shipped in chat. That order described how to
*construct* the app. It was mistaken for what the app is *about*, which is how
barcode scanning — a task you do once per box and then never again — ended up
as the first item in the navigation.

## 1 · What this is

**Three hobbies in one: building, painting, playing** — with buying as the way
in. Clay's framing, and the right one. They are not separate tools that happen
to share a database: the whole point is the handoff between them, and the app
is worth having only to the extent those handoffs are seamless.

Everything else — inventory, barcodes, points, effort weighting — exists to
serve that loop. None of it is the goal.

## 2 · The loop

Clay's words, in his order. Each step names the handoff it receives and the one
it passes on.

### 2.1 · Before buying — "do I already own this?"

Standing in a shop with a box in hand, or browsing online: **do I own this
already, how many, and what state are they in?**

A lookup, not an addition. The answer is a count and a breakdown — *you own 2:
one built, one still sealed* — fast enough to use before reaching the till.

This is the step that saves money, and it is the one the app has never had.

> **Hands off to:** nothing. It is the gate before the loop starts.

### 2.2 · Buying → the collection

**Scan the box, look up what is in it, and insert every model at once.**
Never model by model. A Combat Patrol is twenty-six miniatures and typing them
individually is how a tracker dies.

Every model arrives labelled, pointed at a real datasheet, at "On sprue".

**Contents are typed at the till, not scanned off the box.** This was a
barcode scanner with a sprint queue, a review screen, a box page per code and a
researched catalogue of box contents behind all of it — built, used, and
removed on 2026-08-24. Clay: *"The scanning doesn't work well and I would just
rather look up the contents at the time of purchase and add them in manually.
Must faster."*

The reason it failed is the reason not to rebuild it: a barcode is only a key,
and nobody publishes the table it opens. Scanning paid off only for a box the
app already had contents for, so the catalogue had to keep ahead of what Clay
was buying, and it never could — most scans ended in typing the contents
anyway, one screen later than typing them directly.

**Saying it once still pays for every copy.** A template names a box and what
is in it; owning another copy is one action. That half was always hand-made and
it survives.

**Contents are never authored from recall.** The rule outlived the catalogue
that motivated it: what goes into a template is Clay reading a box, or a source
a person can read — never a plausible guess.

> **Hands off to:** the inventory, and from there the building phase.

### 2.3 · Inventory — what I own and what state it is in

**How many of each, and where each one is.** Two facts, not one:

- **The box** is sealed, opened, or gone. A sealed box carries a resale
  premium; an opened one does not.
- **The models** are on sprue, assembled, primed, painted, based, battle ready.

Clay said "new in box, sealed, built" as one scale. They are two, and keeping
them apart is what makes both "what can I sell" and "what can I play"
answerable.

**Everything already on the shelf needs its own door.** Models built, painted,
or split out of a box years ago are the ones most likely to be missing. So:
paste a list, one line per unit
(`20 Boyz built`, `Trukk primed`, `5 Nobz`), confirm what matched, decide about
what did not. Forgiving about shape, unforgiving about names — a line either
resolves to a real datasheet or comes back for a decision.

> **Hands off to:** building — the inventory is where you see what is waiting.

### 2.4 · Building

Sprue to assembled. Clippers, glue, a desk.

> **Hands off to:** painting. What you assembled tonight is what is ready to
> prime, and priming happens in batches, outdoors, on a different day.

### 2.5 · Painting

Primed, base coated, based, battle ready.

**Basing is not universal, and this is a real modelling problem.** A Rhino has
no base. A pipeline that makes `Base prepared` and `Based` mandatory for every
model either strands a vehicle at a stage it can never leave or advances it
through one that never happened — and because progress is effort-weighted, a
false advance on an effort-8 vehicle quietly inflates how finished the whole
collection looks.

**So stages are applicable per model, not universal.** A model with no base is
measured out of five stages instead of seven.

Which models those are cannot be derived. Rhino, Land Raider and Trukk are
`Vehicle` and have no base; Redemptor Dreadnought, Killa Kans and Deff Dread
are `Vehicle + Walker` and do. That correlation held across the nine checked by
hand, and it is a correlation, not a rule GW publishes — whether a kit ships
with a base is a fact about the plastic, and BSData describes the rules. So it
surfaces as a question next to the model, which stops being asked once
answered, and until then nothing is reclassified. Proposed and confirmed, never
guessed, per §4.

> **Hands off to:** playing. A unit reaching battle ready is what makes a list
> fieldable.

### 2.6 · Playing — the list

**Build a list against the collection.** For each entry: do I own it, is it
built, is it painted, or do I need to buy it?

What is missing becomes a **wishlist** — the thing you take to the shop, which
closes the loop back to 2.1.

> **Hands off to:** buying, building and painting all at once. This is the
> keystone. Without a list to aim at, the pipeline has no pull: models only
> move forward when Clay happens to feel like it, and nothing ever says *this
> is the next thing to work on, and here is why*.

### 2.7 · Lists from elsewhere

**Import a list someone else published** — a tournament list, a new detachment
— and cross-reference it against the collection: what could I field today,
what would I have to buy or paint first?

> **Hands off to:** 2.6, and through it to the rest.

## 3 · What the app must never become

The previous tracker Clay abandoned did not fail on features. It failed because
keeping it current cost more than it gave back. This one is *more* granular, so
the friction problem is worse by default.

- **Whole units are the default interaction.** Per-model rows are the right
  storage and the wrong default UI.
- **Never let stale paint stages block anything valuable.** Inventory and
  progress degrade independently; the expensive-to-maintain half must never
  gate list building, the gap report, or the wishlist.
- **Drift is recoverable.** Build the reconcile flow before the data drifts.
- **No onboarding wizard that asks for 2,000 stages up front.**

## 4 · Invariants

These are load-bearing. Each exists because getting it wrong corrupts data
silently, months before anyone notices. Full reasoning in `CLAUDE.md`.

- **EAN-keyed lookups only, never product name.**
- **Never auto-save extracted or looked-up contents** — they pre-fill a form
  Clay confirms.
- **Never invent a datasheet, never drop a line.**
- **Seed data is derived and reviewed, or it does not ship.**
- **No scraping** GW for prices, eBay for resale, or any site for points.
- **`box_state` is not a model stage.**
- **Disposals are status changes, never deletions.**
- **Every progress figure is effort-weighted**, with raw counts alongside,
  and every one of them goes through `collection.done_fraction` — one
  definition of how far along a model is, read forwards by the rollups and
  backwards by the backlog.

## 5 · Where the app stands against this

Measured 2026-08-22, from the code rather than memory.

| Loop step | State |
|---|---|
| 2.1 Own-it check | **Built.** Searching the collection walks the whole of BSData, so "you own none" is an answer. Filterable by faction, points, stage and ownership. |
| 2.2 Box → collection | **Built, by hand.** A template names a box and its contents; owning a copy inserts every model in one action. The barcode scanner and the researched catalogue behind it were built and removed on 2026-08-24 — see §2.2 for why not to rebuild them. |
| 2.3 Inventory | **Built.** One row per datasheet: owned, built, battle ready, sealed boxes, wanted. Paste-import is the door for what is already on the shelf. |
| 2.4 Building | **Built.** The ladder's first half, moved a whole unit at a time. Not a separate mode — building and painting are one pipeline. |
| 2.5 Painting | **Built.** Paint mode, session mode, per-unit pipeline, and basing applicability. |
| 2.6 List → gap → wishlist | **Built.** The gap splits buy from paint; the shortfall raises a wishlist tagged with the list that wanted it. |
| 2.7 List import | **Built.** Paste a list, confirm every line, and it lands on its own gap report. The paste door needs no source; fetching one still has none. |

Walked end to end against a fresh database: all 11 checks in the loop pass.
Buying the shortfall closes the gap, which is the loop actually closing rather
than each step working alone.

2.7 sat marked "blocked on a source" longer than it deserved. That was true of
*fetching* a list — every candidate host is still refused by egress policy —
but never of pasting one, and pasting is the door that always works. The
file and photo doors remain unbuilt; neither is needed for the loop to close.

Rules data is in place for all of it: 1,445 Warhammer 40,000 datasheets, 2,544
points rows, 1,450 Kill Team operatives.

## 6 · Build order

The loop's own order, not the old step numbers:

1. ~~**Inventory view** (2.3)~~ — done. The backbone; 2.1 and 2.6 are both
   queries against it.
2. ~~**Own-it check** (2.1)~~ — done, and it turned out to be the same screen
   rather than a second one.
3. ~~**Basing applicability** (2.5)~~ — done. Keywords are stored now; they are
   a hint, not a classifier, because they cannot actually decide it.
4. ~~**List builder, gap, wishlist** (2.6)~~ — done.
5. ~~**Onboarding at shelf scale** (2.2, 2.3)~~ — done, then halved. The
   scanning half was removed on 2026-08-24 as slower than typing; paste-import
   and hand-defined templates are what remain, and they are the parts that
   never needed a lookup to answer.
6. ~~**List import** (2.7)~~ — done, by paste. The blocker was on fetching a
   list, not on receiving one.

What is left after that is not new machinery but the things the loop makes
worth having: allocating models between competing lists, and a build mode of
its own if building ever wants one.

## 7 · Known blockers

- ~~**The kit catalogue is grown, not imported.**~~ Resolved by removing it.
  No open dataset of GW box contents or EANs exists and direct fetches are
  refused by egress policy, so the catalogue could only ever grow one lookup at
  a time — and it could not keep ahead of what Clay was buying, which made the
  scanner it existed to serve slower than typing. Both are gone. The blocker
  was real; the answer was that the feature was not worth what it cost.
- **`BACKUP_DEST` unset.** Snapshots live on the same machine as the database.

---

## 8 · The gap checker

Written as "Section 7 — List Gap Checker" against the original spec's
numbering, where §7 was *Ownership, gaps and the shopping list*. It lands here
as §8 because this file was re-scoped around the loop and §7 is already Known
blockers. It arrived as a separate document; it is reproduced whole below,
because a spec that lives only in a chat upload is a spec the next session
cannot read.

### 8.0 · Where it disagrees with this database

**Built in five commits, finished 2026-08-23.** Three more deviations came out
of the last one:

- **The endpoints live under `/api/`.** The spec writes `PATCH
  /lists/<id>/entries/<eid>`; here that is `/api/lists/...`, because
  `require_login` answers an unauthenticated `/api/` call with a 401 and
  everything else with an HTML redirect to the login page. A JSON client
  getting a login form back is the difference between an error it can report
  and one it cannot.
- **The assembly prompt is a control, not an interrupt.** `add_models` stamps
  every model with its unit's datasheet at creation, so the silent auto-fill
  the spec describes for single-option kits has already happened and there is
  nothing to stop Clay for. What auto-fill cannot answer — a box that builds
  several things, where the unit's datasheet is a default rather than a
  decision — is a block on the unit page, shown only for those kits.
- **`points_owned` is a ratio, so it is a sentence rather than a tile.** Three
  tiles across is the design's rule and a fourth wraps onto a line of its own,
  which reads as a separate fact.

Recorded once, here, rather than argued again in each commit.

- **"Models currently link to kits ... the gap checker cannot resolve anything
  without a direct link" is not true here.** `models.unit_id` is NOT NULL and
  `units.datasheet_id` is NOT NULL, so every model already resolves to exactly
  one datasheet and `collection.inventory()` has done that since the collection
  screen was built. The premise describes an earlier schema. The columns are
  still worth having for the half of Section 7 that is new — `is_flexible` and
  `kit_datasheets` — but the backfill follows the unit rather than the kit, and
  the "uncommitted" population starts at zero rather than starting large.
- **`datasheet_id TEXT` is INTEGER here.** `datasheets.id` is an INTEGER
  primary key. TEXT would still "work" in SQLite and then silently fail every
  join.
- **`CREATE TABLE lists` extends `army_lists` instead.** That table already
  exists and `models.wishlist_source_list_id` references it — the link recording
  which list wanted a wishlisted model. A parallel table would split that in
  two. `raw_text` is nullable, because a hand-built list has no pasted text.
- **`list_entries` was rebuilt, not created.** An unresolved entry needs
  `datasheet_id` nullable and SQLite cannot drop NOT NULL in place.
- **"Points owned is the sum of `points`" uses `points_snapshot`.** §2.7
  settled that this app prices a list from the Munitorum manual it imported. An
  export's own figure is stored beside it as `points` and shown, never totalled.
- **No `rapidfuzz`, and no `gap_checker/` package.** Fuzzy matching is stdlib
  `difflib`, which `bulk_add` already used for the same job, and the modules sit
  flat at the repo root like every other module here.
- **"Rolls back clean" is not a thing this repo has.** Migrations are
  forward-only numbered files recorded in `schema_migrations`. What is
  guaranteed is atomicity.

##### 8.1 · The document, as written

Append to `warhammer-tracker-spec.md`. Depends on Sections 1–5 (schema, BSData importer, model CRUD with stage management).

#### What it does

You paste an army list exported from New Recruit or the GW app. The app tells you, per unit, how many models the list needs, how many you own, how many are battle ready, and how many you're short. Plus a points summary: list total vs. points-worth of models you actually own.

It does not build lists. It does not validate legality. New Recruit already does both, reads the same BSData files, and is free. This feature answers the one question no list builder can: *can I field this with what's on my shelf?*

#### Prerequisite schema change

Models currently link to kits. Kits don't map cleanly to datasheets — a Combat Patrol box yields three or four datasheets' worth of models — so the gap checker cannot resolve anything without a direct link.

But the link isn't one-way either. An Armiger sprue builds a Helverin or a Warglaive. The big Knight kit builds five-plus datasheets. And magnetized models stay swappable forever. `datasheet_id` is therefore not a property of the kit — it's the decision made at assembly, and sometimes that decision stays reversible.

```sql
-- What a kit is capable of becoming
CREATE TABLE kit_datasheets (
    kit_id       INTEGER NOT NULL REFERENCES kits(id) ON DELETE CASCADE,
    datasheet_id TEXT NOT NULL REFERENCES datasheets(id),
    PRIMARY KEY (kit_id, datasheet_id)
);

-- What a model currently is, and whether that's reversible
ALTER TABLE models ADD COLUMN datasheet_id TEXT REFERENCES datasheets(id);
ALTER TABLE models ADD COLUMN is_flexible INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_models_datasheet ON models(datasheet_id);
CREATE INDEX idx_models_flexible ON models(is_flexible) WHERE is_flexible = 1;
```

Three states a model can be in:

- **Committed** — `datasheet_id` set, `is_flexible = 0`. Glued as one thing, stays that thing.
- **Magnetized** — `datasheet_id` set (what it's built as right now), `is_flexible = 1`. Counts as battle ready for any of its kit's datasheets, because swapping arms takes seconds.
- **Uncommitted** — `datasheet_id` null. On sprue, or assembled but not yet assigned. Can become any of its kit's datasheets, but needs work first.

`is_flexible` survives stage changes. Set it once when you magnetize; it stays true through painting and basing. It is never inferred — a model is only flexible because you said so.

##### Assembly prompt

When a model advances to `assembled`, resolve its datasheet:

- Kit has one entry in `kit_datasheets` → auto-fill silently. Most kits. You never see a prompt.
- Kit has several → one-tap picker of that kit's options, plus a "magnetized" toggle that sets `is_flexible`.
- Whole-unit advance → pick once, apply to all models in the unit.

Surface a count of uncommitted assembled models in the collection view so they don't silently rot.

#### New tables

```sql
-- A pasted list, kept so you can re-check it after painting progress
CREATE TABLE lists (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    army_id       INTEGER REFERENCES armies(id),   -- nullable
    raw_text      TEXT NOT NULL,                   -- exactly what was pasted
    source_format TEXT,                            -- 'newrecruit' | 'gw_app' | 'unknown'
    points_total  INTEGER,                         -- as declared by the export
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- One row per parsed unit entry. Order preserved for display.
CREATE TABLE list_entries (
    id            INTEGER PRIMARY KEY,
    list_id       INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    raw_name      TEXT NOT NULL,                   -- name as it appeared in the paste
    datasheet_id  TEXT REFERENCES datasheets(id),  -- null = unresolved
    model_count   INTEGER NOT NULL DEFAULT 1,
    points        INTEGER,
    resolved_by   TEXT                             -- 'exact' | 'fuzzy' | 'alias' | 'manual'
);

-- The learned alias table. This is what makes the feature survive.
CREATE TABLE datasheet_aliases (
    id           INTEGER PRIMARY KEY,
    alias        TEXT NOT NULL UNIQUE,             -- normalized form
    datasheet_id TEXT NOT NULL REFERENCES datasheets(id),
    created_at   TEXT NOT NULL
);
```

`raw_text` is stored deliberately. When the parser gets better, old lists can be re-parsed without re-pasting.

#### Parsing

Both New Recruit and the GW app export plain text with a recognizable shape: a unit name, a model count, a points value, then indented wargear lines. Wargear is discarded — Section 3 already excluded loadout tracking.

Three handlers, tried in order:

1. **New Recruit** — detect by header signature. Entries look like `Boyz (180 points)` with a following `• 20x Ork Boy` line.
2. **GW app** — detect by its own header. Entries carry the count inline: `10x Boyz [90pts]`.
3. **Permissive fallback** — scan every line for the pattern `[count] name [points]` in any order, using a regex set. Anything that doesn't match becomes an unresolved entry with `raw_name` set to the whole line.

Do not try to be clever about ambiguous lines. A line the parser can't handle becomes a visible row in the UI that you fix by hand, and that fix teaches the alias table. Silent dropping is the failure mode to avoid — a missing unit makes the whole report wrong in a way you won't notice.

Model count defaults to 1 when absent. Character entries usually have no count.

#### Resolution

For each entry, in order:

1. Normalize the name: lowercase, strip punctuation, collapse whitespace, strip a trailing parenthetical.
2. Look up `datasheet_aliases` on the normalized form. Hit → `resolved_by = 'alias'`, done.
3. Exact match against normalized BSData datasheet names. Hit → `'exact'`.
4. Fuzzy match (token set ratio, `rapidfuzz`). Score ≥ 90 and a clear margin over second place → `'fuzzy'`. Otherwise leave null.
5. Null entries render in the UI with a searchable datasheet picker. Picking one sets `resolved_by = 'manual'` **and writes a row to `datasheet_aliases`**.

Step 5's write-back is the whole point. If you have to re-answer "which datasheet is *Warboss on Warbike*?" every time you paste a list, you'll stop pasting lists. Every correction must be permanent.

Scope fuzzy matching to the list's faction when `army_id` is set. Cuts the candidate pool and kills most false positives.

#### Allocation

The subtle bug worth naming up front: a list with two 10-model Boyz units and one 20-model Boyz unit needs **40** Boyz. Counting per-entry against your collection will match all three entries against the same 20 models you own and report zero shortfall. You will discover this the night before a game.

Allocate instead, in two passes.

**Pass 1 — committed and magnetized-as-built.** Per datasheet:

```
requirements = list entries for this datasheet, sorted by model_count DESC
available    = models where datasheet_id = X, army_id matches (or is unassigned)
               sorted by stage DESC (battle ready first)

for each requirement:
    take = min(requirement.model_count, len(available))
    assign the first `take` models; available = available[take:]
    requirement.owned        = take
    requirement.battle_ready = assigned models at stage 'battle ready'
    requirement.short        = requirement.model_count - take
```

**Pass 2 — fill remaining shortfalls from models that could become the datasheet.** Candidates are unconsumed models where the kit's `kit_datasheets` includes the required datasheet, and either `datasheet_id IS NULL` or `is_flexible = 1`.

Process shortfalls **most-constrained first** — the requirement with the fewest eligible candidates gets served before one with plenty of options. Within a shortfall, prefer magnetized battle-ready models over uncommitted ones, since those need no work at all.

```
shortfalls = requirements where short > 0, sorted by len(candidates(r)) ASC

for each shortfall:
    pool = candidates(shortfall), magnetized-and-ready first, then by stage DESC
    take = min(shortfall.short, len(pool))
    assign and consume those models
    shortfall.swappable  = assigned models that are magnetized and battle ready
    shortfall.buildable  = assigned models that are not
    shortfall.short     -= take
```

A magnetized model is still one physical model. It can serve exactly one requirement in a list, and once consumed it's gone — a single magnetized Knight cannot be both the Paladin and the Errant in the same list.

Most-constrained-first is a heuristic, not provably optimal. At your collection size it won't be wrong in practice, and the alternative is a bipartite matching solver for no real gain.

Largest requirement first in pass 1, battle-ready first throughout. Greedy is correct within a datasheet — all models of the same datasheet are interchangeable.

Whether unassigned models (`army_id IS NULL`) count toward a list should be a toggle on the check view, defaulting to on. You keep kits unassigned on purpose; excluding them by default would make the report pessimistic and useless.

#### Endpoints

Follow Remndrs conventions — Flask blueprint, JSON in/out, server-rendered templates for the views.

```
POST   /lists                    paste raw text, parse, resolve, persist, return report
GET    /lists                    saved lists with staleness indicator
GET    /lists/<id>               re-run allocation against current collection, return report
PATCH  /lists/<id>/entries/<eid> set datasheet_id manually, write alias, re-run
DELETE /lists/<id>
POST   /lists/<id>/reparse       re-run parser over stored raw_text
```

`GET /lists/<id>` re-running allocation on every load is deliberate. The report is a live view of your collection, not a stored result. Paint three Meganobz, reload the list, the numbers move. That feedback loop is the feature.

#### The report view

One table, one row per list entry, ordered as pasted:

| Unit | Need | Ready | Swap | Build | Short |
|---|---|---|---|---|---|

Row states, visually distinct:
- **Short** — you don't own the plastic. The number that matters.
- **Buildable** — you own models that could become this, but they need work. Uncommitted sprues, or assembled-but-unassigned.
- **Swappable** — magnetized and battle ready, just built as something else right now. Tap to see which models and what they're currently configured as.
- **Owned, not ready** — right datasheet, unfinished. Links to the painting queue.
- **Ready** — done, no action.
- **Unresolved** — name didn't match. Datasheet picker inline.

Swappable rows should read as green. A magnetized Warglaive built as a Helverin is a two-minute arm swap, not a project.

Summary line above the table: `1,740 / 2,000 pts owned · 1,215 pts battle ready · 2 swaps · 3 units short`.

Points owned is the sum of `points` for entries where `short = 0`, counting swappable and buildable models toward ownership. A partially-owned unit contributes nothing — a 7-of-10 Boyz mob is not 70% of a Boyz mob on the table.

Battle-ready points count swappable models, since a swap costs no hobby time. Buildable models do not count.

Unresolved entries are excluded from all totals and the summary says so explicitly. Never let an unresolved row quietly deflate the numbers.

#### Test cases

Non-negotiable, these are the ones that break silently:

1. Two 10-model and one 20-model entry of the same datasheet, 20 owned → reports 20 short, not 0.
2. Same datasheet across two armies with `army_id` filtering on → only the matching army's models count.
3. Unassigned models toggle off → those models excluded from allocation.
4. Entry with no model count (character) → treated as 1.
5. Unparseable line → appears as an unresolved row, is not dropped.
6. Manual resolution → alias persists, second paste of the same text resolves automatically.
7. Empty paste, wargear-only paste, list with a header but no entries → no crash, clear message.
8. List needs 2 Warglaives, you own 3 uncommitted Armiger sprues → 0 short, 2 buildable, 1 sprue left over.
9. List needs 1 Warglaive and 1 Helverin, you own 1 magnetized Armiger → one row swappable, the other short. One model cannot serve both.
10. Most-constrained ordering: two shortfalls, one with a single eligible candidate and one with several, all drawing from the same pool → the constrained one is served first and neither reports a false shortfall.
11. Magnetized model at battle ready → counts toward battle-ready points for the datasheet it can swap to, not just the one it's built as.
12. `is_flexible` survives a stage advance from assembled through battle ready.

#### Out of scope for this section

List creation, legality validation, detachment rules, enhancement caps, wargear matching, and any attempt to reconcile a list against specific physical models rather than counts. Export to New Recruit stays the play path.

---

## 9 · Carried over from the original spec, and not built

Audited 2026-08-23, module by module, against the 14-section build spec this
file replaced on 2026-08-22.

The re-scope was right to lead with the loop — but it summarised fourteen
sections into seven and, in doing so, stopped mentioning several concrete
requirements without ever deciding against them. A requirement nobody argued
about and nobody wrote down is the one that gets rediscovered as a surprise. So
they are listed here, with what the app actually has today.

| Original | Requirement | State |
|---|---|---|
| §10 | **CSV export of the whole collection** — "non-negotiable. The data must never be trapped in this app." | **Part built,** 2026-08-23. `GET /api/export/inventory` emits the inventory as JSON or CSV, bearer-token or session authenticated, with `bsdata_id` as the join key and every points tier uncollapsed. It is per *datasheet*, which is what a list optimiser asked for. **`GET /collection.csv` was added 2026-08-24** — the collection screen, downloadable, carrying exactly the filters the page is showing. Deliberately not a button pointed at `/api/export/inventory`: that one is per datasheet for an optimiser and knows only `army_id`, so on a screen filtered to "Orks, unpainted" it would have downloaded every Ork including the painted ones. Both routes go through one `_collection_filters` and one `_collection_rows`, so the file and the page cannot drift. Still owed from §10: list export as text and JSON. |
| §8 | **Sale candidates** — sealed + owned kits, with age, price paid, duplicates, and *whether any list calls for the contents* | **Built,** 2026-08-26. `sale.py` and `/sale`, computed live. That last clause is the feature: a list of expensive things owned is a spreadsheet, a list of things *no planned game needs* is a decision. `models.for_sale_on` has held the shortlist since migration 011 and nothing ever fed it — this proposes, the unit page's pile controls still decide. Two sections because they are two objects: **sealed boxes**, where the box is what you act on because `box_state` carries a resale premium opening destroys; and **surplus models** per datasheet, where the box is irrelevant for exactly the reason the kits screen was dropped. **Needed is the maximum any one list asks, never the sum** — the rule the wishlist deduplicates on and the reason `list_allocate` gives every list the whole collection; summing would inflate what looks needed, hide real surplus and quietly recommend nothing. A sealed box is held back whole if any datasheet in it is wanted, and named rather than dropped. Models already on the shortlist, and models still inside a sealed box, count as owned but are not proposed — the second because getting them out is what destroys the premium the first section is about, so proposing them would offer the same plastic twice. An unresolved list row could be asking for anything here, so the count leads the page and every surplus is reported as a *possible* one. |
| §9 | **List validation** — points against the limit, legal unit sizes, faction consistency, and the three-state badge that refuses to show a false green | **Built,** 2026-08-26. `list_validate.validate` runs live on `/lists/<id>` like the gap report, never stored. Three states because two would lie: *problem* (definite — over the limit, a unit outside its datasheet's size), *review* (nothing wrong that can be seen, but a check could not run), *ok* (every check ran and passed). `review` is the honest common case — 415 of 1,445 imported datasheets carry no unit size, an unresolved row has nothing to check against, and an allied detachment looks exactly like a faction mistake, so a faction stranger is never a fault. Over the limit stays a *problem* even with something unpriced, since unpriced entries can only add. Deliberately not a rules engine: sizes come in increments (ten or twenty Boyz, never fifteen) and `min_models`/`max_models` cannot say so, and detachments and enhancements are not modelled — the badge says so where it passes. |
| §7 | **Shortfalls → purchases** — invert `kit_templates`, show the overage | **Built,** 2026-08-26. `shopping.py` and `/shopping`, computed live like the gap report and the backlog. The wishlist answers in datasheets and a shop sells boxes, so this covers the shortfall greedily out of `kit_templates` — most models still missing first, ties to the smaller overage — and buys one box at a time, so twenty Boyz against a box of ten is that box twice rather than any reasoning about multiples. Anything no box contains is named rather than dropped. **The price half was removed the same day it shipped**, on Clay's "spend and kits are obsolete": no totals, no three-state price honesty, and no à la carte comparison, because the app no longer asks what a box costs. The overage survives as the only cost the screen reports. `rrp_cents` stays in the schema, unread. |
| §7 | **Global shopping list** — deduplicate across lists on the *maximum* requirement, so two lists needing a Deff Dread means buying one | **Built,** 2026-08-25. It used to do the opposite: each list raised its own whole shortfall, so ten Boyz for Saturday and twenty for Sunday put *thirty* on the wishlist — ten models of over-buying on the one screen whose job is saying what to buy. `raise_wishlist` now tops up a shared pool (`_raised_pool`) and claims what it needs out of it, so the answer is the maximum and not the sum. Deduplication makes one model answer several lists, which `models.wishlist_source_list_id` could not express, so migration 012 adds `wishlist_claims`; the column keeps the narrower job of marking the pool. Standing wants and box wants are deliberately outside the pool — a list's shortfall and a thing Clay simply wants are different facts, and collapsing them would under-order. The answer no longer depends on which list was raised first, which the single column could not manage: whichever ran first owned the models and the other was invisible on the line while still waiting on them. |
| §7 | **Sharing models between lists** — "don't allocate models to lists", show a quiet note instead: *"3 Killa Kans also appear in Speed Freeks 1000"* | **Not built.** Note that this does *not* conflict with §8's allocation: that allocates within one report, computed live and never stored, which is what stops one squad satisfying two entries of the same list. Across lists, the original rule still stands. |
| §5.1 | **Dashboard** — models finished in the last 30 days (from `stage_events`), and total spend | **Built,** 2026-08-27, minus the spend half. `recent.py` and the "Last 30 days" panel on Home. Models finished is the number the spec asks for; `effort_done` rides alongside in the same currency §5.5 reports what is left in, and becomes the headline in a month that finished nothing — priming sixty Boyz is an evening every night, and answering that with "0 finished" is the abandonment failure §6 is about. Arrivals are excluded structurally (an inner join on the from-stage), so typing in a painted collection does not report itself as a month's work; buying crosses no step; same-day retreats cancel, later strip-backs do not; nothing ever subtracts. The one counting surface that does **not** filter disposals — it counts what Clay did, not what he has. **The spend total is dropped rather than owed:** PR #60 took the money off every screen at Clay's request, so there is nothing left for it to total. |
| §5.1b | **Games played, per list** — win/loss and point difference | **Built,** 2026-08-27, and not in the original spec: Clay asked for it after moving play into Battlebase. `games.py`, migration 013, and the Games section on `/lists/<id>`, with the record on the index. Records **both scores** rather than a difference — asked which way round, he chose the pair, and it is what tells "lost 85–90" from "lost 45–90". Result and margin are derived on read, never stored. The 0–100 range is his number, pinned as data like `BATTLE_SIZES`, and not scoped by game system because this repo does not write game rules from recall. Outcomes only: no missions, objectives or turns — "playing the game is a whole other thing". |
| §5.5 | **Backlog** — every unfinished model, grouped by unit, sortable by army and acquisition date, effort shown, "a big push or a quick win" | **Built,** 2026-08-26. `backlog.py` and `/backlog`, computed live like the gap report. The sort *is* the feature, so the number it sorts by has to mean something: `effort_left = effort × steps still ahead / steps from the start`. Ten Boyz on sprue is the mob's whole effort (10.0); the same ten needing only a final check is a sixth of it (1.7); one untouched Knight is 8.0 and outranks both — which is the point of weighting by effort at all. Raw `models_left` shows alongside, never instead. The ladder is per model, so a Trukk is four steps from done and not six. Five orderings: big push, quick win, longest waiting (undated boxes last, since most have no date), recently touched, by army. `paintable_units` stays as it was — `/paint` is for a wet brush and this is the deciding beforehand. No nav entry; reached from the home tile and the paint picker. |
| §5.9 | **Admin** — points overrides, effort overrides, stage editing | **Partly built.** `datasheet_points.manual_override` and `datasheets.effort_is_override` exist and the importer respects them; nothing in the UI sets either. |
| §14 | **Non-goals: "No Kill Team", "No 10th edition points"** | **Superseded, deliberately.** 1,450 Kill Team operatives are imported and the points are 11th edition. Both changed on purpose; the original list is stale here rather than violated. |

Nothing on this list is a bug. Each is a decision waiting to be made — build it,
or write down that it is out of scope — and the point of the table is that the
decision now has to be made out loud.

### 9.1 · `GET /api/export/inventory`

Built 2026-08-23 to its own spec. The parts worth carrying here:

- **`collection.export_inventory` is a sibling of `inventory()`, not an edit.**
  The collection screen depends on that function's shape.
- **It groups by `models.datasheet_id`, not the unit's.** Post-migration 008
  they agree unless Clay has said otherwise, and where they disagree the model
  is right: an uncommitted sprue is not anything yet, and counting it as its
  unit's datasheet would report the same plastic as both owned and buildable.
- **`sum(by_stage) == owned + wishlist` for every row**, asserted in a test.
  A failure there means something is double-counted through the flexible or
  capability joins, and the optimiser would build lists Clay cannot field.
- **A bearer token reaches `/api/export/` and nothing else.** Deliberately
  narrower than "use `api_tokens`": a token that can read the inventory is a
  different thing to leave in a script's config than one that can delete a
  kit. `app.TOKEN_PATHS` is where that widens, and it should be a decision.
- Tokens are SHA-256, not bcrypt — 256 bits of `secrets` output has nothing to
  brute-force, and a salted hash could not be looked up by index at all.
  `scripts/api_token.py` mints, lists and revokes them.
- **`faction=` narrows the rows too, added 2026-08-24.** By name or slug, in
  any case, because it gets typed into a curl — `?faction=orks` is writable
  from memory where `?faction_id=1` is a lookup first. An unknown faction is a
  404 rather than an empty list: a cheerful zero rows is how you conclude you
  own no Orks when you actually mistyped. Applied to the assembled rows rather
  than in SQL, because four things contribute rows — the ownership aggregate,
  `by_stage`, the capability join and the flexible join — and the last two can
  add a datasheet nothing is built as yet. One filter at the end cannot miss a
  contributor the way four copies of a WHERE clause could.
- **`fields=` narrows the rows, added 2026-08-24.** The full row is built for a
  list optimiser; the question Clay actually asked from his phone was "a list
  of models, how many I have, then how many battle ready", which was a curl
  piped through python. `?fields=name,owned,battle_ready&format=csv` is now
  three columns and nothing else. It narrows rows only — the envelope
  (`army`, `stages`, `generated_at`) is unchanged, so adding it to a URL never
  makes a consumer rewrite how it reads the reply. An unknown name is a 400
  listing the valid ones, never a quietly missing column;
  `collection.EXPORT_FIELDS` is the single list the route, the CSV header and
  that message all read.
