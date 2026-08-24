# Handoff

## Goal

One goal this session, arrived at in stages: **build the list gap checker** —
paste a list, and be told what to buy, what to build, what to paint, and what
is already on the shelf. Spec §8 (uploaded as "Section 7"), five commits, each
stopped at for review.

Three things attached themselves to it along the way:

1. **A full review of every spec** — Clay asked for it mid-build: missed
   requirements, a code review, dead code removed.
2. **`GET /api/export/inventory`** — the first of spec §9's ten dropped
   requirements to be discharged, from an uploaded export spec.
3. **Provenance for the rules data** — "how often are the MFM and the
   datasheets imported?" turned out to have no answer anywhere in the app.

Then a second half, all of it from Clay using the app rather than reading the
spec: getting the scanner onto HTTPS, removing models, the ramp's bottom rung,
and filtering the collection. **Every one of those found something that was
already built and could not be reached.**

## Current state

**718 tests green.** ShellCheck clean. Working tree clean at `acfaa39`.
`main` has everything; nothing is in flight.

**Deployed to bastion on 2026-08-24 and migration 008 ran clean**, so the
running app is finally the same app as the repo — schema, gap checker, export,
and the three screens the second half changed.

Merged this session: **#27** (commits 1–2), **#28** (the review pass +
commit 3), **#29** (commit 4, the export, the rules-data work), **#30**
(commit 5).

| Piece | State |
|---|---|
| Gap report `/lists/<id>` (spec §8) | **Built, browser-verified.** Six row states, live on every load, never stored |
| Allocation (`list_allocate.py`) | Built. Two passes, models consumed as they go |
| Resolution (`list_resolve.py`) | Built. Alias → exact → fuzzy → null. Aliases learned from every hand pick |
| Export parser (`list_parse.py`) | Built. GW app + New Recruit + plain; **never drops a line** |
| `GET /api/export/inventory` (§9.1) | Built. JSON or CSV, bearer token from `api_tokens` |
| `/reference` provenance | Built. Which manual priced the database, and whether `data/mfm/` is newer |
| `scripts/check_rules_pins.py` | Built, wired into the weekly sweep. Reports; never bumps |
| Spec §8 and §9 | Written into the repo. Section 7 existed only as a chat upload |
| Scanning over HTTPS | **Live.** `tailscale serve --bg 3100` on bastion; `https://bastion.tail25c97e.ts.net/scan` |
| Removing models | Panel on the unit page, and the ramp's bottom rung |
| Collection filters | Faction, points range, stage, ownership, sort — plus the chips that no longer drop each other |

**Nothing is pending on bastion any more.** That sentence stood here for
three PRs; it is the one line in this file worth deleting rather than editing.

## The bug the whole thing existed to kill

`lists.list_gap` counted ownership per entry with nothing consuming a model
once assigned. A list wanting two ten-model Boyz mobs and one twenty-model mob
matched all three against the same twenty Boyz and reported **fieldable**.
Reproduced against shipped code before a line of the replacement was written:
it needed forty, owned twenty, said `to_buy=0 fieldable=True`.

`list_allocate.allocate` now answers that correctly — short 10 — and
`lists.list_gap` is a name over it, so `raise_wishlist` and the list index
inherit the fix. The wishlist had been reading the same double-counted numbers
and under-asking for exactly the models Clay would have found missing at the
table.

## Active files

- `list_parse.py` — `parse`, three format handlers, `ParsedList`/`ParsedEntry`
- `list_resolve.py` — `similarity`, `normalise`, `resolve_entry`, `learn_alias`
- `list_allocate.py` — `allocate`, `_pass_one`, `_pass_two`, `_candidates`,
  `_summarise`, `_state`, `_army_clause`
- `lists.py` — `list_gap` (now a thin name), `import_list`, `reparse`,
  `add_entry`, `raise_wishlist`
- `rules_data.py` — all three pins, `mfm_meta`, `provenance`, `check_pins`
- `collection.py` — `export_inventory`, `add_or_extend_unit`, `add_models`,
  `buildable_options`, `set_built_as`
- `app.py` — `/api/export/inventory`, `TOKEN_PATHS`, `_bearer_token`,
  `PATCH /api/lists/<id>/entries/<eid>`, `POST /api/lists/<id>/reparse`,
  `POST /api/units/<id>/built-as`
- `migrations/008_gap_checker_schema.sql`
- `templates/list.html`, `static/js/list-report.js`, `templates/reference.html`
- `scripts/api_token.py`, `scripts/check_rules_pins.py`,
  `scripts/report_kit_datasheets.py`

## Changes made

**Commit 1 — what a model is, and what it could be.** Migration 008:
`models.datasheet_id` and `models.is_flexible`, `kit_datasheets`,
`datasheet_aliases`, `army_lists` provenance columns, and `list_entries`
rebuilt (SQLite cannot drop NOT NULL) with `position`, `raw_name`, `points`,
`resolved_by`.

**Commit 2 — read the export, lose nothing.** `list_parse.py`. Detects the
format, drops the preamble, carries points and position. `/lists/import` uses
it now, so a real export's header is dropped rather than reported as four
unknown units; `/add` still uses `bulk_add.parse_lines`, which is right for it
— that one reads a shelf typed from memory and *may* skip a line. The two
share `SECTION_RE`, `TOTAL_RE`, `POINTS_RE` so they cannot drift.

**The review pass.** Ten requirements the 2026-08-22 re-scope dropped without
deciding against, now spec §9. Section 7 appended as spec §8. A whole orphaned
CSS block and `repaintPipe`'s dead `||` fallbacks removed.

**Commit 3 — name to datasheet, or admit it could not.** Alias, exact, fuzzy
above 90 with a 10-point margin, else null. The alias write-back lives inside
`resolve_entry` so no route can forget it.

**Commit 4 — one model, one place.** `list_allocate.py`. Pass 1 spends models
that already are the datasheet, largest requirement first, battle-ready first.
Pass 2 fills the rest from plastic that *could become* it, most-constrained
requirement first, magnetised-and-ready before bare sprues.

**Commit 5 — the report.** `/lists/<id>` rebuilt as §8's view: three stat
tiles, points as a sentence, the unassigned toggle, six `row-<state>` classes,
an inline picker on unresolved rows that teaches the alias table, a "which
models" expansion, and "read it again" to re-parse the kept paste.

**The export.** `GET /api/export/inventory` serves an external list optimiser
as JSON or CSV, authenticated by a bearer token from `api_tokens` — the table
migration 001 created and nothing read until now. **A token reaches
`/api/export/` and nothing else**; widening that is one entry in
`app.TOKEN_PATHS` and should be a decision rather than a side effect.
`scripts/api_token.py` mints, lists and revokes.

**The rules data answered its own question.** All three SHAs moved into
`rules_data.py`; the fetch scripts import them from there. `/reference` shows
which manual priced the database and warns when `data/mfm/` is newer than the
import. `scripts/check_rules_pins.py` asks GitHub whether a pin has aged; the
weekly sweep runs it. Nothing bumps a pin automatically.

### The second half, from Clay using it

**HTTPS for the scanner.** A Tailscale MagicDNS name is not HTTPS until
something listens on 443. `sudo tailscale serve --bg 3100` on bastion, and the
docs stopped naming the Cloudflare Tunnel where they meant a scheme — a bare
Tailscale *IP* over http is what fails, not Tailscale. Also: DEPLOY.md said
bastion ships standalone `docker-compose` and then gave `docker compose …` in
every code block after it.

**Removing models.** `collection.remove_models`, `DELETE /api/units/<id>/models`,
a panel on the unit page, and the ramp's bottom rung. Least advanced first, and
within a stage the most recently added — both point at the extras just typed
in. Removing them all deletes the unit. A **correction, not a disposal**: a
sold kit keeps its models and its spend history.

**The ramp.** The bottom rung's `−1` had always been enabled and inert, because
`retreat_unit` skips the first owned stage. It removes a model now, and
confirms — the only tap on that screen that does. Counts became text; inert
`−1`/`+1` are hidden with `visibility` so the numbers stay in one column.

**Collection filters.** Faction, points range, stage, ownership, sort. Points
are a faction-scoped subquery, not a join — `datasheet_points` has a row per
unit size and per tier, and joining multiplies every ownership count. The chips
had to be rebuilt through `filter_url` first: they hand-built their own query
strings carrying only `q`, so tapping "40k" while filtered to Orks threw the
faction away.

## Failed attempts

- **`token_set_ratio` was the spec's own counterexample.** §8 specifies it;
  built that way, it resolved **"Warboss on Warbike" → Warboss at 100** — a
  wrong confident match on the very example the spec uses to explain why
  aliases exist, because a strict subset scores as a perfect match. Sorting the
  words instead scores that pair 56. `list_resolve.similarity` is one function
  used by both paste doors so this cannot be half-fixed later.
- **`models.datasheet_id` had no writer.** Migration 008 backfilled it and
  `add_models` left every new model null — allocation would have reported a
  full collection as owning nothing. **Second time this exact class:** 008 also
  numbered `list_entries.position` while `add_entry` left new rows at 0. Now in
  CLAUDE.md: *a column a migration fills is a column some writer has to keep
  filling*. Find every INSERT into that table in the same commit.
- **The export grouped by `units.datasheet_id`.** An uncommitted sprue came
  back as both owned *and* buildable — double-counted in the file an optimiser
  would trust. Grouping is `models.datasheet_id` now.
- **Two picker bugs, one already shipped, and a browser found both.**
  `input.closest('form')` was assumed non-null, so the report threw
  `null.addEventListener` at page load; and **both paste-confirmation screens
  have been shipping with no `.results` element at all**, so typing a datasheet
  name there had never done anything. Tests were green throughout. The picker
  now builds its own results list and hoists the form lookup once.
- **`data-entry` on both the row and its resolve button**, so `closest`
  matched the button and the click silently did nothing. The button carries
  `data-resolve`; the handler uses `closest('li')`.
- **A teeth-check I ran was invalid and I nearly reported it.** The "no
  consumption" variant only failed case 9, because case 1 is protected by local
  slicing — it looked like the bug was narrower than it is. Re-ran the correct
  variant. Same shape as the earlier `token_set_ratio` variant that referenced
  `kit_datasheets` before creation and aborted the script. **A proof that a
  test has teeth needs the same scrutiny as the test.**
- **`tests/test_gap_schema.py`'s fixture broke twice** by using current
  helpers against a 007-era database. A migration test may only use SQL that
  existed before the migration, and no application code.
- **Three capabilities existed and could not be reached, and Clay found all
  three by using the app.** `DELETE /api/units/<id>` shipped in the first
  commit with a docstring describing exactly the case he hit; the ramp's
  bottom-rung `−1` was enabled and moved nothing; `faction_id` was read by the
  route, passed to the template, and never rendered as a control. Tests were
  green for all of it. **The test that would have caught any of them is one
  that asserts the screen offers the control**, not one that calls the
  function. CLAUDE.md now says so: grep the templates and `static/js/` before
  believing a capability exists.
- **A guard I wrote did nothing and read as load-bearing.** The points filter
  had `points_low IS NOT NULL AND points_low <= ?`; removing the first half
  changed no test, because a comparison against NULL is already not true.
  Deleted. Worth the two minutes: dead defensive code is worse than absent,
  because the next person keeps it.
- **I told Clay to run `python3 migrate.py` as a deploy step for most of a
  session. Wrong.** `app.py:123` calls `db.init_db()`, which migrates on boot.
  `./deploy.sh` is enough; `migrate.py --status` is how you *check*.

## Next steps

1. **Run the one post-deploy report nobody has looked at yet:**
   `docker-compose exec tracker python3 scripts/report_kit_datasheets.py`.
   Migration 008 seeded `kit_datasheets` from `kit_template_units` and from
   real units; whatever it could not map is a box the gap checker will never
   offer as *buildable*, so a sprue that could become the missing unit reads as
   a shortfall to buy. The hyphen is not optional on bastion.
2. **`PUBLIC_URL=https://bastion.tail25c97e.ts.net` in `.env`**, then
   `docker-compose up -d`. Not needed for the camera now that HTTPS is live,
   but it is what lets the scan page hand over a tappable link if Clay ever
   lands on the plain-http address.
3. **Two pins have moved — Clay's call, deliberately.** BSData
   `13f3c4e5 → 04c62fcd` (cheap: new units appear). MFM
   `06754e2f → 3c1efe0d` (**changes points under existing lists**). Kill Team
   is current. `scripts/check_rules_pins.py` prints the exact steps for each.
4. **Spec §9's remaining nine requirements**, each a decision still owed: sale
   candidates, list validation's three-state badge, the shortfall→box inversion
   with à la carte comparison, cross-list wishlist dedupe, the "also appears in
   Speed Freeks 1000" note, the dashboard's 30-day view and spend, the backlog
   view, an admin overrides UI, and the flat per-model CSV.
5. **`BACKUP_DEST` is unset** — snapshots sit on the same Jetson as the
   database.
6. **The weekly sweep needs `0 15 * * 1` after 1 November**, when CDT ends.
7. **Two incomplete catalogue entries**: the Daemon Prince and GSC Broodcoven
   premium kits each list only half their contents. Needs Clay's decision on
   the variant/split.
8. **Do a real shelf session.** ~100 boxes against test fixtures is still the
   only way to find what is slow.
9. **The rest of the design.** Armies, scan review, kit templates, kit detail
   and sign-in still carry the old structure on the new ground. `/lists/<id>`,
   `/units/<id>` and `/collection` came across this session.
10. **The sharing question** decides whether the collection needs a user column.
   Nothing built so far assumes single-user in the data.

## Standing hazards worth re-reading

- **Two lists wanting the same unit raise both shortfalls.** Allocation is
  *within* one list, deliberately — the original spec is explicit that models
  are not allocated between lists. Spec §9 records the cross-list note it asks
  for instead, still unbuilt.
- **There is no bulk box catalogue.** GitHub search returns zero repos; every
  candidate host is egress-blocked. Do not go looking again without new
  information.
- **A blocker belongs to an approach, not to a step.** §2.7 was called "blocked
  on a source" for weeks; that was true of *fetching* a list and never of
  *pasting* one.
- **Route tests only pass as a suite.** A single-test run fails in the client
  fixture's login, which looks like the assertion failing.
- **Flask caches templates when `debug=False`.** A browser check against a
  scratch server keeps serving the *old* template after an edit; static files
  reload from disk, so CSS and JS changes appear and markup changes do not.
  That cost a round of "the layout is still broken" on the filters. Restart the
  server after touching a template.
- **`DB_PATH` is not an env var this app reads.** Set `db.DB_PATH` in a
  launcher (`scratchpad/serve.py`).
