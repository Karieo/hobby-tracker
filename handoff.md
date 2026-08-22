# Handoff

## Goal

Get Clay's collection into the app as easily and seamlessly as possible. He is
starting from zero, and the collection is **mostly still boxed**. Mid-session he
added that he may share the app with other people and wants them to be able to
add models easily — form undecided, so nothing here builds toward accounts, and
nothing here makes that harder later.

## Current state

Working, browser-verified end to end at phone width, 408 tests green.

The onboarding day now runs: **scan the shelf without stopping → one tap
onboards the whole queue → pick a box up and scan it to say what's in it → that
answer fills in every copy already recorded.** Models with no barcode left to
scan get pasted in as a list.

| Piece | State |
|---|---|
| Queue sweep (`/api/scan/sweep`) | Known boxes confirmed, unknown ones recorded, one tap |
| Box page (`/box/<code>`) | Everything known about one EAN; the define-contents entry point |
| Identify mode (`/scan`) | A decode navigates to the box page instead of queuing |
| Adopt-all (`adopt_all_for_code`) | Contents defined once fill every recorded copy |
| Derived catalogue (`seed/derived_kits.py`) | Researched contents with enforced provenance |
| Paste-import (`/add`) | One line per unit, matched or sent back for a decision |

Spec §2.7 (list import) is the only loop step still unbuilt, still gated on a
source.

## Active files

- `scanning.py` — `sweep_queue`
- `collection.py` — `adopt_all_for_code`; `kits_awaiting_contents` now joins
  `barcodes` to suggest a template
- `bulk_add.py` *(new)* — paste parsing, matching, commit
- `names.py` *(new)* — `norm`/`slugify`, extracted from `scripts/import_bsdata.py`
  and re-exported there so the seeds' imports keep working
- `seed/derived_kits.py` + `seed/data/derived_kits.yaml` + `README-derived.md` *(new)*
- `app.py` — `/box/<code>`, `/api/box/<code>/adopt-all`, `/api/scan/sweep`,
  `/add`, `/add/preview`, `/api/add/commit`
- `templates/` — `box.html`, `add.html`, `add_preview.html` *(new)*;
  `scan.html`, `scan_review.html`, `templates.html`, `collection.html` edited
- `static/js/` — `box.js`, `add.js` *(new)*; `scan.js`, `review.js`,
  `template-form.js` edited
- `tests/` — `test_derived_kits.py`, `test_bulk_add.py` *(new)*;
  `test_shelving.py`, `test_routes.py` extended

## Changes made

**Three onboarding frictions, measured against the real ~100-box journey:**

1. A tap per box → `sweep_queue` does the lot, mirroring the per-row `ready`
   rule so an empty template shelves rather than failing.
2. No way to tell recorded boxes apart → the box *is* the index. Identify mode
   plus `/box/<code>`.
3. Defining contents paid for one box → now pays for every copy carrying that
   code, skipping any already filled in.

**The catalogue Clay asked for.** There is no dataset to import — BSData
publishes the rules, nobody publishes the plastic, and direct fetches are
egress-blocked. But *search answers*, so contents get researched one product at
a time and banked in a seed file with their sources. The importer refuses
unsourced entries, matches unit names against BSData or records them unresolved,
and **holds barcodes to a higher bar than contents**: two independent sources or
the entry ships without one.

**Paste-import** for everything already built or painted. Forgiving about shape
(`20 Boyz built`, `Boyz x20`, `Trukk primed`, bare `Nobz`), unforgiving about
names.

## Failed attempts

- **`WebFetch` is still blocked per-domain.** jb-spielwaren, warhammer.com —
  same `403 to CONNECT` as before. `WebSearch` *does* work and returns enough
  content in its answers to source box contents. That difference is the only
  reason the catalogue got unblocked; do not assume fetch will start working.
- **Researching the Necron Combat Patrol EAN returned two conflicting
  barcodes**, with sources disagreeing about whether they meant the 2021 or
  2023 box. This is what drove the two-source barcode rule. That entry ships
  with contents and no barcode.
- **`contents_source='derived_web'` failed a CHECK constraint.** Rather than
  rebuild the constraint in a migration, the seed uses the existing `seed`
  value — accurate, and the per-template source URLs carry the real provenance.
- **`search_datasheets` is a raw SQL LIKE, so it finds nothing for a typo.**
  "Boyzz" returned zero candidates, which would have left an empty picker on
  exactly the line needing one. `bulk_add._near_misses` falls back to folded
  substring, prefix, then word overlap.
- **Two of my own browser assertions were wrong rather than the code** — the
  review heading is uppercased by CSS, so the literal string never matched
  (the same trap as last session), and I asserted `&amp;` where Jinja emits a
  literal `&` from template text. The template now emits `&amp;` properly.
- **Ran the app against the real database by mistake.** `db.DB_PATH` is a
  module constant with no env override, so `DB_PATH=... python3 app.py` was
  silently ignored. Browser checks now go through a launcher that sets
  `db.DB_PATH` before importing `app`.
- **Toast grammar bug shipped to the browser and back**: "1 line still need a
  datasheet". Caught by reading the screenshot, not by a test.

## Next steps

1. **Merge PR #17 and deploy.** It now carries this whole sprint, not just the
   collection fix it was opened for.
2. **Hand over the unknown codes.** Scan the shelf, sweep, tap *Copy N unknown
   codes* on the review screen, paste them into a session. Each gets researched
   and added to `derived_kits.yaml`; re-running the importer makes every
   recorded copy offer to fill itself in.
3. **`BACKUP_DEST` is still unset** — snapshots live on the same Jetson as the
   database.
4. **Spec §2.7, list import** — the last loop step, still gated on a source.
   Paste-import's parser and picker are most of the machinery it needs.
5. **Two premium-kit decisions still open** (Daemon Prince variant, GSC
   Broodcoven split) and the Wolf Guard Headtakers duplicate-name pick.
6. **Sharing.** If it means "others self-host", this sprint is the feature. If
   it means accounts on Clay's box, that is a schema-and-auth project to plan
   on its own — worth asking which before building anything for it.
