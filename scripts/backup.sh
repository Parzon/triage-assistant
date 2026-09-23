#!/usr/bin/env bash
# Back up the database: backups/triage-<env>-<UTC time>.dump (pg_dump's
# custom format: compressed, restorable table by table).
#   make backup [ENV=prod]        keeps the newest KEEP dumps (default 14)
#
# pg_dump reads one consistent snapshot and does not block the app. The
# dump holds the schema (alembic_version included), the data and the grants
# - not the roles, which come from infra/postgres/initdb on a fresh volume.
# A dump on this disk dies with this disk: copy backups/ off the host
# (docs/runbooks/demo-vm.md), and test restores, not just backups.
set -euo pipefail
cd "$(dirname "$0")/.."
STACK=${STACK:-docker compose}
NAME=${NAME:-dev}
KEEP=${KEEP:-14}

mkdir -p backups
out=backups/triage-$NAME-$(date -u +%Y%m%dT%H%M%SZ).dump
start=$SECONDS
# Written under a temporary name: a dump interrupted halfway must never look
# like a good one.
$STACK exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$out.partial"
mv "$out.partial" "$out"
# Reads the dump's table of contents back: catches truncated files.
entries=$($STACK exec -T db pg_restore --list < "$out" | grep -vc '^;')
echo "wrote $out: $(du -h "$out" | cut -f1), $entries entries, $((SECONDS - start))s"

ls -1t backups/triage-"$NAME"-*.dump | tail -n +$((KEEP + 1)) | xargs -r rm -v --
