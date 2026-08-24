# Warhammer Collection Tracker

Tracks every Warhammer 40,000 model in the collection individually — sprue to
battle ready — across multiple armies and game systems.

Single user, Flask + SQLite, deployed in Docker on `bastion` behind the existing
Cloudflare Tunnel. Conventions follow [Remndrs](https://github.com/Karieo/Remndrs):
flat module layout, `python-dotenv` config read lazily, bcrypt + session-cookie
auth, server-rendered Jinja with vanilla JS, no build step and no ORM.

**Status: in use.** Schema and migrations, the rules-data importer, the
collection itself (armies, kits, units, models, the stage pipeline and painting
session mode), the filterable inventory and own-it check, lists with their gap
report and wishlist, a dated photo log per unit, and an authenticated inventory
export.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then set SESSION_SECRET, OWNER_PASSWORD, TIMEZONE

python3 migrate.py            # create/upgrade the database
python3 scripts/fetch_bsdata.py    # fetch the pinned BSData catalogues (~65 MB, once)
python3 scripts/import_bsdata.py   # import datasheets + points, then print the report

python3 app.py                # http://localhost:3100
```

Docker, on `bastion`:

```bash
cp .env.example .env    # then set OWNER_PASSWORD
./deploy.sh             # preflight, build, start, fetch + import rules data
```

See [DEPLOY.md](DEPLOY.md) — it covers the three things that go wrong on a first
deploy, two of which are silent.

Tests: `pip install -r requirements-dev.txt && python3 -m pytest`

CI runs the suite on 3.11 and 3.12, lints the backup scripts with ShellCheck,
and builds and boots the Docker image on every push and pull request
(`.github/workflows/ci.yml`).

## Layout

| Path | What it is |
|---|---|
| `app.py` | Flask routes, auth, bootstrap |
| `database.py` | Connections, the migration runner, shared queries |
| `migrate.py` | Migration CLI (`--status` to look before you leap) |
| `migrations/` | Numbered SQL, applied in order, never rewritten |
| `scripts/fetch_bsdata.py` | Fetches BSData at a pinned commit |
| `scripts/import_bsdata.py` | Imports datasheets and points; reports what it couldn't resolve |
| `data/mfm/` | Munitorum Field Manual snapshots (MIT, committed) |
| `data/bsdata/` | BSData catalogues (fetched, gitignored — see `data/SOURCES.md`) |
| `collection.py` | Armies, kits, units, models, stage movement |
| `kit_templates.py` | What is inside a box, defined by hand |
| `seed/` | Combat Patrol magazine seed job and its contents file |
| `static/vendor/` | ZXing-js, vendored (Apache-2.0) |
| `templates/`, `static/` | Server-rendered Jinja + vanilla JS, no build step |
| `backup.sh` | Nightly snapshot + CSV export + off-box copy |
| `restore.sh` | Verify a snapshot (`--check`) or restore one |
| `deploy.sh` | Preflight + build + start + first-run rules import |

## Using it

Armies hold units; units hold one row per physical model. The primary control
everywhere is **Advance all** — one tap moves a whole unit forward a stage,
because almost every real update is "I primed the squad". *Advance N* handles
half-finished squads, the per-stage `+1` handles single models, and the "set a
count" box handles "six of these ten are primed" without touching a checkbox.
Individual model selection exists and is never required.

`/paint` is session mode: pick a unit, big tap targets, every tap saves. It is
meant to be used *during* the hobby rather than as an admin chore afterwards —
that gap is where the last tracker died.

Every percentage is effort-weighted (`datasheets.effort` per model), because a
Knight and a Termagant are both "1 model" and counting them equally makes
progress bars lie. Raw counts show alongside.

## Getting things in

Two doors, both typed. `/add` takes a pasted list — "20 Boyz built", "Trukk
primed" — and matches every line against a datasheet or asks which one it is.
`/templates` says what is inside a box once, so owning another copy is a single
action.

There was a barcode scanner and a researched box catalogue behind it. Both are
gone: scanning only paid off when the app already knew the box, and no one
publishes what is in a Games Workshop box, so most scans ended in typing the
contents anyway. See CLAUDE.md before rebuilding either.

## Seed data

`seed/combat_patrol_magazine.py` pre-loads the Hachette Combat Patrol partwork
as kit templates, so 90 issues are not 90 manual entries.

```bash
python3 seed/combat_patrol_magazine.py --status          # what is present, what is missing
python3 seed/combat_patrol_magazine.py --dry-run
python3 seed/combat_patrol_magazine.py --owned-through 75
```

Every unit name is matched against an imported BSData datasheet. Anything that
does not match is reported and written to `unresolved_imports` — never guessed
at, never dropped. A sprue split across issues is attached to the issue that
*completes* it, because half a Maulerfiend is not a model you own. Re-running is
idempotent.

**The four premium kits ship with contents; the 90 issues do not.** The
difference is the source: the premium kits are documented in the spec (§11), a
reviewed document, so they seed today. The per-issue contents are not, and every
published source for them was unreachable from the environment this was built
in. Seed data is derived and reviewed or it does not ship — a partwork list
written from memory would be fluent, plausible and wrong in places, with no
signal about which, and it would land as trusted data covering the whole
magazine collection.

The issue importer refuses to run without provenance (source URLs, a retrieval
date, a confidence, and a second source that agrees). `models: min` in the data
file means "one minimum-size unit", resolved from the rules data at seed time so
the count can never drift from them. See `seed/data/README.md`.

## Rules data

Two sources, because they are good at different things:

- **Datasheets** come from [BSData/wh40k-11e](https://github.com/BSData/wh40k-11e):
  the stable `bsdata_id` re-sync key, faction, and the keywords the effort
  heuristic reads.
- **Points** come from [BSData/wh40k-11e-mfm](https://github.com/BSData/wh40k-11e-mfm),
  parsed from GW's official Munitorum Field Manual. It ships points already
  flattened per legal unit size, which removes the modifier-evaluation
  workstream the spec was braced for, and it is the only one of the two that
  carries a licence.

They join on normalised name **scoped by faction**, because 35 unit names carry
genuinely different points depending on who is fielding them. On the pinned
data that resolves 1,030 of 1,053 current datasheets. Everything it cannot
resolve becomes a row in `unresolved_imports` and is printed in the import
report — nothing is guessed at and nothing is dropped.

Re-running the importer is idempotent. Rows with `manual_override = 1` (points)
or `effort_is_override = 1` (effort) are never overwritten.

## Backups

The database is dozens of hours of manual entry with no upstream source —
nothing can reconstruct which of your Boyz are primed. `backup.sh` takes a
`sqlite3 .backup` snapshot, verifies its integrity, writes a credential-redacted
CSV export beside it, and ships both off the box.

```bash
cp .env.example .env      # set BACKUP_DIR; BACKUP_DEST for the off-box copy
./backup.sh
./restore.sh --check      # verify the newest snapshot, change nothing
```

```
0 3 * * *  /path/to/hobby-tracker/backup.sh >> /var/log/hobby-tracker-backup.log 2>&1
```

**Why `.backup` and not `cp`.** The app keeps a connection open, so committed
data routinely sits in the `-wal` file with the `.db` not yet containing it.
Copying the `.db` alone loses it silently, and the backup looks fine until the
day it matters. This is measured, not assumed — `tests/test_backup.py` writes a
row into the WAL, copies both ways, and asserts the plain copy loses it and the
snapshot doesn't.

**Verify a restore, don't assume one.** `./restore.sh --check` opens the newest
snapshot read-only, runs `integrity_check` and a foreign-key check, confirms the
schema is not ahead of the code, and refuses a snapshot holding no collection
data. `./restore.sh <snapshot>` performs a real restore and sets the current
database aside first, because restoring the wrong snapshot is an easier mistake
to make than losing the database was.

The CSV export redacts password and token hashes. The `.db` snapshot beside it
keeps everything; the CSV exists so the *collection* stays readable when the app
or the schema is broken, and a credential does nothing for that while being the
one thing in there worth stealing.

## Licensing

`wh40k-11e` ships no licence file and is community-maintained. Fine for a
private single-user app on your own hardware; do not redistribute the data or
publish this app publicly with it baked in. That is why `data/bsdata/` is
fetched rather than committed. `wh40k-11e-mfm` is MIT and is vendored under
`data/mfm/`. Warhammer 40,000 is © Games Workshop; this project is unaffiliated.
