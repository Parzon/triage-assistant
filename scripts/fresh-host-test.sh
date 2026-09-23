#!/usr/bin/env bash
# Proves the stack comes up on a machine with nothing but Docker, make and
# bash: a Docker-in-Docker container gets the committed tree (git archive
# HEAD - uncommitted files cannot make it pass), `cp .env.example .env`,
# `make prod-up`, and every user path is checked through nginx. Its Docker
# has no image cache, so everything builds and pulls from scratch.
#   make fresh-host-test                          ~5-10 minutes
#   DUMP=backups/<file>.dump make fresh-host-test  also restores a dump into
#                                                  the new host: a disaster-recovery rehearsal
set -euo pipefail
cd "$(dirname "$0")/.."
NAME=triage-fresh-host
step() { printf '== %4ss  %s\n' "$((SECONDS - T0))" "$*"; }
inside() { docker exec -w /srv/triage-assistant "$NAME" bash -c "$1"; }
T0=$SECONDS

docker rm -f -v "$NAME" >/dev/null 2>&1 || true
# /var/lib/docker as a volume: the inner Docker's overlay2 cannot sit on the
# outer one's.
docker run -d -q --privileged --name "$NAME" -v /var/lib/docker docker:28-dind >/dev/null
trap 'docker rm -f -v "$NAME" >/dev/null' EXIT
until docker exec "$NAME" docker info >/dev/null 2>&1; do sleep 1; done
docker exec "$NAME" apk add --no-cache -q bash make curl
step "host ready: $(docker exec "$NAME" docker version --format 'Docker {{.Server.Version}}'), compose $(docker exec "$NAME" docker compose version --short)"

docker exec "$NAME" mkdir -p /srv/triage-assistant
git archive --format=tar HEAD | docker exec -i "$NAME" tar -x -C /srv/triage-assistant
inside 'cp .env.example .env'
step "checkout of $(git rev-parse --short HEAD) + .env.example"

inside 'make prod-up >/tmp/prod-up.log 2>&1' || { inside 'tail -40 /tmp/prod-up.log'; exit 1; }
step "make prod-up done"

inside 'for i in $(seq 90); do [ "$(curl -s -o /dev/null -w "%{http_code}" localhost/api/ready)" = 200 ] && exit 0; sleep 2; done; exit 1'
step "ready: $(inside 'curl -s localhost/api/ready')"
inside 'curl -sf localhost/healthz >/dev/null && curl -sf localhost/ | grep -q "<div id=\"root\">"'
step "nginx serves the app"
created=$(inside "curl -sf -X POST localhost/api/alerts -H 'content-type: application/json' -d '{\"source\":\"fresh-host\",\"severity\":\"high\",\"message\":\"it works\"}'")
inside 'curl -sf "localhost/api/alerts?limit=5"' | grep -q '"fresh-host"'
step "write + read through nginx: $created"
inside "curl -sfN -X POST localhost/api/chat/stream -H 'content-type: application/json' -d '{\"message\":\"hello\"}'" | grep -q '^event: done'
step "chat streams to the end"

if [ -n "${DUMP:-}" ]; then
  docker exec "$NAME" mkdir -p /srv/triage-assistant/backups
  docker cp "$DUMP" "$NAME:/srv/triage-assistant/backups/"
  inside "YES=1 make restore ENV=prod file=backups/$(basename "$DUMP") >/tmp/restore.log 2>&1" || { inside 'tail -30 /tmp/restore.log'; exit 1; }
  step "restored $(basename "$DUMP"): $(inside "docker compose -p triage-assistant-prod -f compose.yaml -f compose.prod.yaml exec -T db sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"select count(*) from alerts\"'") alerts, ready $(inside 'curl -s -o /dev/null -w "%{http_code}" localhost/api/ready')"
fi
step "PASS"
