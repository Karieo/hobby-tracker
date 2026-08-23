# Handoff

## Goal

Three goals across this session, in order.

1. **Get Clay's collection into the app** as easily as possible. He is starting
   from zero and the collection is **mostly still boxed**.
2. **Know what exists, not just what he owns** — "find all of the models and box
   sets out there that someone could add to their collection", plus a **weekly
   sweep for new releases**.
3. **Make it look like the design he drew.** Clay built `Tracker Wireframes` in
   Claude Design and asked for it in the app: the theme, the fonts, and the
   three loop screens. Everything after that came out of using it — the ramp,
   the desktop layout, USD and Central time, the doubled faction list, the
   camera's HTTPS guard, adding a set by name, and the last unbuilt loop step.

He may share the app with other people later; the form is undecided, so nothing
here builds toward accounts and nothing here makes that harder.

## Current state

Working and browser-verified at 430px and 1440px. **522 tests green.**

**The loop closes.** All eleven end-to-end checks pass against a fresh
database: buy → scan → onboard → build → paint → list → gap → wishlist → buy,
and now a list can arrive by paste as well as by hand.

Merged: **#17**–**#24**. On the branch and pushed, no PR yet: the list-import
commit (`f027ca7`).

| Piece | State |
|---|---|
| Rules data (every unit in the game) | 1,445 40k datasheets + 1,450 Kill Team operatives. **Already complete**; re-run the two BSData scripts to refresh |
| Box catalogue (contents, EANs) | 9 entries. Grows weekly + from scanned codes. No bulk source exists |
| Weekly sweep | Armed: Mondays 14:00 UTC (9am CDT), fresh session, push notification |
| Scan → sweep → identify → adopt | Built, verified |
| Paste import `/add` (models) | Built, verified |
| Catalogue `/catalogue` | Built, verified |
| Add a set by name `/sets/new` | Built, verified |
| **List import `/lists/import` (spec §2.7)** | **Built, verified.** The last loop step |
| Theme, fonts, desktop layout | Built. `Tracker Wireframes` ground, Pirata One + Special Elite served locally |

## The three doors, and why there are three

Every way data gets in is the same bargain: something pre-fills a form, Clay
confirms it, nothing is saved before he does.

- **Barcode** — the box identifies itself. Fastest, and the only one that
  supplies an EAN.
- **Name** — `/sets/new`, for a box with no code to hand or a code the camera
  will not read.
- **Paste** — `/add` for models, `/lists/import` for a list. Needs nothing
  fetched, no format supported, no site reachable. It is the door that always
  works, which is why §2.7 turned out not to be blocked after all.

## Active files

- `bulk_add.py` — `parse_lines`, `match_lines`, `commit`, `commit_as_list`
- `templates/list_import.html`, `templates/list_import_preview.html`,
  `static/js/list-import.js`
- `app.py` — `/lists/import`, `/lists/import/preview`, `/api/lists/import`,
  `/sets/new`, `money` filter, `public_url`
- `static/css/app.css` — the theme tokens, `.ramp`/`.rung`, the desktop layer
- `collection.py` — `home_summary`, `stalled_unit`, `retreat_unit`
- `migrations/007_merge_duplicate_factions.sql`
- `seed/data/kits/*.yaml`, `seed/derived_kits.py`, `docs/weekly-sweep.md`

## Changes made

**#17** — collection screen made actionable; queue sweep; `/box/<code>` and
identify mode; adopt-all by barcode; `/add` paste import; `names.py`.

**#18** — catalogue split per faction with duplicate refusal; barcode learning
from scans; `docs/weekly-sweep.md`; weekly Routine armed; 4 products researched.

**#19** — `/catalogue` browse/search/filter; want a box (records *which* box);
own a box; search covers units inside a box, not just its name.

**#20–#21** — the Tracker Wireframes ground: theme tokens, locally-served
fonts, every radius zeroed, home/collection/unit rebuilt, the **ramp** with its
−1 rung (`retreat_unit`, which did not exist), and a desktop layout instead of
a stretched phone one.

**#22** — USD and `America/Chicago` throughout, via a `money` filter and a
configured timezone; then migration `007` **merging** the duplicated factions
rather than labelling them, because the Kill Team importer had created a second
Adepta Sororitas rather than reusing the 40k one. Verified first that the
faction-scoped points join could not be corrupted by the merge.

**#23** — the camera's HTTPS guard now hands over the tunnel address instead of
telling Clay to go find it.

**#24** — add a set by name.

**`f027ca7`** — **list import.** `parse_lines` learned points annotations
(`(95)`, `[200pts]`, `- 185 pts`, `: 105`) and section headings (`+ HQ +`,
`Total: 645pts`); `commit_as_list` creates a list rather than models, and
**discards the pasted points** — this app prices from the Munitorum manual, and
a number out of someone else's app would quietly outrank the official one.
Near-miss suggestions now rank by `difflib` similarity.

## Failed attempts

- **Tried to bulk-import a box catalogue. There isn't one.** GitHub search for a
  GW product/EAN dataset returns **zero** repos. Every candidate host is
  egress-blocked. Do not go looking again without new information.
- **Called §2.7 "blocked on a source" for weeks.** That was true of *fetching* a
  list and never of *pasting* one. A blocker recorded against a step, rather
  than against the specific approach that hit it, hides work that was always
  doable. It took two days to build once actually attempted.
- **Shipped a repaint that silently matched nothing.** `repaintPipe` selected by
  a class the ramp markup no longer had, so advancing a unit moved the data and
  not the screen — Clay found it, not the tests. Fixed by matching on
  `data-stage`, plus a `location.reload()` guard so a future mismatch degrades
  to a refresh instead of a lie, plus a regression test proved against the
  broken markup first.
- **The first "proof" that regression test had teeth was invalid** — it failed
  on a login redirect, not on the missing attribute. These route tests only pass
  as a suite; a single-test run fails in the client fixture's login.
- **`_POINTS` was too greedy**: `Boyz x20` became a unit called `Boyz x` with a
  count of 1. A bare trailing number is a *count*. Points must announce
  themselves — brackets, the word, or a separator.
- **Near misses ranked alphabetically inside prefix buckets.** "Killa Kanz"
  suggested Kill Krusha, Kill Rig and Kill Tank — never Killa Kans. Someone
  tapping the first suggestion gets the wrong datasheet, which is the exact
  silent wrong answer the unresolved-line machinery exists to prevent.
- **"Verified" the camera guard against `localhost`.** localhost *is* a secure
  context, so the check passed and proved nothing. Re-run against a real LAN IP
  with `host='0.0.0.0'`.
- **A Playwright *live* locator (`.cat-row:has(.cat-want)`)** re-resolved after
  the class changed and reported a working feature as broken. Use
  `[data-template="N"]`.
- **Ran the drive script against the wrong database.** `DB_PATH` is **not** an
  env var this app reads. Set `db.DB_PATH` in a launcher (`scratchpad/serve.py`).
- **`ps | grep` matching the shell's own command line** → exit 144, repeatedly.
  Use `ps -eo pid,args | awk '/pat/ && !/awk/'`.
- **Numbered a migration 006 when 005 did not exist.** Renumber before applying.

## Next steps

1. **On bastion**: `git pull && ./deploy.sh && python3 migrate.py` (**007 is
   pending there**), and add to `.env`: `TIMEZONE=America/Chicago`,
   `CURRENCY=USD`, `PUBLIC_URL=https://<tunnel>` — the last one is what the
   camera guard hands over.
2. **The weekly sweep needs `0 15 * * 1` after 1 November**, when CDT ends. The
   existing DST-fix Routine does not cover this one.
3. **Do a real shelf session.** ~100 boxes against test fixtures is the only way
   to find what is still slow.
4. **`BACKUP_DEST` is unset** — snapshots sit on the same Jetson as the database.
5. **Two incomplete catalogue entries**: the Daemon Prince and GSC Broodcoven
   premium kits each list only half their contents. Needs Clay's decision on the
   variant/split.
6. **The rest of the design.** Armies, lists, the gap report, scan review, kit
   templates, kit detail and sign-in still carry the old structure on the new
   ground.
7. **Two lists wanting the same unit raise both shortfalls.** Saturday wanting
   ten Boyz and Sunday wanting twenty puts thirty on the wishlist, not twenty —
   nothing yet says the same models could serve both. They now share one
   wishlist line, so the total is in plain sight where it used to be split
   across two rows. Allocating models between competing lists is the fix and it
   is unbuilt; `test_two_lists_short_of_the_same_unit_share_one_line` pins the
   current behaviour so a later fix has to face it.
8. **The sharing question** decides whether the collection needs a user column.
   Nothing built so far assumes single-user in the data.
