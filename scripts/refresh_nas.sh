#!/usr/bin/env bash
# Replace the NAS verification database with a fresh copy of production.
#
# The NAS drifts from prod: nightly race loads and admin config edits (loop
# distances, lap 1 adjustments) only happen on prod. Verification against a
# stale copy gives wrong answers, so refresh before analysis or PR checks.
#
# Steps: back up the target, dump prod (scripts/backup_prod.sh), restore in a
# single transaction (any failure leaves the target untouched). Fly-only
# extensions (pg_stat_monitor, pgaudit) are skipped: the NAS doesn't have them.
#
# Usage:  scripts/refresh_nas.sh [TARGET_URL]
#   TARGET_URL  default: PICLSTATS_DATABASE_URL from .env
#   KEEP_NAS    how many pre-refresh NAS backups to keep (env, default 5)
set -euo pipefail

cd "$(dirname "$0")/.."
PATH="/opt/homebrew/opt/libpq/bin:$PATH"
DEST="${PICLSTATS_BACKUP_DIR:-$HOME/Backups/piclstats}"
KEEP_NAS="${KEEP_NAS:-5}"

TARGET="${1:-$(grep -E '^PICLSTATS_DATABASE_URL=' .env | cut -d= -f2-)}"
TARGET="${TARGET/postgresql+psycopg:\/\//postgresql://}"
[ -n "$TARGET" ] || { echo "no target URL (pass one or set PICLSTATS_DATABASE_URL in .env)" >&2; exit 1; }
# Never restore onto production.
case "$TARGET" in
    *flympg*|*fly.dev*|*.internal*|*flycast*|*localhost:16433*)
        echo "refusing: target looks like production" >&2; exit 1 ;;
esac

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
BEFORE="$DEST/nas-before-refresh-$STAMP.dump"
pg_dump --format=custom --no-owner --no-privileges --file "$BEFORE" "$TARGET"
echo "backed up target to $BEFORE" >&2
ls -1t "$DEST"/nas-before-refresh-*.dump | tail -n +"$((KEEP_NAS + 1))" | xargs -r rm -f

DUMP="$(scripts/backup_prod.sh "$DEST" | tail -1)"
[ -f "$DUMP" ] || { echo "prod dump failed" >&2; exit 1; }

LIST="$(mktemp)"
trap 'rm -f "$LIST"' EXIT
pg_restore --list "$DUMP" | grep -viE "(EXTENSION|COMMENT) - (EXTENSION )?(pg_stat_monitor|pgaudit)" > "$LIST"
pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction \
    -L "$LIST" -d "$TARGET" "$DUMP"

psql "$TARGET" -Atc "
    SELECT 'migration ' || version_num FROM alembic_version
    UNION ALL SELECT 'results   ' || count(*) FROM results
    UNION ALL SELECT 'events    ' || count(*) FROM events" >&2
echo "NAS now matches prod as of $STAMP" >&2
