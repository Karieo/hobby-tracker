# Deploying to bastion

```bash
git pull
./deploy.sh
```

That is the whole thing. `deploy.sh` runs a preflight, builds, starts, waits for
health, and fetches + imports the rules data if this is a fresh box.

`./deploy.sh --check` runs the preflight and changes nothing.

## First deploy

**The repo is private**, so plain HTTPS will prompt for credentials and GitHub
no longer accepts an account password — you need either an SSH key on the box
that is registered with your GitHub account, or a personal access token.

```bash
cd ~                                                    # not inside an existing clone
git clone git@github.com:Karieo/hobby-tracker.git       # or https:// with a PAT
cd hobby-tracker
cp .env.example .env
$EDITOR .env          # OWNER_PASSWORD at minimum; save before running deploy
./deploy.sh
```

`cd ~` first is not fussiness: cloning from inside an existing checkout leaves
a nested copy at `hobby-tracker/hobby-tracker`, which is confusing to find
later.

### Either flavour of compose works

bastion runs Ubuntu 20.04, which ships the standalone `docker-compose` (v1)
rather than the `docker compose` plugin. `deploy.sh` uses whichever is present.

`docker-compose.yml` declares `version: '3.8'` for the same reason. Compose v2
calls that obsolete and warns; v1 **requires** it, and without it falls back to
the legacy format, reads `services` as a service name, and dies on
"Unsupported config option". It stays.

Then point the Cloudflare Tunnel at **port 3100** and verify a backup:

```bash
./backup.sh && ./restore.sh --check
```

## The three things that go wrong, and why the preflight checks them

**`.env` must exist before the first `docker compose up`.** Compose bind-mounts
`./.env` into the container. If the file is not there, the Docker daemon creates
a *directory* with that name, the app reads no config at all, and the obvious
fix (`cp .env.example .env`) then fails with "is a directory". The preflight
refuses to continue and tells you to `rmdir` it.

**`SESSION_SECRET` must be set.** Empty means the app generates a random one on
every boot, so every deploy silently signs you out on every device. The
preflight generates one if it is blank.

**`OWNER_PASSWORD` must not be the shipped default.** The Cloudflare Tunnel
makes this publicly reachable the moment it starts, and the owner account is
seeded from `.env` on first run. The preflight refuses `changeme`.

## The tunnel is not optional for scanning

`getUserMedia` needs a secure context. The tunnel's HTTPS origin provides one; a
plain-http Tailscale IP or `http://bastion.local:3100` does not, and the camera
will simply never start there. The scan page says so rather than failing
quietly, and manual digit entry works either way.

## Rules data

`data/bsdata/` is fetched rather than committed (65 MB, no licence file), so a
fresh box has none and there are no datasheets to add units against.
`deploy.sh` fetches and imports it automatically when the directory is empty.
To redo it by hand:

```bash
docker compose exec tracker python3 scripts/fetch_bsdata.py
docker compose exec tracker python3 scripts/import_bsdata.py
```

Both are safe to re-run. Hand corrections (`manual_override`,
`effort_is_override`) are never overwritten.

## Backups

Set `BACKUP_DIR` and — importantly — `BACKUP_DEST`, then cron it:

```
0 3 * * *  /path/to/hobby-tracker/backup.sh >> /var/log/hobby-tracker-backup.log 2>&1
```

Without `BACKUP_DEST` the snapshots stay on the Jetson, which is one failure
away from starting over. `backup.sh` says so on every run.

## Rolling back

Images are tagged `hobby-tracker:latest` and the database is migrated forward on
boot, so a rollback is a git checkout plus a rebuild — but **migrations are not
reversible**. If a deploy has already migrated the database, roll back with a
restore rather than by checking out older code:

```bash
./restore.sh --list
./restore.sh <snapshot>
```

`restore.sh` sets the current database aside before replacing it, and refuses a
snapshot whose schema is ahead of the code you are rolling back to.

## Logs and health

```bash
docker compose logs -f tracker
curl -fsS http://localhost:3100/healthz
```

Compose has a healthcheck on `/healthz` at 30s intervals, so `docker compose ps`
shows the container as unhealthy rather than merely running if the app is wedged.
