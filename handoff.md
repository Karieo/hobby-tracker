# Handoff — Session 1

## 1 · Goal

Build steps 1 and 2 of the spec's build order, working and verifiable:

- Project skeleton matching Remndrs conventions
- Full schema as numbered migrations, plus the runner
- Seeded `stages` and `factions`
- `scripts/import_bsdata.py` ingesting BSData into `datasheets` and `datasheet_points`
- An import report: datasheets imported, points rows created, and every entry
  it couldn't resolve

Plus the ten-minute question: does `BSData/wh40k-11e-mfm` carry clean points
tables, and does that delete the points-flattening workstream?

## 2 · Current State

Done and verified. 39 tests pass; the app runs; the importer is idempotent.

**The MFM answer is yes, emphatically.** `wh40k-11e-mfm` is parsed from GW's
official Munitorum Field Manual, MIT licensed, ~600 KB, zod-validated in CI and
re-scraped daily. It ships points **already flattened** — `{models: 10, points:
75}` per legal unit size — so the modifier evaluator the spec braced for
(`set`/`increment` against `greaterThan`/`atLeast`/`equalTo`) was never written.
It is also more correct on unit sizes: BSData models a Boyz mob as "1 Boss Nob +
9-19 Boyz", so deriving 10/20 from it means reassembling the unit from parts.
The manual just says 10 and 20.

It does **not** replace BSData, which still owns the rules-side identity MFM has
no concept of: `bsdata_id` (the stable re-sync key), the datasheet list, and the
keywords the effort heuristic reads. So the split is:

```
datasheets  <- BSData   (identity, faction, keywords, effort)
points      <- MFM      (flat tables, official, licensed)
```

**Import result on the pinned data:**

| | |
|---|---|
| factions seeded | 30 |
| datasheets imported | 1,445 (1,053 current + 330 Legends + 62 Crucible) |
| points rows created | 2,544 (399 of them inherited Chapter listings) |
| current datasheets priced | 1,030 of 1,053 |
| **unresolved** | **31** (8 manual entries with no datasheet, 23 datasheets with no points) |

Spot-checked against the spec's own worked example: Killa Kans import as 3→120
and 6→240 for your 1st–2nd unit, 3→130 and 6→250 for your 3rd+. Boyz import with
min/max 10/20, not BSData's 9–19.

The 31 unresolved rows are each a real human decision, not a bug — naming
variants (MFM "Vyper" vs BSData "Vypers"; MFM's one "Soul Grinder" vs BSData's
four god-specific ones), units BSData has two identically-named datasheets for
(Wolf Guard Headtakers), and units the manual prices only under a god-specific
faction (Khorne Berzerkers is priced under world-eaters, so the Chaos Space
Marines copy is unpriced). They are rows in `unresolved_imports`, printed in the
report, and listed on the index page.

## 3 · Active Files

| File | Role |
|---|---|
| `migrations/001_core_schema.sql` | The whole data model, one migration |
| `migrations/002_seed_stages.sql` | The 8-stage pipeline, Wishlist at 0 |
| `database.py` | Connections, migration runner, shared queries |
| `migrate.py` | Migration CLI |
| `scripts/fetch_bsdata.py` | Pinned-SHA fetch of BSData |
| `scripts/import_bsdata.py` | The importer — read its module docstring first |
| `app.py` | Auth + status skeleton |
| `data/mfm/`, `data/SOURCES.md` | Vendored points data and the licensing note |
| `tests/` | 39 tests, mostly guarding the "never guess" rules |

## 4 · Changes Made

**Schema.** The full spec data model landed in one migration rather than only
the tables steps 1–2 need, so later steps add rows and views instead of
reshaping tables under hand-typed data. Enums are `TEXT` with `CHECK`
constraints so a typo fails at write time. Money is integer cents.

**Three columns the spec doesn't have**, each because the data would otherwise
be lost:

- `datasheet_points.tier_min` / `tier_max` — 11th edition's Requisition
  Thresholds. Killa Kans cost 120 as your 1st–2nd unit and 130 as your 3rd+.
  The spec predates the mechanic. v1 reads the tier containing 1 and ignores
  the rest; dropping the rows at import time would be unrecoverable.
- `datasheet_points.faction_id` — which faction is paying. NULL means the
  datasheet's own faction. There is **one** Repulsor Executioner datasheet, and
  it costs a Black Templar 255 and a Blood Angel 230. Both are true. Without
  this column one of them gets silently overwritten.
- `datasheets.variant` — BSData suffixes alternate printings in brackets:
  `[Legends]` (deprecated) and `[Crucible]` (a game mode). Kept and flagged
  rather than imported as if current, and not expected to carry points.

**`datasheets.effort_is_override`** protects a hand-tuned effort score from the
next re-sync, mirroring what `manual_override` does for points.

**BSData is fetched, not vendored.** The spec says vendor it and pin the SHA.
It is pinned (`13f3c4e5`) and nothing is fetched at runtime, but the checkout
is gitignored and `scripts/fetch_bsdata.py` pulls it, for two reasons: it is
65 MB that would live in this repo's history forever, and `wh40k-11e` ships no
licence file at all. The spec's own rule is "do not redistribute the data or
publish the app publicly with it baked in", and not baking it in is the
cleanest way to honour that. MFM is MIT and 600 KB, so it is committed. See
`data/SOURCES.md`. **Flagging this as a deliberate deviation** — say the word
and I'll commit the JSON instead.

**Migrations diverge from Remndrs on purpose.** Remndrs builds its schema
idempotently and patches it with an ad-hoc `_migrate()`. The spec asks for
numbered SQL, and this data has no upstream source, so an ordered recorded
history is what makes a restore trustworthy. Everything else — flat module
layout, lazy `os.getenv`, bcrypt + session cookie, `before_request` allowlist,
per-IP login throttle, ProxyFix for the tunnel — follows Remndrs.

**`backup.sh`** goes slightly beyond the spec: `sqlite3 .backup` snapshot (never
a file copy), **plus an integrity check on the snapshot**, plus the weekly CSV
export written on every run, plus the off-box copy. A backup nobody verified is
a backup nobody has.

## 5 · Failed Attempts

**Deriving unit sizes from BSData `constraints[]` — abandoned.** The obvious
read is wrong twice over. The top-level `max 3, field: selections, scope: force`
on Killa Kans is a force-org limit, not a unit size. And the real size
constraint lives on `selectionEntryGroups`, where a Boyz mob reads "9–19" —
because the Boss Nob is a separate mandatory entry. Anything built on that
would have quietly produced 9- and 19-model Boyz units. Sizes now come from the
manual's cost rows, which state 10 and 20 directly.

**A global name join — measured, then rejected.** Joining MFM to BSData on name
alone matches 1,459 of 1,466 current units, which looks like a finished job. It
isn't: 35 names carry genuinely different points per faction. A global join
gives a Blood Angels Repulsor Executioner the Black Templars price and nothing
ever flags it. The join is faction-scoped, and the faction mapping was built by
auto-matching catalogue filenames first (30 of 46) and hand-checking only the
16-file residue.

**Base-roster-only group fallback — wrong, produced 447 false unresolved rows.**
`groupTitle` means three different things: a Chapter inside space-marines
("Ultramarines"), a sub-faction inside a larger one ("Harlequins" under
aeldari), and a successor's shared roster ("Space Marines" inside
black-templars). Falling back only to the base roster missed the latter two
entirely and buried the real problems. The search now widens: group hint →
base roster → any group in the faction, with the last step accepting only a
*unique* hit.

**Filtering sub-models on "has a `pts` cost" — over-filtered, dropped real
datasheets.** BSData's `type: "model"` covers both "Warboss" (a datasheet) and
"Burna Boy" (one of the models inside a Burna Boyz mob), and letting the latter
through would put "Burna Boy" in the picker next to "Burna Boyz". But requiring
a `pts` cost also dropped Firestrike Servo-Turrets, Convergence of Dominion and
Kapricus Defenders — real `type: "unit"` datasheets that BSData leaves costed
`[]`. The check now only applies to `type: "model"`, and accepts a `Faction:`
keyword as evidence too.

**`sorted()` on the manual index — crashed on the first run.** Keys are
`(slug, group_or_None, name)` and `None` doesn't compare to `str`. Trivial, but
worth knowing that key tuples here carry a nullable middle element.

Cumulatively the unresolved count went 604 → 551 → 197 → 127 → 38 → **31**.
Every one of those steps was a real bug that would have shipped silently wrong
data, not noise reduction.

## 6 · Next Steps

**Step 3 — armies, kits, units, models CRUD.** The one that matters is the
interaction shape, not the tables: "advance all to next stage" as the primary
one-tap control, "advance N of them" second, individual models available but
never required. Painting session mode is part of step 3, not a follow-up — it
is the screen that decides whether this gets used at the desk or becomes an
admin chore afterwards.

**Two open questions for Clay:**

1. **BSData fetched vs. committed** (see §4). Fine as is, or commit the 65 MB?
2. **Chapter-specific pricing at list-build time.** The data is captured
   correctly now (`datasheet_points.faction_id`), but step 8 will have to
   decide *which* price a list uses when a Blood Angels list fields a
   Space-Marines-owned Repulsor Executioner. Not blocking anything before then.

**Worth doing before the data grows:** run `backup.sh` once and verify a restore
while the database is still small and losing it wouldn't matter. The spec is
right that this is load-bearing, and it is much easier to test now than later.

**Do not build past step 5.**
