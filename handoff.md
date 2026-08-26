# Handoff

## 1 · Goal

Six PRs, and only the first was asked for as a feature. The rest came from Clay
using the app on his phone and saying what was wrong with it, or from asking
him what to do next and building the answer.

The thread, in order:

1. **The shopping list was over-buying** (#48). Two lists wanting the same unit
   raised the wishlist twice.
2. > "The filtering on the factions is still not working properly."

   Third round on the same complaint, and this time the cause was a natural key
   containing a derived value (#49).
3. **List validation** (#50) and **the backlog screen** (#51), both chosen from
   the spec §9 audit.
4. > "I believe the backup drive is the T7, can you give the the script to make
   > it the backup for the DB and photos?" … "Yes add the last backup date to
   > the Home Screen."

   The nightly cron, then the home-screen line that breaks its silence (#52).
5. **The shopping list answering in boxes** (#53), chosen from §9 as the last
   big gap in the buying half of the loop.

## 2 · Current state

**`main` is green at `7a75437`** — PR #53 merged 18:31 UTC on 2026-08-26.
**791 tests**, ShellCheck clean, no open PRs, no branch in flight.

**Deployed state on bastion is unknown and probably behind.** Clay deployed
once mid-run — his screenshot read `kill team operatives: 1450, under Orks: 23`
— but that was before #50 through #53, and **he has never run the Kill Team
importer**, which is the only thing that fixes the faction filtering. Assume
nothing since #49 is live until he says otherwise.

### What landed

**#48 · The wishlist deduplicates on the maximum, never the sum.** Ten Boyz for
Saturday and twenty for Sunday is twenty to buy — the same twenty field either
game. It said thirty for months, with a test asserting it and a comment
admitting the test pinned the behaviour rather than blessing it. `raise_wishlist`
tops up a shared `_raised_pool` and claims out of it. That makes one model
answer several lists, which `models.wishlist_source_list_id` cannot express, so
**migration 012 adds `wishlist_claims`**: the column marks *the pool*, the table
records *which lists need it now*. Keying the pool on the column rather than a
live claim is what stops a shrinking list ejecting models and having the next
raise buy them again.

**#49 · A natural key may not contain a derived value.** `datasheets.bsdata_id`
was `kt:{edition}:{faction}:{entry}` while the comment beside it said "edition,
team and entry id are all stable" — and the faction is the one part that is
not. Every time a team's allegiance was worked out the key moved with it: the
importer could no longer find its own row, inserted a second, and left the first
on the old faction. Re-importing made **86 duplicate operatives**. Keyed on the
team now, with `_legacy_row` healing an existing database in place. Two more
fixes rode along: `self_categories` reads the 2021 catalogues' own inline
categories, and `canonical_names` files every printing under the newest
spelling. Extra copies from a re-import that already ran are **reported, never
deleted** — any of them may carry Clay's models.

**#50 · List validation has three states, and the third is the point.**
`problem` is definite, `review` means a check could not run, `ok` means every
check ran and passed. Two would have been a lie: 415 of 1,445 datasheets carry
no `min_models`/`max_models`, and an allied detachment looks exactly like a
faction mistake. A badge saying "legal" while a third of the checks were skipped
is worse than no badge, because it gets believed.

**#51 · The backlog measures work left, not models left.**
`effort_left = effort × steps ahead / steps from the start`. Ten Boyz on sprue
is 10.0, the same ten needing a final check is 1.7, one untouched Knight is 8.0
and beats both. `/paint` and `/backlog` stay separate — one is for a wet brush,
the other for deciding beforehand.

**#52 · The home screen says when the last backup finished**, because cron's
failure mode is silence. `backup.sh` writes `data/.last-backup` as its **final**
act, so a run that dies half way leaves the marker at its old value. A marker
rather than the snapshots themselves: the container mounts only `./data` and
`./.env`, so `/mnt/t7` does not exist from inside it and statting `BACKUP_DIR`
would work perfectly in development and report "no backups, ever" on the only
machine that matters.

**#53 · The shopping list answers in boxes, because a shop does not sell seven
Boyz.** `shopping.py` and `/shopping` invert `kit_templates` against the
wishlist. Greedy and deliberately dull — most missing models first, ties to the
smaller overage — and **not an optimiser**: a real minimum-cost cover needs a
price on every box, most have none, and optimising against `rrp_cents` would
mean optimising against whichever boxes Clay happened to have priced. Bundle
against à la carte is one function run twice, so the comparison cannot drift
from the thing it compares to.

**Prices are three-state for the same reason list validation is.** A total that
quietly skipped the unpriced boxes would read **low**, the one direction a
shopping total must never be wrong in. `partial` is the honest common case and
shows as "at least".

## 3 · Active files

Nothing is in flight. What these six PRs added or reshaped:

- `shopping.py`, `templates/shopping.html`, `tests/test_shopping.py` — **new**
  (#53). 26 tests.
- `backup_status.py`, `tests/test_backup_status.py` — **new** (#52). The
  `backup.sh` marker write is step 6, deliberately last.
- `backlog.py`, `templates/backlog.html`, `tests/test_backlog.py` — **new**
  (#51). No nav entry; reached from the home tile and the paint picker.
- `list_validate.py`, `tests/test_list_validate.py` — **new** (#50).
- `migrations/012_wishlist_claims.sql` — **new** (#48), with a backfill from
  `models.wishlist_source_list_id`.
- `lists.py` — `raise_wishlist`, `_raised_pool`, `_claim`, `wishlist()` all
  reworked (#48). `wishlist()` is now the one source of "what is wanted";
  `shopping._wanted` reads through it rather than querying again.
- `scripts/import_killteam.py` — the key fix, `_legacy_row`, `self_categories`,
  `canonical_names`, `real_factions`, `match_faction` (#49).
- `seed/data/killteam_factions.yaml` — Clay's reviewed table, with his
  2026-08-25 and 2026-08-26 decisions recorded inline.
- `CLAUDE.md` and `warhammer-tracker-spec.md` §9 — updated by every one of the
  six.

## 4 · Changes made

All six merged to `main`: #48, #49, #50, #51, #52, #53. Test count 696 → 791.

## 5 · Failed attempts

**"A check that isn't checking" happened five more times this run**, which is
now the single most recurring failure mode in this repo. Worth stating the tell
plainly: **the sabotage run prints nothing.** If a teeth check passes, the check
proved nothing and has to be re-aimed at the failure mode the test is *about*.

The five:

- **A vacuous precedence test.** `test_the_game_system_still_wins_over_a_catalogue_s_own_categories`
  passed under sabotage because the fixture catalogues had no inline categories
  at all. Rewritten with a catalogue that links `fac-ork` *and* declares
  `Aeldari` inline.
- **A faction sabotage that only changed message text**, so nothing about
  `_check_faction` was under test. Redone against the real signature; then four
  tests failed properly.
- **A `backup_status` sabotage that passed for the wrong reason.** I made the
  corrupt-marker path return the *real clock*, and the future-timestamp guard
  caught it — so the test went green while the fail-open case, the one that
  actually matters, stayed unchecked. Re-aimed at a plausible *recent* date it
  bites.
- **A `shopping` test that proved nothing about the filter it named.** It
  asserted an empty template never reaches a plan — but an empty box covers
  nothing, so the cover skipped it whether or not the filter existed.
  Retargeted at `_boxes` directly.
- **A too-broad sabotage that produced misleading collateral.** Forcing
  `state = PRICED` in `shopping._summarise` failed twelve tests, ten of them
  from `_saving` then crashing on `None - None` — an artefact of my own
  inconsistent edit, not a signal. Narrow sabotages one at a time after that.

**The last of those found a real hazard.** The zero-coverage skip in
`_best_box` is what makes `_cover` terminate. Remove it and the page **hangs**
rather than failing — the worst kind of bug to ship, because no test suite
reports it as red. There are two guards now and a test pinning the behaviour
rather than either mechanism; with both removed it times out at 124.

**I told Clay several times that `BACKUP_DEST` was the local backup path.** It
is not — `BACKUP_DIR=/mnt/t7/hobby-tracker` is the local snapshot directory and
carries the DB, CSV and photos; `BACKUP_DEST` is an *off-box* `user@host:path`
over SSH. Setting a local path there would have broken the backup. Read
`backup.sh` and `.env.example` before advising on either again.

**`docker-compose exec app` does not work — the service is `tracker`.** Guessed
from the container name and it cost Clay a round trip.

**A crontab line was handed over as if it were a shell command**, so pasting it
produced `0: command not found`.

**I reported PR #53 as closed-unmerged and asked Clay whether it was a
misclick. It had merged.** The API returned `merged: false` five seconds before
the flag settled, and `main` already carried the merge commit. **Check the
branch against `origin/main` before believing a `merged` flag near a state
transition** — the git history is the primary source, the API field is not.

**The app ignores `DB_PATH` from the environment.** `database.DB_PATH` is a
module constant, so `DB_PATH=… python3 app.py` silently runs against
`data/hobby_tracker.db`. To point a dev server somewhere else, set
`database.DB_PATH` *before* importing `app` — `seed_owner()` runs at import.

## 6 · Next steps

**On bastion, and nothing else is blocking:**

- **Deploy and run the Kill Team importer.** `git pull && ./deploy.sh`, then
  `docker-compose exec tracker python3 scripts/import_killteam.py`. The faction
  filtering stays broken until this runs — there is no migration, the fix
  arrives with the importer only. `under Orks` should go 23 → 62.
- **Confirm the backup cron took** — `crontab -l`. The install command was
  given after the `0: command not found` mishap and never confirmed.
- **Revoke the API token pasted into chat.** `scripts/api_token.py --list`, then
  `--revoke <id>`, then mint a fresh one.

**Decisions owed, none urgent:**

- **`BACKUP_DEST` is still unset**, so both copies live on the same Jetson. An
  off-box target is the remaining gap that loses data. Clay asked for a path to
  be wired and has not named a host.
- **Two rules pins have moved** — BSData `13f3c4e5→04c62fcd` (cheap), MFM
  `06754e2f→3c1efe0d` (moves points under existing lists, so a deliberate
  call, not a drive-by).
- **Weekly sweep DST shift** — `0 14 * * 1` → `0 15 * * 1` after 1 November.
  The existing DST-fix Routine covers Morning Brief, Evening Wind-down and D&D
  Prep but **not** the sweep.
- `barcodes` and `scan_queue` still have no readers. Dropping them destroys the
  codes already linked to templates — Clay's decision, not a tidy-up.

**Still unbuilt from spec §9**, in the order I would take them:

- **Sale candidates** (§8). `models.for_sale_on` and the collection's `own=sell`
  filter exist and nothing *feeds* them: sealed, duplicated, longest unbuilt,
  and not called for by any list is a computable question with every field
  already present.
- **Dashboard** (§5.1) — models finished in the last 30 days from
  `stage_events`, and total spend from `cost_cents`. Cheap, and on the screen
  Clay sees daily.
- **Sharing models between lists** (§7) — the quiet note, *"3 Killa Kans also
  appear in Speed Freeks 1000"*. Explicitly **not** allocation across lists;
  §8's allocation is within one report.
- **Admin overrides UI** (§5.9) — `datasheet_points.manual_override` and
  `datasheets.effort_is_override` are respected by the importer and settable by
  nothing.
- **List export as text and JSON** (§10).
