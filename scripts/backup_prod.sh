#!/usr/bin/env bash
# Dump the production database (Fly Postgres) to a local directory.
#
# Opens a temporary `fly proxy` to the piclstats-db app, reads the connection
# string from the running app machine, runs pg_dump through the proxy, and
# keeps the newest N dumps. Fly's own daily volume snapshots are the only other
# backup this database has, and they are retained for a few days.
#
# Usage:  scripts/backup_prod.sh [DEST_DIR]
#   DEST_DIR   where dumps go (default: $PICLSTATS_BACKUP_DIR or ~/Backups/piclstats)
#   KEEP       how many dumps to keep (env, default 30)
#
# Needs: flyctl (logged in), pg_dump >= the server's major version
#        (brew install libpq; /opt/homebrew/opt/libpq/bin on PATH).
#
# Restore (into an empty database):
#   pg_restore --no-owner --no-privileges -d "$TARGET_URL" piclstats-YYYYmmdd-HHMMSS.dump
set -euo pipefail

APP="piclstats"
DB_APP="piclstats-db"
PORT="${PICLSTATS_BACKUP_PORT:-15433}"
DEST="${1:-${PICLSTATS_BACKUP_DIR:-$HOME/Backups/piclstats}}"
KEEP="${KEEP:-30}"

PATH="/opt/homebrew/opt/libpq/bin:$PATH"
command -v fly >/dev/null || { echo "flyctl not found" >&2; exit 1; }
command -v pg_dump >/dev/null || { echo "pg_dump not found (brew install libpq)" >&2; exit 1; }

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/piclstats-$STAMP.dump"

# Connection string lives only in the app's secrets; never echo it.
RAW="$(fly ssh console -a "$APP" -C "printenv DATABASE_URL" 2>/dev/null | grep '^postgres' || true)"
[ -n "$RAW" ] || { echo "could not read DATABASE_URL from the $APP machine (is it running?)" >&2; exit 1; }
URL="$(printf '%s' "$RAW" | sed -E "s#@[^/]+/#@localhost:${PORT}/#")"

fly proxy "${PORT}:5432" -a "$DB_APP" >/dev/null 2>&1 &
PROXY_PID=$!
trap 'kill "$PROXY_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
    nc -z localhost "$PORT" 2>/dev/null && break
    sleep 1
done
nc -z localhost "$PORT" 2>/dev/null || { echo "proxy to $DB_APP did not come up" >&2; exit 1; }

pg_dump --format=custom --no-owner --no-privileges --file "$OUT" "$URL"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"

# Retention: keep the newest $KEEP dumps.
ls -1t "$DEST"/piclstats-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
    rm -f "$old" && echo "removed $old"
done
