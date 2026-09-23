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
# Everything on loopback and on ports of its own, the TLS edge included;
# the bundled identity provider signs a demo user in.
export COMPOSE_PROFILES=mock,edge,idp SITE_ADDRESS=localhost HTTP_BIND=127.0.0.1 EDGE_BIND=127.0.0.1
export HTTP_PORT=${SMOKE_PORT:-18088} EDGE_HTTP_PORT=${SMOKE_HTTP_PORT:-18080} EDGE_HTTPS_PORT=${SMOKE_HTTPS_PORT:-18443}
BASE=https://localhost:$EDGE_HTTPS_PORT
export PUBLIC_URL=$BASE
COMPOSE=(docker compose -p triage-assistant-smoke -f compose.yaml -f compose.prod.yaml)
[ -f .env ] || cp .env.example .env
PASSWORD=${DEMO_USER_PASSWORD:-$(sed -n 's/^DEMO_USER_PASSWORD=//p' .env)}
CA=$(mktemp) JAR=$(mktemp)
trap '"${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true; rm -f "$CA" "$JAR"' EXIT
step() { printf '== %4ss  %s\n' "$SECONDS" "$*"; }
get() { curl -sf --cacert "$CA" "$@"; }
# As the signed-in user: the session cookie, and this site's Origin (the
# api refuses state-changing requests without it).
user() { get -b "$JAR" -H "Origin: $BASE" "$@"; }
# shellcheck source=scripts/lib/session.sh
. scripts/lib/session.sh

"${COMPOSE[@]}" pull --quiet api web migrate edge keycloak
"${COMPOSE[@]}" build --quiet mock-llm   # the provider stand-in is not a release artifact
step "pulled $IMAGE_PREFIX-{api,web,edge}:$IMAGE_TAG ($(docker image inspect -f '{{.Os}}/{{.Architecture}}' "$IMAGE_PREFIX-api:$IMAGE_TAG"))"

"${COMPOSE[@]}" up -d --no-build
for _ in $(seq 90); do
  "${COMPOSE[@]}" cp edge:/data/caddy/pki/authorities/local/root.crt "$CA" >/dev/null 2>&1 \
    && [ "$(curl -s --cacert "$CA" -o /dev/null -w '%{http_code}' "$BASE/api/ready")" = 200 ] && break
  sleep 2
done
get "$BASE/api/ready" || { "${COMPOSE[@]}" logs --tail 40; exit 1; }
echo
step "ready over HTTPS (certificate verified against the edge's CA)"

[ "$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$EDGE_HTTP_PORT/")" = 308 ]
step "plain HTTP is redirected to HTTPS"
get "$BASE/" | grep -q '<div id="root">'
step "the app is served"

[ "$(curl -s --cacert "$CA" -o /dev/null -w '%{http_code}' "$BASE/api/alerts")" = 401 ]
step "the api refuses anyone not signed in"
for _ in $(seq 90); do
  [ "$(curl -s --cacert "$CA" -o /dev/null -w '%{http_code}' "$BASE/auth/realms/triage/.well-known/openid-configuration")" = 200 ] && break
  sleep 2
done
sign_in "$BASE" alice "$PASSWORD" "$JAR" --cacert "$CA" || { "${COMPOSE[@]}" logs --tail 40 api keycloak; exit 1; }
step "signed in as alice through the identity provider: $(user "$BASE/api/me" | grep -o '"teams":.*')"
[ "$(curl -s --cacert "$CA" -o /dev/null -w '%{http_code}' "$BASE/auth/admin/")" = 404 ]
step "the identity provider's admin console is not exposed"

user -X POST "$BASE/api/alerts" -H 'content-type: application/json' \
  -d '{"team":"payments","source":"smoke","severity":"high","message":"release smoke test"}' >/dev/null
user "$BASE/api/alerts?limit=5" | grep -q '"release smoke test"'
step "write + read"
user -N -X POST "$BASE/api/chat/stream" -H 'content-type: application/json' \
  -d '{"message":"what is failing?"}' | grep -q '^event: done'
step "chat streams to the end"
step "PASS"
