# Handoff

## 1 · Goal

Clay is using the app on his phone for the first time, and every session since
has been driven by what he found when he did. The run this file covers took it
from "the pipeline is on every screen" to a shape he described himself:

> "Collection should just be a summary and add or remove... Paint mode has the
> ramp."

> "I want it to be a journey of my whole hobby life across all models."

Plus one removal he asked for outright: the barcode scanner and the box
catalogue behind it are gone.

## 2 · Current state

`main` is green — 635 tests, ShellCheck clean, CI passing on 3.11 and 3.12.
Everything below is committed on `claude/new-session-8l17p6` and stacked onto
**PR #39**.

**The screens split by job.** The unit page is a summary and a count: a stat
card per stage, photos, "How many", and move-to-army. The ramp — advance,
advance all, per-stage ±1 — lives only in paint mode now, on every screen that
used to carry it. Nickname and notes are gone from the unit page; `update_unit`
writes only the keys it is given, so nothing else blanks them.

**Removing models is possible.** `DELETE /api/units/<id>/models` had shipped
long ago with nothing calling it; the count control now does, and the bottom
rung's `−1` un-owns rather than dead-ends. Removing every model deletes the
unit — a correction, not a disposal, and the screen says which.

**The collection filters.** Faction, stage, points range, owned/wanted, free
text, and seven sorts, all in the query string via a `filter_url` template
global that strips empty values in both directions.

**Photos are a dated log per unit**, added and edited through one `<dialog>`,
with the caption editable after the fact. `backup.sh` and `restore.sh` carry
`data/photos/` in a shared directory beside the snapshots — shared because the
filenames are random and immutable, so thirty snapshots are not thirty copies,
and never rotated.

**`/gallery` is the journey.** Four dated streams merged — pictures taken,
models moving forward, boxes bought, boxes gone — oldest first, grouped by day,
with a photo scrubber above. It is the first thing ever to read `stage_events`,
which has been append-only since the first commit for exactly this.

**The scanner and the catalogue are gone.** See CLAUDE.md's "Scanning
(removed)" for why and for what would have to be true before rebuilding it.
`barcodes` and `scan_queue` survive with no readers, deliberately: dropping a
table destroys the codes already linked to templates, which is Clay's decision
rather than a side effect of deleting a screen.

## 3 · Active files

- `journey.py` — new. Merges the four streams; aggregates stage events per
  day/unit/stage; nets same-day corrections out.
- `photos.py` — `update()` and `timeline()` added.
- `kit_templates.py` — new, extracted from the deleted `scanning.py`.
- `collection.py` — `remove_models`, partial-write `update_unit`, the
  `inventory()` filters and sorts.
- `app.py` — `/gallery`, `PATCH /api/photos/<id>`, `DELETE
  /api/units/<id>/models`, `filter_url`; all `/scan*`, `/box/<code>`,
  `/catalogue` routes removed.
- `templates/` — `unit.html`, `gallery.html`, `_macros.html` (stage icons).
- `static/js/` — `app.js` (the photo dialog), `gallery.js` (new, the scrubber).
- `backup.sh`, `restore.sh` — the photo directory.

## 4 · Changes made

Eight commits, `c5ec3fd` through `4e4fce2`. Each answers one message from Clay,
in the order he sent them.

## 5 · Failed attempts

**Every request Clay made this run found something already built and
unreachable, with green tests throughout.** `DELETE /api/units/<id>` had
shipped in the first commit and nothing ever called it, so "I have no way to
remove models" was true while the route sat there answering. The bottom-rung
`−1` was rendered and dead. `faction_id` was on the datasheet and nothing
filtered by it. `POST /api/units/<id>/models` is *still* in that state. The
tests called the functions; not one asserted that a screen offered the control.
This is now an invariant in CLAUDE.md: grep the templates and `static/js/`
before believing a capability exists.

**`update_unit` would have silently eaten nicknames.** It wrote both columns
every time, so dropping the nickname input meant any notes save blanked the
name. Caught by reading the writer while removing the field, not by a test —
there was no test. Fixed to partial writes; restoring write-both now fails.

**A `<details>` inside the nowrap `.search` flex row crushed the search box to
"Sear".** Found by looking at a screenshot. No test could have caught it and
none was added; the browser pass is the test.

**Removing the scanner swept up `/add` and `/lists/import`.** The route slice I
cut was wider than intended. Caught by my own assertion that `/api/scan` was
gone *and* the paste doors were still there, then restored precisely.

**`AS on` is a syntax error in SQLite** — `ON` is reserved by `JOIN ... ON`.
The column is `happened_on` and mapped to `'on'` in Python.

**Hiding retreats was not enough.** The journey dropped backward moves as
corrections, which left eight Boyz advanced-and-walked-back showing "8 × Boyz —
Base prepared" forever: the mis-tap visible, the fix hidden. Found in the
seeded browser pass, not in the fifteen tests that were already green. Same-day
retreats now cancel the advance they undid.

**A test that passes either way is not a test.** Teeth-checking the netting
found the arrival guard untestable in the obvious fixture, because
`retreat_unit` cannot leave the first owned stage. The real case is a unit that
*arrives* at Painted, which paste-import does — that test bites.

## 6 · Next steps

1. **Deploy to bastion**: `./backup.sh` → `git pull` → `./deploy.sh`. Note
   bastion runs standalone `docker-compose` (v1), hyphenated. Picks up
   migration 009, the nav without Scan and Catalogue, the reworked unit page
   and the journey.
2. **`BACKUP_DEST` is unset** — photos would exist only on the Jetson. This is
   the one thing on this list that loses data if left.
3. **Two rules pins have moved** — BSData `13f3c4e5 → 04c62fcd` (cheap), MFM
   `06754e2f → 3c1efe0d` (moves points under existing lists). Clay's call;
   nothing bumps a pin automatically by design.
4. **`barcodes` and `scan_queue` have no readers.** Dropping them is a one-line
   migration and a decision, not a tidy-up.
5. **The weekly sweep fires at `0 14 * * 1`** — becomes 15:00 local after the
   1 November DST shift.
6. **Spec §9's remaining dropped requirements** — nine still owed a decision
   after export.
