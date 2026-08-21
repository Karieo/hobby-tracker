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

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
note() { printf '  \033[33m•\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗ %s\033[0m\n' "$*"; exit 1; }

env_value() { grep "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-; }
expand()    { eval echo "$1"; }

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
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' "
    "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
for t in tables:
    rows = conn.execute(f'SELECT * FROM "{t}"').fetchall()
    with open(os.path.join(out, f'{t}.csv'), 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow([d[0] for d in conn.execute(f'SELECT * FROM "{t}" LIMIT 0').description])
        w.writerows([tuple(r) for r in rows])
print(f'  exported {len(tables)} tables')
PY
ok "CSV export → csv/$STAMP/"

# ── 3 · Off-box copy ────────────────────────────────────
if [ -n "$BACKUP_DEST" ]; then
  SSH_OPTS="-o BatchMode=yes -o StrictHostKeyChecking=accept-new"
  [ -n "$BACKUP_SSH_KEY" ] && SSH_OPTS="$SSH_OPTS -i $(expand "$BACKUP_SSH_KEY")"
  DEST_HOST="${BACKUP_DEST%%:*}"; DEST_PATH="${BACKUP_DEST#*:}"
  ssh $SSH_OPTS "$DEST_HOST" "mkdir -p '$DEST_PATH/db' '$DEST_PATH/csv'" \
    || fail "Can't reach $DEST_HOST over SSH"
  # No --delete: a deletion here must never wipe the backup copy.
  rsync -az -e "ssh $SSH_OPTS" "$SNAP" "$BACKUP_DEST/db/"
  rsync -az -e "ssh $SSH_OPTS" "$CSV_DIR" "$BACKUP_DEST/csv/"
  ok "Shipped off-box → $BACKUP_DEST"
else
  note "BACKUP_DEST unset — local snapshot only. One box is not a backup."
fi

# ── 4 · Rotate ──────────────────────────────────────────
ls -1t "$BACKUP_DIR"/db/tracker-*.db 2>/dev/null | tail -n +$((BACKUP_KEEP + 1)) | xargs -r rm -f
ls -1dt "$BACKUP_DIR"/csv/*/ 2>/dev/null | tail -n +$((BACKUP_KEEP + 1)) | xargs -r rm -rf
ok "Kept newest $BACKUP_KEEP snapshots"
