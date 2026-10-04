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
#   5. replace nginx: behind the TLS edge no request fails (the edge retries);
#      where nginx publishes the port itself, ~0.3 s of refused connections;
#      then the edge, only if its image changed (~2 s of refused connections)
# Rollback = deploy the previous tag. The database stays as it is (the
# code rolls back, the schema does not), so the previous tag must be able to
# run on it: the expand/contract rule.
set -euo pipefail
cd "$(dirname "$0")/.."

TAG=${1:?usage: scripts/deploy.sh <image tag>}
export IMAGE_TAG=$TAG
PROJECT=triage-assistant-prod
COMPOSE=(docker compose -p "$PROJECT" -f compose.yaml -f compose.prod.yaml)
step() { printf '\n== %s  %s\n' "$(date -u +%T)" "$*"; }

# The api refuses the template's secrets at startup (ADR-0023), but only the
# ones it receives. The database owner's, the monitor's, Grafana's and
# Keycloak's never reach it, so they are checked here.
if left=$(grep -oE '^[A-Z0-9_]+=change-me' .env 2>/dev/null); then
  echo ".env still holds the template's values for: $(cut -d= -f1 <<< "$left" | tr '\n' ' ')" >&2
  echo "Generate them as make .env does (openssl rand -hex 24), then deploy again." >&2
  exit 1
fi

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

# The database's image is not a release image: it comes from the compose
# files, and changes with them (an extension, such as pgvector's build).
# Replacing it restarts Postgres, a few seconds of errors, so it is a
# deliberate step, never a side effect of a deploy. Without it, the
# migration fails on an extension the old image lacks.
db=$(containers db)
if [ -n "$db" ]; then
  running=$(docker inspect -f '{{.Config.Image}}' "$db")
  wanted=$("${COMPOSE[@]}" config --images db | head -n1)
  if [ "$running" != "$wanted" ]; then
    echo "the database runs $running; these compose files want $wanted." >&2
    echo "Replace it first (Postgres restarts: a few seconds of errors), then deploy again:" >&2
    echo "  ${COMPOSE[*]} up -d --no-deps db" >&2
    echo "Rolling back? Keep the database's image: deploy the old tag from this checkout." >&2
    exit 1
  fi
fi

if [ "${PULL:-1}" = 1 ]; then
  step "pull $IMAGE_TAG"
  "${COMPOSE[@]}" pull --quiet api web migrate edge
fi

# Every image of this release must be on the host now. A missing one would not
# fail the steps below: compose builds a service that has a build section, so
# this checkout would run under the release's name (measured: `run migrate`
# built ...-api:<tag> from the working tree). make prod-up refuses the same.
missing=$("${COMPOSE[@]}" config --images | grep -E -- "-(api|web|edge):${TAG//./\\.}\$" | sort -u |
  while read -r image; do docker image inspect "$image" >/dev/null 2>&1 || echo "$image"; done)
if [ -n "$missing" ]; then
  echo "not on this host: $(tr '\n' ' ' <<< "$missing")" >&2
  echo "Deploy with PULL=1 (the default), or build this checkout first: make prod-build." >&2
  exit 1
fi

# A rollback: the database already carries a newer release's migrations,
# which this image's Alembic has never heard of ("Can't locate revision").
# Run this release's code on the newer schema, without migrating. That is
# safe only if this release can run on it - down to the release before a
# contract migration, never past it (v0.3.0 -> v0.2.0 yes, -> v0.1.0 no).
# Migrations are never reversed automatically: `alembic downgrade` is a
# deliberate, separate step.
current=$("${COMPOSE[@]}" exec -T db sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version" 2>/dev/null' || true)
ahead=no
if [ -n "$current" ] && ! shown=$("${COMPOSE[@]}" run --rm --no-deps -T migrate alembic show "$current" 2>&1); then
  # Only this message means "a newer release migrated it". Any other failure
  # (Docker unavailable, an image that does not start) is a failed deploy,
  # not a rollback: reading it as one would skip the migration.
  if ! grep -q "Can't locate revision" <<< "$shown"; then
    printf '%s\n' "$shown" >&2
    echo "could not check the database's revision against $TAG: deploy stopped" >&2
    exit 1
  fi
  ahead=yes
fi
if [ "$ahead" = yes ]; then
  step "the database ($current) is ahead of $TAG: rolling back the code only, no migration"
else
  step "migrate"
  "${COMPOSE[@]}" run --rm migrate
fi

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
# The edge holds requests and retries while nginx restarts (lb_try_duration
# in tools/edge/Caddyfile): measured, no request failed through the edge.
"${COMPOSE[@]}" up -d --no-deps web
healthy "$(containers web)" 30 || { echo "nginx is not healthy: make prod-logs S=web" >&2; exit 1; }

# The edge owns the published ports: replacing it refuses connections for
# ~2 s (measured). Only when it changed. CI stamps the edge image with a hash
# of its build inputs (tools/edge/**): every release rebuilds it, and a
# rebuild never has the same layers (COPY records file times, and each
# checkout gets new ones), so layers alone would replace it every time.
# Local builds carry no stamp; for them, identical layers mean unchanged.
edge=$(containers edge)
if [ -n "$edge" ]; then
  inputs='{{index .Config.Labels "io.triage-assistant.inputs"}}'
  running_image=$(docker inspect -f '{{.Image}}' "$edge")
  # The image name as compose resolves it (IMAGE_PREFIX usually lives only
  # in .env, which this shell never reads): `config --images edge` lists the
  # edge and its dependencies; the edge's own ends in -edge:<tag>.
  image=$("${COMPOSE[@]}" config --images edge | grep -m1 -- "-edge:$TAG\$" || true)
  docker image inspect "$image" >/dev/null 2>&1 || {
    echo "cannot inspect the edge image '${image:-?}' that was just pulled" >&2; exit 1; }
  stamp=$(docker image inspect -f "$inputs" "$running_image")
  if [ -n "$stamp" ] && [ "$stamp" != "<no value>" ] \
      && [ "$stamp" = "$(docker image inspect -f "$inputs" "$image")" ]; then
    changed=no
  elif [ "$(docker image inspect -f '{{json .RootFS.Layers}}' "$running_image")" \
      = "$(docker image inspect -f '{{json .RootFS.Layers}}' "$image")" ]; then
    changed=no
  else
    changed=yes
  fi
  if [ "$changed" = yes ]; then
    step "replace the edge (its image changed)"
    "${COMPOSE[@]}" up -d --no-deps edge
    healthy "$(containers edge)" 30 || { echo "the edge is not healthy: make prod-logs S=edge" >&2; exit 1; }
  else
    step "the edge is unchanged: left running"
  fi
fi

# Every later compose command (make prod-up, make restore, a reboot's
# restart) must run the same images: record the tag where compose reads it.
# Without this, the next `docker compose up` quietly rolls back to the
# default tag (measured: make restore did exactly that).
if [ "${RECORD_TAG:-1}" = 1 ]; then
  if grep -q '^IMAGE_TAG=' .env; then sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$TAG/" .env; else echo "IMAGE_TAG=$TAG" >> .env; fi
fi

step "done: $("${COMPOSE[@]}" ps --format '{{.Service}} {{.Image}} {{.Status}}' api web | tr '\n' ';')"
