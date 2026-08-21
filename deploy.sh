#!/bin/bash
# Preflight + deploy for bastion.
#
#   ./deploy.sh            check everything, then build and start
#   ./deploy.sh --check    check only, change nothing
#
# The checks exist because three things go wrong on a first deploy and two of
# them are silent:
#
#   1. docker-compose bind-mounts ./.env into the container. If that file does
#      not exist yet, the Docker daemon helpfully creates a *directory* with
#      that name, the app reads no config at all, and `cp .env.example .env`
#      then fails with "is a directory".
#   2. An empty SESSION_SECRET makes the app generate a random one per boot, so
#      every deploy silently signs you out on every device.
#   3. OWNER_PASSWORD=changeme ships in .env.example, and this app is publicly
#      reachable through the Cloudflare Tunnel the moment it is up.
set -euo pipefail

on_exit() {
  local status=$?
  [ "$status" -ne 0 ] && printf '\033[31m✗ deploy stopped (exit %s)\033[0m\n' \
    "$status" >&2
  exit "$status"
}
trap on_exit EXIT

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
note() { printf '  \033[33m•\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗ %s\033[0m\n' "$*"; exit 1; }

env_value() {
  local line=''
  line="$(grep "^$1=" .env 2>/dev/null | head -1)" || true
  printf '%s' "${line#*=}"
}

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

bold "── Preflight ──"

# ── 1 · .env must exist and be a file ────────────────────
if [ -d .env ]; then
  fail ".env is a DIRECTORY. Docker created it because compose bind-mounts it
    and it did not exist. Remove it and copy the example:
      rmdir .env && cp .env.example .env"
fi
if [ ! -f .env ]; then
  note "No .env — creating one from .env.example"
  cp .env.example .env
  note "Edit .env now: OWNER_PASSWORD at minimum, then re-run."
  exit 1
fi
ok ".env is a file"

# ── 1b · .env must actually parse ────────────────────────
# Docker Compose reads .env for variable substitution and refuses the whole
# run over one bad line, with an error that names the mangled text rather than
# the line number. Easy to produce by accident: type a shell command into an
# editor that is open on .env, and line 1 becomes "./deploy.sh# Copy to ...".
# Catching it here costs nothing and points at the actual line.
BAD_LINE=""
BAD_NUMBER=0
while IFS= read -r line; do
  BAD_NUMBER=$((BAD_NUMBER + 1))
  case "$line" in
    ''|'#'*) continue ;;
  esac
  if ! printf '%s' "$line" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*='; then
    BAD_LINE="$line"
    break
  fi
done < .env
if [ -n "$BAD_LINE" ]; then
  fail ".env line $BAD_NUMBER is not NAME=value and will stop Compose:
      $BAD_LINE
    Fix that line — a stray command typed into an editor is the usual cause."
fi
ok ".env parses"

# ── 2 · A session secret that survives a restart ─────────
if [ -z "$(env_value SESSION_SECRET)" ]; then
  if [ "$CHECK_ONLY" -eq 1 ]; then
    fail "SESSION_SECRET is empty — every restart would sign you out"
  fi
  SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  # Portable in-place edit: BSD and GNU sed disagree about -i.
  python3 - "$SECRET" <<'PY'
import re, sys
secret = sys.argv[1]
with open('.env', encoding='utf-8') as fh:
    text = fh.read()
text = re.sub(r'^SESSION_SECRET=.*$', f'SESSION_SECRET={secret}', text,
              count=1, flags=re.M)
with open('.env', 'w', encoding='utf-8') as fh:
    fh.write(text)
PY
  ok "Generated a SESSION_SECRET"
else
  ok "SESSION_SECRET is set"
fi

# ── 3 · Not the shipped password, on a public URL ────────
PASSWORD="$(env_value OWNER_PASSWORD)"
case "$PASSWORD" in
  ''|changeme|password|test)
    fail "OWNER_PASSWORD is '$PASSWORD'. The Cloudflare Tunnel makes this
    publicly reachable the moment it starts — set a real one in .env." ;;
esac
[ "${#PASSWORD}" -ge 12 ] || note "OWNER_PASSWORD is short for a public URL"
ok "OWNER_PASSWORD is set"

[ -n "$(env_value TIMEZONE)" ] || note "TIMEZONE unset — timestamps will use UTC"
[ -n "$(env_value BACKUP_DIR)" ] || note "BACKUP_DIR unset — ./backup.sh will not run"
if [ -z "$(env_value BACKUP_DEST)" ]; then
  note "BACKUP_DEST unset — backups stay on this box. One Jetson with one
    drive is one failure away from starting over."
fi

command -v docker >/dev/null || fail "docker not found"

# Ubuntu 20.04 — which is what bastion runs — ships the standalone
# docker-compose v1 rather than the v2 plugin, and plenty of boxes have one
# and not the other. Either is fine; refusing to deploy over which spelling is
# installed would be a check getting in the way of the job.
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
  note "Using the standalone docker-compose (v1)"
else
  fail "Neither 'docker compose' nor 'docker-compose' is installed"
fi
ok "docker and compose present"

if [ "$CHECK_ONLY" -eq 1 ]; then
  bold ""
  bold "Preflight passed. Run ./deploy.sh to build and start."
  exit 0
fi

# ── Deploy ───────────────────────────────────────────────
bold ""
bold "── Building and starting ──"
"${COMPOSE[@]}" up -d --build

printf '  waiting for health'
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:$(env_value PORT || echo 3100)/healthz" \
       >/dev/null 2>&1; then
    printf '\n'; ok "Healthy"
    break
  fi
  printf '.'; sleep 1
done
curl -fsS "http://localhost:$(env_value PORT || echo 3100)/healthz" >/dev/null 2>&1 \
  || { printf '\n'; "${COMPOSE[@]}" logs --tail 40; fail "Never became healthy"; }

# ── Rules data ───────────────────────────────────────────
# Fetched rather than vendored (65 MB, no licence), so a fresh box has none.
# Without it there are no datasheets, and a unit cannot be added against
# nothing.
if [ ! -d data/bsdata ] || [ -z "$(ls -A data/bsdata 2>/dev/null)" ]; then
  bold ""
  bold "── Rules data ──"
  note "No data/bsdata yet — fetching and importing (a few minutes)"
  "${COMPOSE[@]}" exec -T tracker python3 scripts/fetch_bsdata.py
  "${COMPOSE[@]}" exec -T tracker python3 scripts/import_bsdata.py
else
  ok "Rules data present"
fi

bold ""
bold "Deployed."
echo "  Local:  http://localhost:$(env_value PORT || echo 3100)"
echo "  Public: whatever the Cloudflare Tunnel points at port"
echo "          $(env_value PORT || echo 3100) — the camera needs that HTTPS origin."
echo
echo "  Next:   ./backup.sh && ./restore.sh --check"
