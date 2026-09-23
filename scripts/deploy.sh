#!/usr/bin/env bash
# Deploy a release to this host without refusing requests.
#
#   make deploy tag=1.4.0          images from $IMAGE_PREFIX (ghcr.io/..., see .env)
#   PULL=0 make deploy tag=local   images already on this host (local testing)
#
# `docker compose up -d` replaces a container by stopping it first: the old
# api drains its in-flight requests while nothing accepts new ones (measured:
# 502s for 6.7s, and for as long as the longest answer in flight). Instead:
#   1. migrate (the old api is still serving: migrations must be backward
#      compatible - add first, remove in a later release)
#   2. start the new api NEXT TO the old one and wait until it is healthy
#   3. wait for nginx to resolve it (resolver valid=10s in default.conf)
#   4. stop the old api: it finishes its in-flight requests (streams
#      included, up to graceful_timeout) while nginx sends new ones to the new
#   5. replace nginx itself - the one step with a gap: one container owns the
#      published port (measured below; zero only behind a load balancer)
# Rollback = deploy the previous tag (and never a migration that removed
# something the previous tag still needs).
set -euo pipefail
cd "$(dirname "$0")/.."

TAG=${1:?usage: scripts/deploy.sh <image tag>}
export IMAGE_TAG=$TAG
PROJECT=triage-assistant-prod
COMPOSE=(docker compose -p "$PROJECT" -f compose.yaml -f compose.prod.yaml)
step() { printf '\n== %s  %s\n' "$(date -u +%T)" "$*"; }

containers() {  # running containers of one service
  docker ps -q --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=$1"
}

healthy() {  # healthy <container> <seconds>: 0 once healthy, 1 if unhealthy or too slow
  local s end=$((SECONDS + $2))
  until s=$(docker inspect -f '{{.State.Health.Status}}' "$1") && [ "$s" = healthy ]; do
    { [ "$s" = unhealthy ] || [ $SECONDS -gt $end ]; } && return 1
    sleep 1
  done
}

if [ "${PULL:-1}" = 1 ]; then
  step "pull $IMAGE_TAG"
  "${COMPOSE[@]}" pull --quiet api web migrate
fi

step "migrate"
"${COMPOSE[@]}" run --rm migrate

mapfile -t old < <(containers api)
step "start the new api next to the ${#old[@]} running"
"${COMPOSE[@]}" up -d --no-deps --no-recreate --scale "api=$((${#old[@]} + 1))" api
new=$(comm -13 <(printf '%s\n' "${old[@]}" | sort) <(containers api | sort))
[ -n "$new" ] || { echo "no new api container appeared" >&2; exit 1; }
if ! healthy "$new" 90; then
  echo "the new api did not become healthy: removing it; the old one keeps serving" >&2
  docker logs --tail 30 "$new" >&2
  docker rm -f "$new" >/dev/null
  exit 1
fi
echo "healthy: $(docker inspect -f '{{.Name}} {{.Config.Image}}' "$new")"

step "wait for nginx to resolve it"
sleep 11

if [ ${#old[@]} -gt 0 ]; then
  step "drain and remove the old api"
  # SIGTERM: gunicorn stops accepting, finishes in-flight requests.
  # --time: Docker's SIGKILL deadline, above gunicorn's graceful_timeout.
  docker stop --time 130 "${old[@]}" >/dev/null
  docker rm "${old[@]}" >/dev/null
fi
"${COMPOSE[@]}" up -d --no-deps --no-recreate --scale api=1 api

step "replace nginx"
"${COMPOSE[@]}" up -d --no-deps web
healthy "$(containers web)" 30 || { echo "nginx is not healthy: make prod-logs S=web" >&2; exit 1; }

# Every later compose command (make prod-up, make restore, a reboot's
# restart) must run the same images: record the tag where compose reads it.
# Without this, the next `docker compose up` quietly rolls back to the
# default tag (measured: make restore did exactly that).
if [ "${RECORD_TAG:-1}" = 1 ]; then
  if grep -q '^IMAGE_TAG=' .env; then sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$TAG/" .env; else echo "IMAGE_TAG=$TAG" >> .env; fi
fi

step "done: $("${COMPOSE[@]}" ps --format '{{.Service}} {{.Image}} {{.Status}}' api web | tr '\n' ';')"
