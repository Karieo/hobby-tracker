#!/bin/bash
# Restore — and, more importantly, *verify* — a backup snapshot.
#
# The spec asks for a restore to be verified once, early, while the database is
# still small and losing it wouldn't matter. That is only true if verifying is
# a command someone can actually run, so this is it. Run it after any change to
# the backup path, and any time you want to believe the backups again.
#
#   ./restore.sh --check                 verify the newest snapshot, change nothing
#   ./restore.sh --check <snapshot.db>   verify a specific one
#   ./restore.sh --list                  list available snapshots
#   ./restore.sh <snapshot.db>           actually restore it over the live database
#
# --check is the default posture on purpose: it opens the snapshot read-only,
# runs an integrity check, checks foreign keys, confirms the schema is at a
# migration version this code knows about, and prints the row counts so a
# truncated backup is obvious at a glance. It never writes anything.
#
# A real restore refuses to clobber a live database without first taking a
# safety copy of it, because "restore the wrong snapshot" is a much easier
# mistake to make than losing the database in the first place.
set -euo pipefail

on_exit() {
  local status=$?
  [ "$status" -ne 0 ] && printf '\033[31m✗ restore FAILED (exit %s)\033[0m\n' \
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
expand() { printf '%s' "${1/#\~/$HOME}"; }

PYTHON="$APP_DIR/venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)" || fail "python3 not found"

BACKUP_DIR="$(expand "$(env_value BACKUP_DIR)")"
DB_PATH="$APP_DIR/data/hobby_tracker.db"

newest_snapshot() {
  [ -n "$BACKUP_DIR" ] || fail "BACKUP_DIR is not set in .env"
  { ls -1t "$BACKUP_DIR"/db/tracker-*.db 2>/dev/null || true; } | head -1
}

verify() {
  local snap="$1"
  [ -f "$snap" ] || fail "No such snapshot: $snap"
  bold "── Verifying $(basename "$snap") ──"
  printf '  %s bytes, taken %s\n' \
    "$(stat -c %s "$snap" 2>/dev/null || stat -f %z "$snap")" \
    "$(date -r "$snap" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo '?')"
  "$PYTHON" - "$snap" "$APP_DIR" <<'PY'
import os, sqlite3, sys

snap, app_dir = sys.argv[1], sys.argv[2]
# Read-only: verifying a backup must never be able to damage it.
conn = sqlite3.connect(f'file:{snap}?mode=ro', uri=True)
conn.row_factory = sqlite3.Row

problems = []

if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
    problems.append('integrity_check failed — this snapshot is corrupt')

violations = conn.execute('PRAGMA foreign_key_check').fetchall()
if violations:
    problems.append(f'{len(violations)} foreign key violations')

# A snapshot from a newer schema than this checkout knows about would restore
# into an app that cannot read it. Better to say so now than at 2am.
try:
    applied = {r['version'] for r in conn.execute(
        'SELECT version FROM schema_migrations')}
except sqlite3.OperationalError:
    problems.append('no schema_migrations table — not a tracker database?')
    applied = set()

on_disk = {f.split('_')[0] for f in os.listdir(os.path.join(app_dir, 'migrations'))
           if f.endswith('.sql') and f.split('_')[0].isdigit()}
ahead = applied - on_disk
behind = on_disk - applied
if ahead:
    problems.append(f'snapshot is AHEAD of this code: migrations {sorted(ahead)} '
                    'are applied here but absent from migrations/')
print(f'  schema: {len(applied)} migrations applied'
      + (f', {len(behind)} pending on restore ({sorted(behind)})' if behind else ''))

tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' "
    "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}

print(f'  {len(tables)} tables:')
for t, n in counts.items():
    if n:
        print(f'      {t:24s} {n:>7}')
empty = [t for t, n in counts.items() if not n]
if empty:
    print(f'      ({len(empty)} empty: {", ".join(empty)})')

# The collection is the part that cannot be reconstructed from anywhere. Rules
# data can always be re-imported; "which of my Boyz are primed" cannot.
irreplaceable = sum(counts.get(t, 0) for t in
                    ('armies', 'kits', 'units', 'models', 'stage_events'))
if irreplaceable:
    print(f'  irreplaceable rows (collection + history): {irreplaceable}')
else:
    # An empty collection is either a fresh install or a catastrophe, and the
    # snapshot alone cannot tell you which. Ask the live database. Calling it a
    # failure unconditionally means the first verification anyone ever runs, on
    # the day they deploy, fails — which teaches them to ignore this check
    # before it has ever told them anything true.
    live_path = os.path.join(app_dir, 'data', 'hobby_tracker.db')
    live = None
    if os.path.exists(live_path):
        try:
            lc = sqlite3.connect(f'file:{live_path}?mode=ro', uri=True)
            live = sum(lc.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                       for t in ('armies', 'kits', 'units', 'models', 'stage_events'))
            lc.close()
        except sqlite3.Error:
            # Unreadable live database — mid-restore, locked, wrong schema.
            # Not knowing is not the same as knowing something is wrong.
            live = None

    if live:
        problems.append(
            f'the snapshot holds no collection data, but the live database has '
            f'{live} rows (armies/kits/units/models/stage_events). This '
            f'snapshot would lose all of it — do not restore from it.')
    elif live == 0:
        print('  no collection data yet — matches the live database, so this '
              'is a fresh install, not a loss.')
    else:
        print('  no collection data. Could not read the live database to '
              'compare, so this is unverified rather than wrong.')

if problems:
    print('\n  \033[31mPROBLEMS\033[0m')
    for p in problems:
        print(f'   ✗ {p}')
    raise SystemExit(1)
print('\n  \033[32mSnapshot is intact and restorable.\033[0m')
PY
}

case "${1:---check}" in
  --list)
    [ -n "$BACKUP_DIR" ] || fail "BACKUP_DIR is not set in .env"
    bold "Snapshots in $BACKUP_DIR/db"
    { ls -1t "$BACKUP_DIR"/db/tracker-*.db 2>/dev/null || true; } \
      | while read -r f; do printf '  %s  %s bytes\n' "$(basename "$f")" \
          "$(stat -c %s "$f" 2>/dev/null || stat -f %z "$f")"; done
    ;;

  --check)
    SNAP="${2:-$(newest_snapshot)}"
    [ -n "$SNAP" ] || fail "No snapshots found in $BACKUP_DIR/db — run ./backup.sh first"
    verify "$SNAP"
    ;;

  -h|--help)
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    ;;

  *)
    SNAP="$1"
    verify "$SNAP"
    echo
    # Restoring the wrong snapshot is an easier mistake than losing the DB, so
    # the live database is never simply overwritten.
    if [ -f "$DB_PATH" ]; then
      SAFETY="$DB_PATH.replaced-$(date +%Y%m%d-%H%M%S)"
      "$PYTHON" - "$DB_PATH" "$SAFETY" <<'PY'
import sqlite3, sys
s = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
d = sqlite3.connect(sys.argv[2])
with d:
    s.backup(d)
s.close(); d.close()
PY
      note "Current database set aside at $(basename "$SAFETY")"
    fi
    # Copy via the backup API rather than cp, for the same reason backup.sh
    # does: the source may have a live WAL alongside it.
    rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"
    "$PYTHON" - "$SNAP" "$DB_PATH" <<'PY'
import sqlite3, sys
s = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
d = sqlite3.connect(sys.argv[2])
with d:
    s.backup(d)
s.close(); d.close()
PY
    ok "Restored $(basename "$SNAP") → data/hobby_tracker.db"

    # The other half. unit_photos rows point at files under data/photos/, and
    # a database restored without them is a hobby log of missing pictures —
    # which the unit page will say out loud, but only after Clay has gone
    # looking. The snapshot lives at <backup>/db/tracker-*.db and the photos
    # beside it at <backup>/photos/, shared across every snapshot.
    PHOTO_SRC="$(dirname "$(dirname "$SNAP")")/photos"
    if [ -d "$PHOTO_SRC" ]; then
      mkdir -p "$APP_DIR/data/photos"
      # No --delete: a photo taken since this snapshot is not something a
      # restore should throw away. Extra pictures are harmless; the rows that
      # named them are what decide what the app shows.
      rsync -a "$PHOTO_SRC/" "$APP_DIR/data/photos/"
      RESTORED="$(find "$APP_DIR/data/photos" -type f | wc -l | tr -d ' ')"
      ok "Photos → data/photos/ ($RESTORED files)"
    else
      note "No photos/ beside the snapshot — restoring the database only"
    fi

    bold ""
    bold "Restart the app, then check the armies page looks right."
    ;;
esac
