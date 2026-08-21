# Warhammer Collection Tracker

Tracks every Warhammer 40,000 model in the collection individually — sprue to
battle ready — across multiple armies, with barcode scanning for onboarding.

Single user, Flask + SQLite, deployed in Docker on `bastion` behind the existing
Cloudflare Tunnel. Conventions follow [Remndrs](https://github.com/Karieo/Remndrs):
flat module layout, `python-dotenv` config read lazily, bcrypt + session-cookie
auth, server-rendered Jinja with vanilla JS, no build step and no ORM.

**Status: build steps 1–2 of 5.** Schema, migration runner, reference-data seed
and the rules-data importer are done. Armies, kits, units and models (step 3),
scanning (step 4) and the collection view (step 5) are not built yet.

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
docker compose up -d --build
docker compose exec tracker python3 scripts/fetch_bsdata.py
docker compose exec tracker python3 scripts/import_bsdata.py
```

Tests: `pip install -r requirements-dev.txt && python3 -m pytest`

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
| `backup.sh` | Nightly snapshot + CSV export + off-box copy |

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
`sqlite3 .backup` snapshot (never a file copy, which corrupts), verifies its
integrity, writes a full CSV export beside it, and ships both off the box.

```
0 3 * * *  /path/to/hobby-tracker/backup.sh >> /var/log/hobby-tracker-backup.log 2>&1
```

Verify a restore once, early, while losing the database still wouldn't matter.

## Licensing

`wh40k-11e` ships no licence file and is community-maintained. Fine for a
private single-user app on your own hardware; do not redistribute the data or
publish this app publicly with it baked in. That is why `data/bsdata/` is
fetched rather than committed. `wh40k-11e-mfm` is MIT and is vendored under
`data/mfm/`. Warhammer 40,000 is © Games Workshop; this project is unaffiliated.
