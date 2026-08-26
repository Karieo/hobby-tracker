#!/bin/bash
# Backup for the collection database.
#
# This database is dozens of hours of manual entry that cannot be reconstructed
# from anywhere — there is no upstream source for "which of my Boyz are primed".
# That makes backup a first-class requirement, not an afterthought.
#
# What it does, in order:
#   1. sqlite3 .backup to a dated snapshot — NOT a file copy. Copying a live
#      SQLite file while the app is mid-write produces a corrupt backup that
#      looks fine until you need it.
#   2. A CSV export alongside it. If the schema or the app ever breaks badly,
#      a human-readable snapshot is what saves the collection.
#   3. Ships both off the box if BACKUP_DEST is set. A single Jetson with one
#      external drive is one failure away from starting over.
#   4. Rotates old snapshots.
#
# Configure in .env:
#   BACKUP_DIR=/mnt/t7/hobby-tracker    local snapshot dir        (required)
#   BACKUP_DEST=clay@macbook.local:HobbyTrackerBackup   off-box   (recommended)
#   BACKUP_SSH_KEY=~/.ssh/id_ed25519    key for unattended cron   (optional)
#   BACKUP_KEEP=14                      snapshots to keep         (optional)
#
# Verify a restore once, early, while the database is still small and losing it
# would not matter:
#   sqlite3 "$BACKUP_DIR/db/tracker-<stamp>.db" 'PRAGMA integrity_check;'
set -euo pipefail

# The one thing worse than no backup is believing you have one. Any unexpected
# failure says so on the way out instead of exiting quietly with a status only
# cron sees.
on_exit() {
  local status=$?
  [ "$status" -ne 0 ] && printf '\033[31m✗ backup FAILED (exit %s)\033[0m\n' \
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

# Read one key from .env. Must never fail when the key is absent: optional
# settings legitimately aren't there (BACKUP_SSH_KEY ships commented out), and
# under `set -e` a non-matching grep would kill the whole backup — silently, and
# under cron, forever.
env_value() {
  local line=''
  line="$(grep "^$1=" .env 2>/dev/null | head -1)" || true
  printf '%s' "${line#*=}"
}

# Expand a leading ~ without eval, so a stray character in .env can't run.
expand() { printf '%s' "${1/#\~/$HOME}"; }

PYTHON="$APP_DIR/venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)" || fail "python3 not found"

BACKUP_DIR="${BACKUP_DIR:-$(env_value BACKUP_DIR)}"
[ -n "$BACKUP_DIR" ] || fail "Set BACKUP_DIR in .env, e.g. BACKUP_DIR=/mnt/t7/hobby-tracker"
BACKUP_DIR="$(expand "$BACKUP_DIR")"
BACKUP_DEST="${BACKUP_DEST:-$(env_value BACKUP_DEST)}"
BACKUP_SSH_KEY="${BACKUP_SSH_KEY:-$(env_value BACKUP_SSH_KEY)}"
BACKUP_KEEP="${BACKUP_KEEP:-$(env_value BACKUP_KEEP)}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"

DB_PATH="$APP_DIR/data/hobby_tracker.db"
[ -f "$DB_PATH" ] || fail "No database at $DB_PATH"

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/csv"

# ── 1 · Consistent snapshot via the online-backup API ────
SNAP="$BACKUP_DIR/db/tracker-$STAMP.db"
"$PYTHON" - "$DB_PATH" "$SNAP" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(f'file:{src}?mode=ro', uri=True)
d = sqlite3.connect(dst)
with d:
    s.backup(d)
# A backup nobody checked is a backup nobody has.
if d.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
    raise SystemExit('integrity_check failed on the snapshot')
s.close(); d.close()
PY
ok "DB snapshot → db/tracker-$STAMP.db (integrity checked)"

# ── 2 · Human-readable CSV export ───────────────────────
CSV_DIR="$BACKUP_DIR/csv/$STAMP"
mkdir -p "$CSV_DIR"
"$PYTHON" - "$SNAP" "$CSV_DIR" <<'PY'
import csv, os, sqlite3, sys

db, out = sys.argv[1], sys.argv[2]

# Credentials are redacted from the CSV. The .db snapshot beside it is the real
# backup and keeps everything; this file exists so the *collection* is readable
# when the app or the schema is broken, and a password hash does nothing for
# that while being the one thing here worth stealing. This copy travels
# off-box in plain text.
REDACT = {'users': {'password_hash'}, 'api_tokens': {'token_hash'}}

conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' "
    "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
redacted = 0
for t in tables:
    cols = [d[0] for d in conn.execute(f'SELECT * FROM "{t}" LIMIT 0').description]
    hide = REDACT.get(t, set())
    with open(os.path.join(out, f'{t}.csv'), 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for row in conn.execute(f'SELECT * FROM "{t}"'):
            values = []
            for col in cols:
                if col in hide and row[col] is not None:
                    values.append('[redacted]')
                else:
                    values.append(row[col])
            w.writerow(values)
    redacted += len(hide & set(cols))
print(f'  exported {len(tables)} tables'
      + (f' ({redacted} credential columns redacted)' if redacted else ''))
PY
ok "CSV export → csv/$STAMP/"

# ── 3 · Photos ──────────────────────────────────────────
# The one thing in this app whose bytes are not in the database. A row in
# unit_photos points at a file under data/photos/, so a snapshot without the
# directory restores a hobby log of missing images — and the app would say so
# on every unit page, which is a miserable way to find out.
#
# rsync into one shared directory rather than a per-stamp copy: the files are
# immutable and named by a random token, so the same photo is never written
# twice and thirty snapshots do not mean thirty copies of every picture.
PHOTO_DIR="$APP_DIR/data/photos"
if [ -d "$PHOTO_DIR" ]; then
  mkdir -p "$BACKUP_DIR/photos"
  # No --delete: a picture removed in the app must not vanish from the backup
  # the same night. Rotation is a decision, not a side effect.
  rsync -a "$PHOTO_DIR/" "$BACKUP_DIR/photos/"
  # `find | wc -l` rather than `ls | wc -l`: an empty directory makes ls print
  # nothing and this whole script runs under `set -e`.
  PHOTO_COUNT="$(find "$BACKUP_DIR/photos" -type f | wc -l | tr -d ' ')"
  ok "Photos → photos/ ($PHOTO_COUNT files)"
else
  note "No data/photos/ yet — nothing to carry"
fi

# ── 4 · Off-box copy ────────────────────────────────────
if [ -n "$BACKUP_DEST" ]; then
  # An array, not a string: a key path is a filename, and splitting one on
  # whitespace is how a backup starts failing for someone whose home directory
  # has a space in it.
  SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new)
  [ -n "$BACKUP_SSH_KEY" ] && SSH_OPTS+=(-i "$(expand "$BACKUP_SSH_KEY")")
  DEST_HOST="${BACKUP_DEST%%:*}"; DEST_PATH="${BACKUP_DEST#*:}"
  # DEST_PATH is deliberately expanded here, not on the remote — this side is
  # what knows the path. The inner quotes protect it once it lands there.
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "$DEST_HOST" "mkdir -p '$DEST_PATH/db' '$DEST_PATH/csv'" \
    || fail "Can't reach $DEST_HOST over SSH"
  # rsync -e takes a command string and splits it itself, so a key path with a
  # space is not supported on this leg. Rare, and it fails loudly if it happens.
  RSH="ssh ${SSH_OPTS[*]}"
  # No --delete: a deletion here must never wipe the backup copy.
  rsync -az -e "$RSH" "$SNAP" "$BACKUP_DEST/db/"
  rsync -az -e "$RSH" "$CSV_DIR" "$BACKUP_DEST/csv/"
  if [ -d "$BACKUP_DIR/photos" ]; then
    # Same as above: DEST_PATH is expanded here on purpose, and the inner
    # quotes protect it once it lands on the far side.
    # shellcheck disable=SC2029
    ssh "${SSH_OPTS[@]}" "$DEST_HOST" "mkdir -p '$DEST_PATH/photos'" \
      || fail "Can't reach $DEST_HOST over SSH"
    rsync -az -e "$RSH" "$BACKUP_DIR/photos/" "$BACKUP_DEST/photos/"
  fi
  ok "Shipped off-box → $BACKUP_DEST"
else
  note "BACKUP_DEST unset — local snapshot only. One box is not a backup."
fi

# ── 5 · Rotate ──────────────────────────────────────────
# `|| true` because on the very first run there is nothing to rotate, and ls
# exiting non-zero must not take the (already successful) backup down with it.
{ ls -1t "$BACKUP_DIR"/db/tracker-*.db 2>/dev/null || true; } \
  | tail -n +$((BACKUP_KEEP + 1)) | xargs -r rm -f
{ ls -1dt "$BACKUP_DIR"/csv/*/ 2>/dev/null || true; } \
  | tail -n +$((BACKUP_KEEP + 1)) | xargs -r rm -rf
# Only db/ and csv/ rotate. photos/ is shared across every snapshot — an old
# snapshot still points at the same files, so deleting them would hollow out
# every backup at once rather than the oldest.
ok "Kept newest $BACKUP_KEEP snapshots (photos/ is shared and never rotated)"

# ── 6 · Tell the app ────────────────────────────────────
# On a nightly cron this script's failure mode is silence — it reports loudly,
# but at 3am that is one line in a file nobody opens. So the home screen shows
# when the last backup finished, and this is how it finds out.
#
# A marker rather than the app reading $BACKUP_DIR itself: the container has
# only ./data and ./.env mounted, so /mnt/t7 does not exist from in there.
# Statting the snapshots would work in development and report "no backups,
# ever" on the one machine that matters.
#
# Written last on purpose. Under `set -euo pipefail` reaching this line means
# every step above succeeded, so a run that died half way leaves the marker at
# its old value and the home screen keeps saying the backup is overdue.
date -u +%Y-%m-%dT%H:%M:%SZ > "$APP_DIR/data/.last-backup"
ok "Marked the app's home screen"

bold ""
bold "Backup complete → $BACKUP_DIR"
echo "  Verify a restore with:  ./restore.sh --check $SNAP"
