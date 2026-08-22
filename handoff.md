# Handoff — Session 6

## 1 · Goal

Deploy to bastion, then make it usable with real boxes. Everything here came
out of doing that on the real box rather than reading about it.

## 2 · Current State

**Live on bastion, with both rules sources loaded and a verified backup.**
Eight PRs merged (#8–#15).

| | |
|---|---|
| Image | healthy on port 3100 |
| 40,000 datasheets | 1,445 (+330 Legends, +62 Crucible, kept and flagged) |
| Kill Team operatives | 1,450 across 104 teams, editions 2018 / 2021 / 2024 |
| Points rows | 2,544, of which 399 inherited Chapter listings |
| Unresolved | 31, all expected classes |
| Collection | Clay's to fill now that both catalogues exist |
| Backup | verified restorable; `BACKUP_DEST` still unset |

**Not tested on real hardware:** the camera. The Cloudflare Tunnel is still not
pointed at 3100, so scanning has only ever been manual entry over plain HTTP.
The secure-context guard was seen working — it refuses the camera, names the
reason, and offers the keypad — but `getUserMedia` itself has never run.

## 3 · Active Files

- `migrations/003_game_system.sql` — `datasheets.game_system`
- `scripts/fetch_killteam.py`, `scripts/import_killteam.py`
- `collection.py` — `search_datasheets`, `kits_awaiting_contents`,
  `adopt_template`, `list_templates_with_contents`
- `scanning.py` — `shelve_queue_row`
- `static/js/app.js` (picker labels), `static/js/review.js`
- `templates/scan_review.html`
- `deploy.sh`, `restore.sh`, `Dockerfile`, `.gitignore`, `.github/workflows/ci.yml`

## 4 · Changes Made

**#8–#12, the deploy.** A compose schema bastion's docker-compose rejected;
`git` missing from the image so the rules fetch died after a healthy boot;
`.gitignore` covering `.env` but none of nano's copies of it; the import gated
on a *directory* rather than on the database; a fetch refusal that told Clay to
delete a complete checkout; and `restore.sh --check` failing a perfectly good
first snapshot because a fresh install has no collection. Full detail in each
PR; all five were found by running the thing, and CI was green throughout.

**#14, recording a box without contents.** `shelve_queue_row` creates a kit at
"On sprue" with no units; `kits_awaiting_contents` is the visible backlog;
`adopt_template` fills one in later without duplicating it. Sound work, wrong
problem — see section 5.

**#15, Kill Team.** BSData keeps Kill Team in a separate repository
(`wh40k-killteam`, XML `.cat`), so operatives had never been imported and those
boxes could not be recorded at all. 1,450 operatives, all three editions kept
in `variant` for the same reason Combat Patrol needs its year. One new column,
defaulted, so existing rows are untouched.

259 tests pass; shellcheck clean.

## 5 · Failed Attempts

**I built the wrong fix, at length, and shipped it.** Clay scanned a real box,
got "Unknown box", and said he would not enter ~100 boxes by hand. I diagnosed
*form friction* and made contents optional — which records barcodes and tracks
no models, in an app whose entire purpose is tracking models sprue to battle
ready. He said "this is not really doing what I need it to do", and he was
right.

Two things would have caught it before I wrote any code. **Measuring the form:**
it has a searchable datasheet picker and pre-fills the count from `min_models`
— four keystrokes, a few seconds, not the two minutes I assumed. **Asking what
was actually missing:** "the kill team are missing from the database as well"
arrived unprompted and explained everything. The data was missing, not the
patience.

The pattern from earlier in the session repeated: I optimised against the
constraint Clay stated ("no manual entry") instead of the goal he had
("track my models"), and did not check the premise.

**Three guesses about a box I cannot see**, each answerable with one command:
telling Clay to `mv` a complete 46-catalogue checkout aside (it survived only
because he ran the `ls` first); `git pull && ./deploy.sh` twice while the fix
sat in an unmerged PR, with `Using cache` and an identical image ID visible in
the screenshot I already had; and quoting "~1,900 datasheets" as his health
checkpoint when the real figure, in a docstring I had read, is 1,445.

**A green suite that was lying.** An interrupted edit appended the same route
tests twice; both copies passed because Python shadows the earlier definition,
so half of them never ran. Found by noticing the diff was larger than what I
wrote.

**Kill Team nearly shipped broken twice**, both silent, both caught only by
running it against the real 127 catalogues: BSData reuses entry ids across
catalogues, so keying on the bare id let one team overwrite another's
operatives; and `search_datasheets` hides `variant IS NOT NULL`, which would
have imported 1,450 operatives and displayed none of them. A third was my own
regression — ordering put Kill Team above 40,000 and buried Intercessor Squad.

## 6 · Next Steps

**Point the tunnel at 3100 and scan a real box.** The last untested thing, and
the one that has to work in a shop rather than at a desk. iOS will prompt for
camera access once; a dismissed prompt is remembered and `Start camera` then
does nothing forever (Settings → Safari → Camera to undo).

**Then the collection view** — the other half of step 5, and the last thing
before v1 is done. No blockers.

**Open questions for Clay:**

- **Other game systems.** If he owns Age of Sigmar, Necromunda or Horus Heresy
  boxes they are missing for exactly the reason Kill Team was. BSData has repos
  for each and the XML shape is identical, so extending the importer is small.
- **`BACKUP_DEST`** is still unset. Snapshots land on `/mnt/t7` — a different
  disk, the same Jetson.
- **The 90 Combat Patrol issues** still need a source. Every candidate host is
  refused by egress policy (`403 to CONNECT`), including `lexicanum.com`, which
  would not have carried box contents anyway. No open dataset of GW kit
  contents or EANs exists on GitHub — searched, and the search works.
- **Two premium-kit decisions**: which Daemon Prince variant, and whether GSC
  Broodcoven splits into Magus / Primus / Patriarch.
- **Wolf Guard Headtakers** needs a pick between two same-named datasheets.
- Dependencies unpinned (`>=`), so CI resolves the latest release each run.

**Do not build past step 5.**
