# Warhammer Tracker — the spec

The source of truth for what this app is for. `CLAUDE.md` is conventions and
hard-won context; this is the goal.

Written from Clay's own description of the loop, 2026-08-22, replacing the
13-step build order that shipped in chat. That order described how to
*construct* the app. It was mistaken for what the app is *about*, which is how
barcode scanning — a task you do once per box and then never again — ended up
as the first item in the navigation.

## 1 · What this is

**One hobby, three activities: buying, building, painting, playing.** They are
not separate tools that happen to share a database. The whole point is the
handoff between them, and the app is worth having only to the extent those
handoffs are seamless.

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

> **Hands off to:** the inventory, and from there the building phase.

### 2.3 · Inventory — what I own and what state it is in

**How many of each, and where each one is.** Two facts, not one:

- **The box** is sealed, opened, or gone. A sealed box carries a resale
  premium; an opened one does not.
- **The models** are on sprue, assembled, primed, painted, based, battle ready.

Clay said "new in box, sealed, built" as one scale. They are two, and keeping
them apart is what makes both "what can I sell" and "what can I play"
answerable.

> **Hands off to:** building — the inventory is where you see what is waiting.

### 2.4 · Building

Sprue to assembled. Clippers, glue, a desk.

> **Hands off to:** painting. What you assembled tonight is what is ready to
> prime, and priming happens in batches, outdoors, on a different day.

### 2.5 · Painting

Primed, base coated, based, battle ready.

**Basing is not universal, and this is a real modelling problem.** A Rhino has
no base. Today the pipeline makes `Base prepared` and `Based` mandatory for
every model, so a vehicle either sits at a stage it can never leave or gets
falsely advanced — and because progress is effort-weighted, a false advance
quietly inflates how finished the collection looks.

**Stages are applicable per model, not universal.** A model with no base is
measured out of five stages, not seven. The applicability is proposed from the
rules data and confirmed by Clay — never guessed, in keeping with §4.

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
- **Every progress figure is effort-weighted**, with raw counts alongside.

## 5 · Where the app stands against this

Measured 2026-08-22, from the code rather than memory.

| Loop step | State |
|---|---|
| 2.1 Own-it check | **Built.** Searching the collection walks the whole catalogue, so "you own none" is an answer. |
| 2.2 Box → collection | **Built, data-blocked.** One scan inserts every model; the *catalogue* of box contents still does not exist, so contents are defined once per box by hand. |
| 2.3 Inventory | **Built.** One row per datasheet: owned, built, battle ready, sealed boxes, wanted. |
| 2.4 Building | **Built.** The ladder's first half, moved a whole unit at a time. Not a separate mode — building and painting are one pipeline. |
| 2.5 Painting | **Built.** Paint mode, session mode, per-unit pipeline, and basing applicability. |
| 2.6 List → gap → wishlist | **Built.** The gap splits buy from paint; the shortfall raises a wishlist tagged with the list that wanted it. |
| 2.7 List import | **Not built.** Blocked on a source — every candidate host is refused by egress policy. |

Walked end to end against a fresh database: 10 of the 11 checks in the loop
pass, the eleventh being 2.7. Buying the shortfall closes the gap, which is the
loop actually closing rather than each step working alone.

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
5. **List import** (2.7) — remaining, and gated on a source.

What is left after that is not new machinery but the things the loop makes
worth having: allocating models between competing lists, a build mode of its
own if building ever wants one, and the kit catalogue that would make 2.2 cheap
in bulk.

## 7 · Known blockers

- **The kit catalogue.** 2.2 needs to know what is in each box. Every candidate
  source is refused by egress policy (`403 to CONNECT`), and no open dataset of
  GW box contents or EANs exists on GitHub. Until then, a box's contents are
  defined once by hand — cheap per box (a searchable picker, counts pre-filled
  from the rules data), expensive across a hundred.
- **`BACKUP_DEST` unset.** Snapshots live on the same machine as the database.
