#!/usr/bin/env bash
# Run the stack from *published* images and check every user path. The
# release workflow runs this after publishing, on amd64 and on arm64; run
# it on any host to check a release before deploying it.
#   scripts/smoke-release.sh ghcr.io/parzon/triage-assistant 0.1.0
#
# Its own compose project and port: it never touches a running stack, and
# it removes everything it started (volumes included) when it exits.
set -euo pipefail
cd "$(dirname "$0")/.."

export IMAGE_PREFIX=${1:?usage: scripts/smoke-release.sh <image prefix> <tag>}
export IMAGE_TAG=${2:?usage: scripts/smoke-release.sh <image prefix> <tag>}
export HTTP_BIND=127.0.0.1 HTTP_PORT=${SMOKE_PORT:-8089} COMPOSE_PROFILES=mock
BASE=http://127.0.0.1:$HTTP_PORT
COMPOSE=(docker compose -p triage-assistant-smoke -f compose.yaml -f compose.prod.yaml)
[ -f .env ] || cp .env.example .env
trap '"${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true' EXIT
step() { printf '== %4ss  %s\n' "$SECONDS" "$*"; }

"${COMPOSE[@]}" pull --quiet api web migrate
"${COMPOSE[@]}" build --quiet mock-llm   # the provider stand-in is not a release artifact
step "pulled $IMAGE_PREFIX-{api,web}:$IMAGE_TAG ($(docker image inspect -f '{{.Os}}/{{.Architecture}}' "$IMAGE_PREFIX-api:$IMAGE_TAG"))"

"${COMPOSE[@]}" up -d --no-build
for _ in $(seq 90); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/ready")" = 200 ] && break
  sleep 2
done
curl -sf "$BASE/api/ready" || { "${COMPOSE[@]}" logs --tail 40; exit 1; }
echo
step "ready"

curl -sf "$BASE/" | grep -q '<div id="root">'
step "nginx serves the app"
curl -sf -X POST "$BASE/api/alerts" -H 'content-type: application/json' \
  -d '{"source":"smoke","severity":"high","message":"release smoke test"}' >/dev/null
curl -sf "$BASE/api/alerts?limit=5" | grep -q '"release smoke test"'
step "write + read"
curl -sfN -X POST "$BASE/api/chat/stream" -H 'content-type: application/json' \
  -d '{"message":"what is failing?"}' | grep -q '^event: done'
step "chat streams to the end"
step "PASS"
