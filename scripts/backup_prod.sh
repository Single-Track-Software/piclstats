#!/usr/bin/env bash
# Dump the production database (Fly Managed Postgres) to a local directory.
#
# Managed Postgres already keeps its own backups (daily full + hourly
# incremental; `fly mpg backup list n83v7rgj26xr5gxk`). This is the
# independent copy outside Fly. It opens a temporary `fly mpg proxy`, reads the
# app's own DATABASE_URL from a running app machine (never printed), runs
# pg_dump through the proxy, and keeps the newest N dumps. The last line of
# output is the dump path (scripts/refresh_nas.sh reads it).
#
# Usage:  scripts/backup_prod.sh [DEST_DIR]
#   DEST_DIR   where dumps go (default: $PICLSTATS_BACKUP_DIR or ~/Backups/piclstats)
#   KEEP       how many dumps to keep (env, default 30)
#
# Needs: flyctl (logged in), pg_dump >= the server's major version (17)
#        (brew install libpq; /opt/homebrew/opt/libpq/bin), jq, nc.
#
# Restore into an empty database (skip the two Fly-only extensions; see
# refresh_nas.sh for a full example):
#   pg_restore --no-owner --no-privileges -d "$TARGET_URL" piclstats-YYYYmmdd-HHMMSS.dump
set -euo pipefail

APP="piclstats"
CLUSTER="${PICLSTATS_MPG_CLUSTER:-n83v7rgj26xr5gxk}"
PORT="${PICLSTATS_BACKUP_PORT:-16433}"
DEST="${1:-${PICLSTATS_BACKUP_DIR:-$HOME/Backups/piclstats}}"
KEEP="${KEEP:-30}"

PATH="/opt/homebrew/opt/libpq/bin:$PATH"
for tool in fly pg_dump jq nc python3; do
    command -v "$tool" >/dev/null || { echo "$tool not found" >&2; exit 1; }
done

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/piclstats-$STAMP.dump"

# The app's DATABASE_URL holds the cluster user's credentials. Read it from a
# started app machine through the Machines API (no WireGuard needed). Never echo it.
MACHINE="$(fly machines list -a "$APP" --json | jq -r '[.[] | select(.state == "started")][0].id // empty')"
[ -n "$MACHINE" ] || { echo "no started $APP machine to read DATABASE_URL from" >&2; exit 1; }
RAW="$(fly machine exec "$MACHINE" -a "$APP" "printenv DATABASE_URL" 2>/dev/null | grep -E '^postgres' | tail -1 || true)"
[ -n "$RAW" ] || { echo "could not read DATABASE_URL from machine $MACHINE" >&2; exit 1; }
URL="$(python3 - "$RAW" "$PORT" <<'PY'
import sys, urllib.parse as u
p = u.urlsplit(sys.argv[1])
q = dict(u.parse_qsl(p.query))
q["sslmode"] = "disable"  # the proxy is local; it carries TLS to Fly
print(u.urlunsplit(("postgresql", f"{p.username}:{p.password}@localhost:{sys.argv[2]}", p.path, u.urlencode(q), "")))
PY
)"

PROXY_LOG="$(mktemp)"
fly mpg proxy "$CLUSTER" --local-port "$PORT" >"$PROXY_LOG" 2>&1 &
PROXY_PID=$!
trap 'kill "$PROXY_PID" 2>/dev/null; wait "$PROXY_PID" 2>/dev/null || true; rm -f "$PROXY_LOG"' EXIT

# The first proxy of a session can take well over 30 s (it sets up the
# tunnel); stop early if the proxy process exits.
for _ in $(seq 1 90); do
    nc -z localhost "$PORT" 2>/dev/null && break
    kill -0 "$PROXY_PID" 2>/dev/null || break
    sleep 1
done
if ! nc -z localhost "$PORT" 2>/dev/null; then
    echo "proxy to cluster $CLUSTER did not come up:" >&2
    sed 's/^/  /' "$PROXY_LOG" >&2
    exit 1
fi

pg_dump --format=custom --no-owner --no-privileges --file "$OUT" "$URL"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))" >&2

# Retention: keep the newest $KEEP dumps.
ls -1t "$DEST"/piclstats-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
    rm -f "$old" && echo "removed $old" >&2
done

echo "$OUT"
