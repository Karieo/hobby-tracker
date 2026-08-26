# Handoff

## 1 · Goal

Seven PRs, and not one of them was a feature request in the ordinary sense.
They came from Clay opening the app on his phone and saying what was wrong, or
from asking him what to do next and building his answer.

The thread, in order:

1. **`/shopping`** (#53) — chosen from the §9 audit as the last big gap in the
   buying half of the loop. Merged before this run's handoff was written.
2. > "No way to delete list."

   A screenshot of an empty Imperial Knights list. The delete control was the
   easy half; **the pool bug behind it** was the reason it could not just be
   wired up (#54).
3. **`/sale`** (#55) — chosen from §9. `models.for_sale_on` had existed since
   migration 011 and nothing ever *fed* it.
4. > "There are only 2 list battle sizes for list. Unit composition shall not
   > exceed."

   A screenshot of the 40,000 app's own picker (#56).
5. > "Can you do a full code review" … "fix 1, 2 and 4" … "What else?"

   A full-repo sweep, eight findings, shipped in two halves (#57, #58).
6. > "How often are we updating all of the data." … "New points came out
   > today."

   Investigated; the app was already current. The *check* was not (#59).
7. > "I'll play games in another app, it's called Battlebase. So I just need to
   > be able to track models here… I do need to edit lists. Spend and kits are
   > obsolete."

   List editing, and the money off every screen (#60, **open**).

## 2 · Current state

**`main` is green at `1a282e3`** — PR #59 merged. **PR #60 is open and draft**
on `claude/new-session-8l17p6` at `3392987`, two commits, all four CI checks
green, `mergeable_state: clean`. **864 tests**, ShellCheck clean.

**Deployed state on bastion is still behind, and one item is still blocking.**
Clay has **never run the Kill Team importer**, which is the only thing that
fixes the faction filtering — there is no migration, the fix arrives with the
importer alone. His Orks filter reads 23 where it should read 62.

### What landed

**#54 · Deleting a list re-points its wishlist models rather than clearing
them.** `DELETE /api/lists/<id>` and `lists.delete_list` had both shipped and
nothing called either — the endpoint-with-no-caller pattern, third instance.
Wiring the button up first meant fixing what it would have done: `delete_list`
predates migration 012 and cleared `wishlist_source_list_id` for every model the
list raised, which marks *the pool* — so deleting one list dropped models
**another live list still claimed** and the next raise bought them again.
Measured: Saturday raises 20, Sunday claims the same 20, delete Saturday and the
pool is empty with Sunday's 20 claims standing; Monday's raise took the wishlist
to 40. The exact over-buying `wishlist_claims` exists to stop, through the
delete door. It cannot simply keep the old id — the column is a plain
`REFERENCES` with no `ON DELETE`, so a surviving reference restricts the delete
outright, which is *why* the blanket clear was there.

**#55 · The sale screen proposes; the shortlist decides.** Two sections, because
a sealed box and a loose model are different objects. **"Needed" is the maximum
any one list asks, never the sum** — the same rule the wishlist deduplicates on.
Summing is the dangerous direction here: it inflates what looks needed, hides
real surplus, and makes the screen recommend nothing, which is a quiet failure
nobody notices. A sealed box is **held back whole** if any datasheet in it is
wanted, and held-back boxes are *named* — "nothing sealed worth selling" and
"four sealed boxes and every one is spoken for" are different facts.

**#56 · Battle size is a picker of two, and the two came off a screenshot.**
`lists.BATTLE_SIZES` is Incursion (1000) and Strike Force (2000), taken from the
40,000 app's own picker — not from anything a model recalls about the game,
which is the distinction this repo cares most about. No migration:
`army_lists.points_limit` already holds the number. **And `SELECT l.*` collided
with `AS points_total`** — `army_lists` has a column of that name, so the
aggregate came back second and `sqlite3.Row` hands `dict()` the first. Every
list on `/lists` showed the word "None" where its points belong.

**#57 and #58 · The eight review findings.** The one that mattered:
`home_summary` had `_ACTIVE_UNIT` and no `_LIVE_MODEL`, so the home screen's
headline went on counting sold models while every other surface had dropped
them. **CLAUDE.md had cited the test that would have caught it, by name, for
months before it existed.** It exists now and walks every ownership surface.
Also: `/lists` showing 0 for an unpriced list, the Kill Team points nag, a way
to sign out at all (`POST /api/auth/logout` shipped in the first commit and
nothing ever called it), and two more disposal filters.

**#59 · A moved pin is not stale data, and the check now says which.** Measured,
both directions wrong: the MFM pin had moved on a `chore(deps)` CI bump with the
points files **byte-identical**, and BSData had moved by 35 genuine data commits
— 805 inserted lines in `Orks.json` alone — that changed **two rows** of what
this app imports, both keyword-only, in armies Clay does not play. `check_pins`
separates `moved` from `stale`, and for BSData and Kill Team `stale` is **None,
not False**: "not established" and "no" are different answers and only one is
honest.

### What is open on #60

**Lists are editable, and the paste behind them is not.** `update_list` takes
`**fields` against a `_LIST_FIELDS` allowlist. `raw_text`, `source_format` and
`points_total` are **outside it on purpose** — they record what was *pasted*,
`reparse` reads them, and a form that could rewrite them would let a typo erase
provenance nothing can rebuild.

**There is no money anywhere on screen.** No RRP field on `/templates`, no "paid
£X" on `/sale`, no cost on `/gallery`, no totals on `/shopping`. `rrp_cents` and
`cost_cents` **stay in the schema, unread** — the same bargain migration 010's
disposal columns made. But the *code* behind the prices went rather than being
left computing: a figure nothing renders drifts out of step with the catalogue
and nobody notices. What survived is the half that was never about money —
which boxes, and how much spare they arrive with. The overage is now the only
cost anything reports, which is why it sits directly behind coverage in the
tie-break.

## 3 · Active files

**In flight on `claude/new-session-8l17p6` (PR #60):**

- `lists.py` — `update_list`, `_LIST_FIELDS`, `battle_size`, `BATTLE_SIZES`,
  `points_headline`, `delete_list`.
- `templates/list.html` — the `<details id="edit-list">` form and the
  `#delete-list` danger button.
- `shopping.py`, `templates/shopping.html` — the price apparatus removed.
- `templates/sale.html`, `gallery.html`, `template.html`, `templates.html` —
  money removed.
- `CLAUDE.md` — the two sections promising price honesty **deleted rather than
  edited**; they described behaviour that no longer exists.

**Landed this run and worth knowing about:**

- `sale.py`, `templates/sale.html`, `tests/test_sale.py` — **new** (#55).
- `templates/base.html` — the footer owner name is now the sign-out button
  (#58). No nav entry: it sits where you would look to check who you are
  signed in as.
- `rules_data.py` — `mfm_upstream()` reads the MFM's `DATA-CHANGELOG.md`;
  `check_pins` separates `moved` from `stale` (#59).
- `collection.py` — `home_summary`, `list_for_sale`, `stalled_unit` all gained
  the disposal filter (#57, #58).
- `tests/test_collection.py::test_every_ownership_surface_drops_a_disposed_model`
  — **new**, and the one to add to when a new screen counts ownership.

## 4 · Changes made

Merged to `main`: #54, #55, #56, #57, #58, #59. Open: #60. Test count
791 → 864.

## 5 · Failed attempts

**A teeth-check harness that proved nothing, five times over.** My bash helper
passed `-k 'a or b'` through an unquoted `$2`, which word-split into a broken
pytest expression; pytest errored, and my grep for `^FAILED` found nothing — so
five sabotages "passed" and I nearly believed them. Quoted properly, all five
bit. **The tell is the same one this repo keeps re-learning: a sabotage run that
prints nothing has not checked anything.** Read the sabotage output, do not
grep it.

**A vacuous test I wrote and then deleted.** It asserted `/login` carries no
sign-out control — but `login.html` is standalone and does not extend
`base.html`, so it could only ever pass. Deleted rather than kept.

**Two commit messages with wrong test counts** (862 for 854, 871 for 873).
Amended before opening the PRs. Count, do not estimate.

**`delete_list`'s first fix hit `FOREIGN KEY constraint failed`** and that
failure was the useful part — it is *why* the destructive blanket clear existed.
Re-pointing rather than retaining was the fix.

**An autouse `_no_network` fixture shadowed `mfm_upstream`** for the three tests
that exercise it. Fixed by capturing `_REAL_MFM_UPSTREAM` before the fixture.

**`shopping.html` left unbalanced** after I removed a block containing an
`{% endif %}`. Caught with an opens/closes balance check across every touched
template — worth running whenever a block is removed rather than replaced.

**Two bugs found by rendering the real screen, not by a green suite.** The sale
screen offered the same sealed Gorkanaut box twice — once as a box, once as "2
spare" models that cannot be sold loose anyway. And the list index showed the
word "None". **Neither had a failing test; both were obvious on sight.** Render
the page.

**I misreported PR #53 as closed-unmerged and asked Clay if it was a misclick.**
It had merged five seconds earlier. Every check-in since verifies against
`origin/main` with git — the history is the primary source, the API's `merged`
flag near a state transition is not.

**I misquoted the BSData target SHA** as `04c62fcd` from a stale note; the live
check said `46d8cc50`. Do not quote a pin from memory when a script reads it.

**A stop-hook "1 unpushed commit" warning was a false positive** — a stale local
ref for a branch GitHub deleted on merge. `git fetch --prune` cleared it.

## 6 · Next steps

**On bastion, and the first is still blocking a complaint Clay has raised three
times:**

- **Deploy and run the Kill Team importer.** `git pull && ./deploy.sh`, then
  `docker-compose exec tracker python3 scripts/import_killteam.py`. The faction
  filtering stays broken until this runs. `under Orks` should go 23 → 62.
  (The service is `tracker`, not `app`.)
- **Confirm the backup cron took** — `crontab -l`. Never confirmed after the
  `0: command not found` mishap.
- **Revoke the API token pasted into chat.** `scripts/api_token.py --list`, then
  `--revoke <id>`, then mint a fresh one.

**Floated, not commissioned:**

- > "I guess I could keep track of wins by list in the app just for posterity."

  Thinking aloud, and treated as such. Do not build it without asking.

**Decisions owed, none urgent:**

- **`BACKUP_DEST` is still unset**, so both copies live on the same Jetson. An
  off-box target is the remaining gap that loses data. Clay asked for a path to
  be wired and has not named a host.
- **Weekly sweep DST shift** — `0 14 * * 1` → `0 15 * * 1` after 1 November.
  The existing DST-fix Routine covers Morning Brief, Evening Wind-down and D&D
  Prep but **not** the sweep.
- **The rules pins.** `check_rules_pins.py` now reports `moved` without crying
  stale, so there is nothing to react to — bumping either is a deliberate call.
- `barcodes` and `scan_queue` still have no readers. Dropping them destroys the
  codes already linked to templates — Clay's decision, not a tidy-up.

**Still unbuilt from spec §9**, in the order I would take them:

- **Dashboard** (§5.1) — models finished in the last 30 days from
  `stage_events`. The spend half is dropped; #60 took the money off the screens.
- **Sharing models between lists** (§7) — the quiet note, *"3 Killa Kans also
  appear in Speed Freeks 1000"*. Explicitly **not** allocation across lists.
- **Admin overrides UI** (§5.9) — `datasheet_points.manual_override` and
  `datasheets.effort_is_override` are respected by the importer and settable by
  nothing.
- **List export as text and JSON** (§10).
- **Importing a list from a file or a URL** (§2.7) — gated on a source; every
  candidate host is refused by egress policy. Pasting never was.
