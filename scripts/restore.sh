#!/usr/bin/env bash
# Replace the database's contents with a dump made by backup.sh.
#   make restore file=backups/triage-prod-20260923T050000Z.dump [ENV=prod]
#
# 1. stops the api, so nothing writes meanwhile (nginx answers with its
#    JSON 502 in the meantime)
# 2. restores in ONE transaction: all or nothing - a failed restore leaves
#    the database as it was
# 3. runs migrations: a dump from an older release is brought up to date
# 4. starts the api again
# On a new host: `make prod-up` first (it creates the roles the grants in
# the dump refer to), then this.
set -euo pipefail
cd "$(dirname "$0")/.."
STACK=${STACK:-docker compose}
file=${1:?usage: scripts/restore.sh <dump file>}
[ -s "$file" ] || { echo "no such dump: $file" >&2; exit 1; }

if [ "${YES:-}" != 1 ]; then
  read -r -p "Replace the database with $file? Type yes: " answer
  [ "$answer" = yes ] || { echo "aborted"; exit 1; }
fi

start=$SECONDS
$STACK stop api
$STACK exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --single-transaction --exit-on-error' < "$file"
echo "restored $file in $((SECONDS - start))s"
$STACK run --rm migrate
$STACK up -d --no-deps --wait api
echo "api healthy again after $((SECONDS - start))s"
