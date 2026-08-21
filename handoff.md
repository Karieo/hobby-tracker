# Handoff — Session 2

## 1 · Goal

Build step 3: armies, kits, units and models CRUD, whole-unit stage advance,
painting session mode, and the army detail view.

Session 1 (steps 1–2 — schema, migration runner, reference-data seed, the
BSData + Munitorum importer) is on the same branch and unchanged.

## 2 · Current State

Done and verified. 97 tests pass, and the app was driven end to end in a real
browser at iPhone width — not just asserted against.

Working:

- **Armies index** with effort-weighted completion bars, plus an **Unassigned**
  bucket that appears only when something is in it.
- **Army detail** — header stats over a unit list, each unit with its own
  stage-coloured bar and its own advance controls.
- **Unit detail** — the full eight-stage pipeline including empty stages, a
  per-stage `+1`, a "set a count" box, and the bulk model selector.
- **Painting session mode** at `/paint` — 358×66px primary target, every tap
  saves, no forms and no navigation.
- **Kits** — add, list, and the disposal lifecycle (owned/listed/sold/traded/
  gifted) with what it actually went for.
- **Datasheet picker** — typeahead against imported datasheets only, current
  printings only.

Measured in the browser: no JS errors, no horizontal overflow at 390px, tap
targets ≥44px, and the picker resolves "Killa" to Killa Kans and Deffkilla
Wartrike with faction, legal size and effort shown.

## 3 · Active Files

| File | Role |
|---|---|
| `collection.py` | Armies, kits, units, models, all stage movement |
| `app.py` | Routes, auth, `_read`/`_write` connection helpers |
| `templates/` | `base` + `_macros`, armies, army, unit, paint, kits, reference |
| `static/js/app.js` | Stage controls, bulk selection, picker, toasts |
| `static/css/app.css` | Mobile-first; the stage palette lives in `:root` |
| `tests/test_collection.py` | 28 tests on the interaction rules |
| `tests/test_routes.py` | 23 tests on wiring, auth and API contracts |

## 4 · Changes Made

**The interaction shape, which is the actual deliverable.** `advance_unit()`
with no arguments moves a whole unit forward one stage — that is what the
primary button binds to, and it is one tap with no selection. With a count it
moves the *least advanced* models, because "six of these ten are primed" must
never ask which six. `from_stage_id` narrows it to one stage for the per-stage
`+1`. Individual model selection exists behind a collapsed list and is never on
the path of a normal update.

**Stage controls patch the DOM instead of reloading.** A full navigation after
every squad loses your scroll position halfway down an army, and that friction
is the thing this app is designed against.

**Effort weighting is enforced by the queries, not by the templates.** There is
a test asserting that 8 of 9 models finished reads as 50%, not 89%, when the
ninth is a Deff Dread.

**Disposals are a status change.** A sold kit keeps its models and its rows;
they leave ownership counts and effort totals via `ACTIVE_KIT_STATUSES`, so
spend history stays correct. `listed` still counts as owned.

**Asset cache-busting** (`?v=<mtime>`), added after a cached stylesheet hid a
CSS fix during this session. On a phone that has had the page open for a week
that would be considerably more confusing than it was here.

## 5 · Failed Attempts

**`set_unit_stage_counts` pulled finished models backwards — and my test
asserted the bug was correct.** The "set a count" box promised in its own hint
text that it never drags anything backwards. It did: candidates were ordered by
stage position ascending, so asking for "2 primed" on a unit of four Battle
Ready models pulled two finished models back. Worse, the test guarding it was
named `..._never_pulls_models_backwards` and its assertions confirmed the
opposite behaviour — a green test that documented the bug.

Fixed properly rather than by rewording the hint: candidates now come from
*behind* the target stage first (least advanced first, matching `advance_unit`),
and only reach forward once nothing is left behind — taking the *closest* model
when they do, so anything finished is disturbed last. Correcting downwards is
still possible, because Clay saying "only 2 are primed" is him fixing the app.
Three tests replace the one bad one.

**The count form defaulted to "20 models at Wishlist".** Landing on the unit
page and hitting Set would have moved an entire painted squad to Wishlist. It
now defaults to the stage most of the unit is already at, so the default action
is a no-op — verified in the browser: submitting untouched says "Nothing to
move". A form whose default answer is destructive eventually destroys something.

**Three controls will not fit on one 390px line.** "Advance all", the stepper
and "Paint" wrapped so that "Paint" dangled alone on a second row. Shrinking
them to fit would have shrunk the one that matters, so the primary action now
takes a full row of its own below 30rem and they share a line above it.

**Two browser passes showed stale output** — first because Flask caches
templates outside debug mode and the server needed restarting, then because the
browser had cached CSS and JS (`304`). The second one is why cache-busting got
added. Worth remembering: a screenshot that looks unchanged after an edit is
more likely a caching problem than a broken edit.

## 6 · Next Steps

**Step 4 — scanner, scan sprint queue, review screen.** Build the manual
template form *first* so onboarding never depends on automation, then EAN-keyed
contents resolution, then photo extraction as the fallback. The constraints that
matter are already in `CLAUDE.md`: ZXing-js as the primary decoder (not the
fallback — WebKit has no `BarcodeDetector` and fails silently on every iOS
browser), a secure context from the tunnel, manual digit entry as a
non-negotiable, and capture split from enrichment so a shelf can be worked
through at the speed of turning boxes over.

`instantiate_template()` already exists and is tested — scanning a known
barcode is meant to call straight into it.

**Still worth doing before the data grows:** run `backup.sh` once and verify a
restore. It has never been executed against a real database.

**Open, not blocking:** which price a list uses at step 8 when a Blood Angels
list fields a Space-Marines-owned datasheet. The data is captured correctly
(`datasheet_points.faction_id`); only the query is undecided.

**Do not build past step 5.**
