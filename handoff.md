# Handoff

## 1 · Goal

Four more PRs, all of them started by Clay opening the app and saying what was
wrong, or answering "what next". The run before this one is at the bottom.

1. > "I just need to be able to track models here… I do need to edit lists.
   > Spend and kits are obsolete."

   List editing, and the money off every screen (#60).
2. **The home dashboard** (#61) — chosen from spec §9 when asked what to build.
3. > "games played by list, win/loss and point difference 0-100"

   Games per list (#62).
4. > "I want to be able to paste in a list and it reconcile against the
   > datasheets and add." … "Here is the format." … **"I pasted from Claude
   > trying to make a list."**

   The `/add` paste door, a real count bug behind it, and a correction to a
   claim I had already written into the repo (#63).
5. > "Pull the title, battle size from the list import. If I don't give it let
   > me add after and before fully saved."

   A screenshot of `/lists/import` on his phone, keyboard up over a `required`
   name field, with the name three lines below it in the textarea (#65).

## 2 · Current state

**`main` is green at `ab2012a`** — PR #63 merged. **939 tests**, ShellCheck
clean, no open PRs, no branch in flight.

**Deployed state on bastion is four merges behind and the same item is still
blocking.** Clay has **never run the Kill Team importer**, which is the only
thing that fixes the faction filtering — no migration, the fix arrives with the
importer alone. His Orks filter reads 23 where it should read 62.

### What landed

**#60 · Lists are editable, and the paste behind them is not.** `update_list`
takes `**fields` against a `_LIST_FIELDS` allowlist. `raw_text`,
`source_format` and `points_total` are outside it on purpose: they record what
was *pasted*, `reparse` reads them, and a form that could rewrite them would let
a typo erase provenance nothing can rebuild.

**And there is no money anywhere on screen.** No RRP on `/templates`, no "paid
£X" on `/sale`, no cost on `/gallery`, no totals on `/shopping`. `rrp_cents` and
`cost_cents` stay in the schema unread — the bargain migration 010's disposal
columns already made — but the *code* behind the prices went rather than being
left computing: a figure nothing renders drifts out of step and nobody notices.

**#61 · The home screen says what you got done, and never answers with a zero.**
`recent.py` and the "Last 30 days" panel, the second thing to read
`stage_events` back after `journey.py`. Models finished is what §5.1 asks for
and reads **zero** for a month spent priming sixty Boyz — so the headline falls
back to what did move, with `effort_done` alongside in the same currency
`/backlog` reports what is left in. Arrivals are excluded *structurally* (an
inner join on the from-stage), so typing in a painted collection does not report
itself as a month's work. It is the one counting surface that does **not**
filter disposals: it counts what Clay did, not what he has.

**#62 · Games are recorded per list, and nothing derived is stored.** `games.py`
and migration 013. Asked which way round, Clay chose **both scores** over a
difference — one more number typed, and it is what tells "lost 85–90" from
"lost 45–90". Result and margin both fall out of the pair, so neither is a
column. The 0–100 range is his number, pinned as data like `BATTLE_SIZES`, and
deliberately not scoped by game system. Outcomes only: he plays in Battlebase,
"playing the game is a whole other thing".

**#65 · An export names itself, so `/lists/import` stopped asking.**
`list_parse.preamble` reads the block `_split` already isolates and returns the
name, leaving the rest as candidates — nothing in an export says which line is
the faction and which the detachment. `lists.read_preamble` applies the app's
vocabulary: a battle size only by matching `BATTLE_SIZES` by name, a faction
only by matching a real row, **exactly**. Typed always beats parsed, the screen
names what it filled in, and all three stay editable on the preview. The `name`
input lost its `required`; `autofocus` moved to the textarea.

**#63 · `/add` takes an app export, and counts it properly.** Measured first: a
GW-style export through the shelf parser gave fifteen rows, **seven of them
junk**. `bulk_add.parse_paste` now asks `list_parse.detect_format` and
dispatches; the two parsers stay separate because merging costs the shelf its
stage words and the export its refusal to skip.

Behind it was a real bug. `_newrecruit_count` inferred a unit's models from
bullet *nesting* and returned 1 for any flat block — 20 units and 92 models read
as 20. For the gap report that is a survivable under-count; for `/add`, which
*writes* the models, it is seventy miniatures silently missing.

## 3 · Active files

Nothing is in flight. New this run:

- `recent.py`, `tests/test_recent.py` — **new** (#61). No nav entry; the panel
  lives on Home.
- `games.py`, `migrations/013_games.sql`, `tests/test_games.py` — **new** (#62).
- `bulk_add.parse_paste` and `list_parse._uncounted_wargear` — **new** (#63).
  `list_parse` imports the shared scaffolding patterns from `bulk_add`, so
  `parse_paste`'s import of `list_parse` is local and says why.
- `tests/fixtures/lists/pasted_orks_2000.txt` — Clay's paste. **Real input,
  invented format.** See §5.
- `lists.py`, `templates/list.html`, `templates/lists.html` — editing, the games
  section, the record on the index.
- `list_parse.preamble` / `_split`, `lists.read_preamble`,
  `templates/list_import*.html`, `static/js/list-import.js` — **new** (#65).
  The commit JS reads the fields rather than the button's data attributes,
  which would carry whatever they said before Clay corrected them.

## 4 · Changes made

Merged: #60, #61, #62, #63, #64 (the handoff itself). Test count 864 → 959,
with #65 open.

## 5 · Failed attempts

**I filed model-written text as real data, and wrote it into the repo as fact.**
Clay pasted a 2000-point Ork list with "Here is the format". I took it at face
value, named the fixture `real_orks_2000.txt`, rewrote the fixtures README around
it as "the first real export this repo has ever had", and cited it in CLAUDE.md
and three docstrings as evidence of what an app writes. Two days later: **"I
pasted from Claude trying to make a list."**

It is the exact laundering this repo forbids everywhere else — fluent,
plausible, unsourceable — and the seed-data rule has a whole section in
CLAUDE.md that I walked straight past **because the text arrived from a person
rather than from a file**. That is the transferable lesson: provenance is about
where the words were *written*, not who handed them over. Ask.

Corrected in `5f3561d`. The bug it exposed was real and the fix stays; what
changed is that it is documented as an unverified convention a real sample could
disprove. **This repo has still never read a verified export from any app.**

**Two vacuous tests, both caught by the teeth check, both in #61.** The
window-edge test pinned a day *inside* the window rather than the boundary, so
widening the window by a day still passed. The distinctness test had its second
event cancelled by the retreat before it, so it never exercised distinctness at
all. Both rebuilt.

**A third overclaiming test, in #63.** `test_a_retyped_sheet_is_a_shelf_not_an_export`
passed under the sabotage that broke routing entirely — because, measured, both
parsers return identical rows for a retyped sheet. Renamed to claim only the
detection.

**Two sabotages that were wrong themselves.** One crashed the code (a LEFT JOIN
leaving `fs.position` NULL) instead of producing a wrong answer, which proves
nothing about the tests. Another changed only the *lookup* key and not the key
being built, making it a duplicate of an earlier sabotage. **A sabotage has to
produce a wrong answer, not an exception.**

**A screenshot that looked unchanged was not proof the change had not landed.**
The dev server runs `debug=False`, so Jinja caches templates; the first
re-render after a template edit was byte-identical. Restart the server before
believing a render.

**Rendering the page found something in all four PRs.** "10 models finished"
above "… · 10 battle ready" (one fact twice); a per-game margin line restating
what "Lost 55–60" already said; a remove control that was the heaviest element
in every card despite being the rarest action; and "Lines *without* a stage word
land at Assembled" on a paste where none of them have one.

**`pkill -f devserver.py` killed my own shell** twice (exit 143). Stop a
background task with its task id.

## 6 · Next steps

**On bastion, and the first has now been outstanding for four merges:**

- **Deploy and run the Kill Team importer.** `git pull && ./deploy.sh`, then
  `docker-compose exec tracker python3 scripts/import_killteam.py`. The service
  is `tracker`, not `app`. `under Orks` should go 23 → 62.
- **Confirm the backup cron took** — `crontab -l`.
- **Revoke the API token pasted into chat.** `scripts/api_token.py --list`, then
  `--revoke <id>`.

**Two more tests that were not checking, both in #65.** One did
`' '.join(a_string)`, which spaces out every character and made
`'out of the paste' not in body` true no matter what. The other asserted around
the sentence it cared about rather than on it. Caught by reading them back, not
by the suite.

**A sabotage that was too weak to trip the test it aimed at.** Loose faction
matching on a two-character prefix changed nothing, because no detachment in the
fixture started with the same two letters as a faction. The lesson is the same
shape as before: **a sabotage that fires and changes no outcome has not tested
anything.** The guarantee turned out not to come from match strictness at all —
it comes from matching against *real faction rows*, so the sabotage that breaks
it is one that **invents** a faction from the line, and that is what the test is
aimed at now.

**Asked and unanswered:**

- **Should one paste both create a list and add the models?** Today `/add` adds
  models and `/lists/import` makes a list — two doors, two pastes. Clay was
  offered the combination and has not answered.
- **Which app does he export from?** Nothing knows. `/add` says "Read as an app
  export" and names nothing, which is right until there is evidence.
  `/lists/<id>` still names an app in its own "read as" line and has the same
  problem; it predates this and is worth fixing once he says.

**Decisions owed, none urgent:**

- **`BACKUP_DEST` is still unset**, so both copies live on the same Jetson.
- **Weekly sweep DST shift** — `0 14 * * 1` → `0 15 * * 1` after 1 November. The
  existing DST-fix Routine covers Morning Brief, Evening Wind-down and D&D Prep
  but **not** the sweep.
- `barcodes` and `scan_queue` still have no readers.

**Still unbuilt from spec §9:**

- **Sharing models between lists** (§7) — the quiet note, *"3 Killa Kans also
  appear in Speed Freeks 1000"*. Explicitly **not** allocation across lists.
- **Admin overrides UI** (§5.9) — `datasheet_points.manual_override` and
  `datasheets.effort_is_override` are respected by the importer and settable by
  nothing.
- **List export as text and JSON** (§10).
- **Importing a list from a file or a URL** (§2.7) — gated on egress policy.
  Pasting never was.

---

## Previous run (six PRs, #48–#53)

Kept because its §5 is still the best statement of this repo's most recurring
failure. **"A check that isn't checking" happened five times in that run**, and
the tell is that **the sabotage run prints nothing**. Also from it: the
zero-coverage guard in `shopping._cover` is what makes the loop terminate —
remove it and the page *hangs* rather than failing, which no suite reports as
red. `BACKUP_DIR` is the local snapshot directory; `BACKUP_DEST` is an off-box
`user@host:path`. `docker-compose exec app` does not work — the service is
`tracker`. And PR #53 was reported as closed-unmerged when it had merged five
seconds earlier: **check the branch against `origin/main` with git before
believing a `merged` flag near a state transition.**
