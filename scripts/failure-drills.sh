#!/usr/bin/env bash
# Failure drills ("game day"): inject one fault at a time into the
# production-shaped stack, record what a user sees, restore.
#
#   make drills                                  every drill (~15 minutes)
#   make drills d="redis-hang deploy"            a selection
#
# Needs the prod stack with the mock LLM and rate limits raised, so the
# probes themselves are never rate limited:
#   ALERTS_RATE_LIMIT=1000000 CHAT_RATE_LIMIT=1000000 make prod-up
#
# Two kinds of drill:
#   dependency faults  one probe of each user path while the fault is active:
#                      health (liveness), ready (readiness, names the broken
#                      dependency), read (GET /api/alerts), chat (a stream)
#   in-flight faults   STREAMS slow streams are running when the fault hits,
#                      and /api/health is probed 4x a second throughout: how
#                      many streams finished, and how long new requests failed
#   freezes under load 20 threads read steadily (scripts/drills/steady_reads.py)
#                      while a dependency is frozen for 20s: what the requests
#                      caught mid-flight got, and whether any database
#                      connection is still held once it is over (a leak)
set -uo pipefail
cd "$(dirname "$0")/.."

BASE=${BASE:-http://127.0.0.1:${HTTP_PORT:-8088}}
PROJECT=triage-assistant-prod
COMPOSE="docker compose -p $PROJECT -f compose.yaml -f compose.prod.yaml"
NETSHOOT=nicolaka/netshoot:v0.14
C() { echo "$PROJECT-$1-1"; }
# The api is looked up by its compose labels before every drill: a rolling
# deploy leaves it named api-2, not api-1.
api() {
  docker ps -q --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=api" | head -1
}
API=$(api)

healthy() {  # wait until every core container is healthy: each drill starts from a sound stack
  local c s start=$SECONDS
  for c in db pgbouncer redis mock-llm api web; do
    until s=$(docker inspect -f '{{.State.Health.Status}}' "$(if [ $c = api ]; then api; else C "$c"; fi)" 2>/dev/null) \
        && [ "$s" = healthy ]; do
      [ $((SECONDS - start)) -gt 90 ] && { echo "$c is ${s:-missing}"; return 1; }
      sleep 0.5
    done
  done
}

mock() {  # mock <config|reset> <json>: the mock's admin API, from inside its own container
  docker exec "$(C mock-llm)" python -c "import sys,urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:8020/_admin/'+sys.argv[1], data=sys.argv[2].encode(), headers={'content-type':'application/json'})).read()" "$1" "$2" >/dev/null
}

last_event() {  # the last SSE event of a saved stream; error events with their code
  local ev; ev=$(grep -oE '^event: [a-z_]+' "$1" | tail -1 | cut -d' ' -f2)
  [ "$ev" = "error" ] && ev="error:$(grep -A1 '^event: error' "$1" | grep -oE '"code": ?"[a-z_]+"' | tail -1 | grep -oE '[a-z_]+"$' | tr -d '"')"
  echo "${ev:-none}"
}

probe() {
  local health ready rd code t body
  health=$(curl -s -o /dev/null -w '%{http_code}/%{time_total}s' --max-time 10 "$BASE/api/health")
  ready=$(curl -s -w ' %{http_code}' --max-time 10 "$BASE/api/ready" | sed -E 's/.*"database": ?"([a-z_]+)", ?"redis": ?"([a-z_]+)".* ([0-9]+)$/\3 db=\1 redis=\2/')
  rd=$(curl -s -o /dev/null -w '%{http_code}/%{time_total}s' --max-time 20 "$BASE/api/alerts?limit=5")
  body=$(mktemp)
  read -r code t < <(curl -sN -o "$body" -w '%{http_code} %{time_total}' --max-time 150 -X POST "$BASE/api/chat/stream" \
    -H 'content-type: application/json' -d '{"message":"drill"}')
  printf 'health %s · ready %s · read %s · chat %s %s/%ss' "$health" "$ready" "$rd" "$code" "$(last_event "$body")" "$t"
  rm -f "$body"
}

recovered() {  # seconds until /api/ready answers 200 again
  local start=$SECONDS
  until [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$BASE/api/ready")" = "200" ]; do
    sleep 0.5; [ $((SECONDS - start)) -gt 180 ] && { echo ">180s"; return; }
  done
  echo "$((SECONDS - start))s"
}

drill() {  # drill <name> <inject> <restore>
  echo ">> $(date -u +%T) $1" >&2
  local err
  if ! err=$(eval "$2" 2>&1); then  # a fault that was not injected must not be reported
    printf '| %s | INJECT FAILED: %s | |\n' "$1" "$(echo "$err" | tail -1)"; return
  fi
  sleep 3
  local seen; seen=$(probe)
  eval "$3" >/dev/null 2>&1
  printf '| %s | %s | %s |\n' "$1" "$seen" "$(recovered)"
}

inflight() {  # inflight <name> <inject> [restore]
  local name=$1 dir i code rc ev
  dir=$(mktemp -d)
  echo ">> $(date -u +%T) $name" >&2
  mock config '{"tokens_per_s": 5}'   # ~50-token replies: streams of ~10s
  for i in $(seq 1 "${STREAMS:-6}"); do
    ( curl -sN --max-time 150 -o "$dir/s$i" -w '%{http_code}' -X POST "$BASE/api/chat/stream" \
        -H 'content-type: application/json' -d '{"message":"drill"}' > "$dir/s$i.code"
      echo $? > "$dir/s$i.rc" ) &
  done
  ( end=$((SECONDS + ${WINDOW:-30}))
    while [ $SECONDS -lt $end ]; do
      printf '%s %s\n' "$(date +%s.%N)" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$BASE/api/health")"
      sleep 0.25
    done > "$dir/probe" ) &
  sleep 3
  eval "$2" >"$dir/inject.log" 2>&1 || echo "  inject failed: $(tail -1 "$dir/inject.log")" >&2
  wait
  [ -n "${3:-}" ] && eval "$3" >/dev/null 2>&1
  local rec; rec=$(recovered)
  mock reset '{}' 2>/dev/null || true
  local streams; streams=$(for i in $(seq 1 "${STREAMS:-6}"); do
    code=$(cat "$dir/s$i.code"); rc=$(cat "$dir/s$i.rc"); ev=$(last_event "$dir/s$i")
    if [ "$code" != "200" ]; then echo "HTTP$code"
    elif [ "$ev" = "done" ] || [ "${ev%%:*}" = "error" ]; then echo "$ev"
    else echo "cut(curl$rc)"; fi
  done | sort | uniq -c | awk '{printf "%s%s×%s", (NR>1?", ":""), $1, $2}')
  local outage; outage=$(awk '$2!="200"{if(!f)f=$1; l=$1; n++; c[$2]++} END{
      if(!n){print "none"; exit}
      s=""; for(k in c) s=s (s?", ":"") c[k] "×" (k=="000"?"refused/timeout":k)
      printf "%.1fs (%d of %d probes: %s)", l-f+0.25, n, NR, s}' "$dir/probe")
  printf '| %s | streams in flight: %s · new requests failing for: %s | %s |\n' "$name" "$streams" "$outage" "$rec"
  rm -rf "$dir"
}

pool_in_use() {  # connections the api's database pools hold right now
  docker exec "$API" python -c "import urllib.request as u; print(next(l.split()[1] for l in u.urlopen('http://127.0.0.1:8010/metrics').read().decode().splitlines() if l.startswith('db_pool_connections_in_use ')))"
}

freeze() {  # freeze <name> <service>: steady reads; <service> frozen from +8s to +28s
  local name=$1 target seen
  target=$(C "$2")
  echo ">> $(date -u +%T) $name" >&2
  ( sleep 8; docker pause "$target" >/dev/null; sleep 20; docker unpause "$target" >/dev/null ) &
  seen=$(docker run --rm -i --network "${PROJECT}_default" python:3.13-slim python - 45 < scripts/drills/steady_reads.py)
  wait
  sleep 3  # the pool gauge is sampled each second
  printf '| %s | reads: %s · pool connections held afterwards: %s | %s |\n' "$name" "$seen" "$(pool_in_use)" "$(recovered)"
}

oom() {  # oom <name>: cap the api's memory below what its processes already use
  local name=$1 mem0 swap0 anon limit since restarts0
  mem0=$(docker inspect -f '{{.HostConfig.Memory}}' "$API"); swap0=$(docker inspect -f '{{.HostConfig.MemorySwap}}' "$API")
  restarts0=$(docker inspect -f '{{.RestartCount}}' "$API")
  anon=$(docker exec "$API" awk '$1 == "anon" {print $2}' /sys/fs/cgroup/memory.stat)
  limit=$((anon * 6 / 10))
  since=$(date +%s)
  # memory-swap = memory: no swap, as compose.prod.yaml runs it (memswap_limit).
  inflight "$name" "docker update --memory $limit --memory-swap $limit $API" \
    "docker update --memory $mem0 --memory-swap $swap0 $API"
  # Not the cgroup's oom_kill counter, nor Docker's OOMKilled flag: both are
  # reset when the container restarts, which is what a killed PID 1 causes.
  printf '|  | limit %s MiB (processes held %s MiB) · gunicorn "Perhaps out of memory?": %s · container restarts: %s | |\n' \
    "$((limit / 1048576))" "$((anon / 1048576))" \
    "$(docker logs --since "$since" "$API" 2>&1 | grep -c 'Perhaps out of memory')" \
    "$(( $(docker inspect -f '{{.RestartCount}}' "$API") - restarts0 ))"
}

# SIGKILL one gunicorn worker (a child of PID 1, the master); the docker-exec'd
# process itself has PPid 0, so it never picks itself.
KILL_WORKER="docker exec \$API python -c \"import os,signal; w=[int(p) for p in os.listdir('/proc') if p.isdigit() and open(f'/proc/{p}/status').read().split('PPid:')[1].split()[0]=='1']; os.kill(min(w), signal.SIGKILL); print(min(w))\""

# A crash, as Docker sees one: SIGKILL to the container's PID 1 from the host
# PID namespace (inside the container, PID 1 ignores SIGKILL). Not `docker
# kill`: Docker records that as a manual stop, and the restart policy then
# leaves the container down (measured: exit 137, RestartCount 0).
CRASH="docker run --rm --pid=host $NETSHOOT kill -9 \$(docker inspect -f '{{.State.Pid}}' \$API)"

# A deploy as `docker compose up` does it: stop the old container (SIGTERM,
# graceful drain), then create and start the new one. Keeps the running
# container's rate limits, which came from the shell, not .env.
DEPLOY="export \$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' \$API | grep -E '^(ALERTS|CHAT)_RATE_LIMIT='); $COMPOSE up -d --no-deps --force-recreate api"

# The same new release, rolled out by scripts/deploy.sh instead: the new api
# starts next to the old one, which then drains. Tags the running images as
# a "release", so nothing is pulled.
ROLLOUT="export \$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' \$API | grep -E '^(ALERTS|CHAT)_RATE_LIMIT='); \
  for s in api web; do docker tag \$(docker inspect -f '{{.Config.Image}}' \$(docker ps -q --filter label=com.docker.compose.project=$PROJECT --filter label=com.docker.compose.service=\$s | head -1)) triage-assistant-\$s:drill-\$\$; done; \
  PULL=0 RECORD_TAG=0 scripts/deploy.sh drill-\$\$"

run_drill() {
  case $1 in
    baseline)        drill baseline : : ;;
    redis-stop)      drill redis-stop "docker stop $(C redis)" "docker start $(C redis)" ;;
    redis-hang)      drill redis-hang "docker pause $(C redis)" "docker unpause $(C redis)" ;;
    pgbouncer-stop)  drill pgbouncer-stop "docker stop $(C pgbouncer)" "docker start $(C pgbouncer)" ;;
    pgbouncer-hang)  drill pgbouncer-hang "docker pause $(C pgbouncer)" "docker unpause $(C pgbouncer)" ;;
    db-stop)         drill db-stop "docker stop $(C db)" "docker start $(C db)" ;;
    db-hang)         drill db-hang "docker pause $(C db)" "docker unpause $(C db)" ;;
    db-freeze)       freeze db-freeze db ;;
    pgbouncer-freeze) freeze pgbouncer-freeze pgbouncer ;;
    llm-down)        drill llm-down "docker stop $(C mock-llm)" "docker start $(C mock-llm)" ;;
    llm-429)         drill llm-429 "mock config '{\"fail_mode\":\"http_429\"}'" "mock reset '{}'" ;;
    llm-500)         drill llm-500 "mock config '{\"fail_mode\":\"http_500\"}'" "mock reset '{}'" ;;
    llm-hang)        drill llm-hang "mock config '{\"fail_mode\":\"hang\"}'" "mock reset '{}'" ;;
    llm-drop)        drill llm-drop "mock config '{\"fail_mode\":\"drop_mid_stream\"}'" "mock reset '{}'" ;;
    network-cut)     drill network-cut "docker network disconnect ${PROJECT}_default $API" \
                       "docker network connect --alias api ${PROJECT}_default $API" ;;
    nginx-stop)      drill nginx-stop "docker stop $(C web)" "docker start $(C web)" ;;
    worker-kill)     inflight worker-kill "$KILL_WORKER" ;;
    api-crash)       inflight api-crash "$CRASH" ;;
    deploy)          WINDOW=40 inflight deploy "$DEPLOY" ;;
    rollout)         WINDOW=45 inflight rollout "$ROLLOUT" ;;
    oom)             oom oom ;;
    *) echo "unknown drill: $1" >&2; return 1 ;;
  esac
}

ALL="baseline redis-stop redis-hang pgbouncer-stop pgbouncer-hang db-stop db-hang db-freeze pgbouncer-freeze llm-down llm-429 llm-500 llm-hang llm-drop network-cut nginx-stop worker-kill api-crash deploy rollout oom"
echo "| drill | what a user sees while the fault is active | ready again after restore |"
echo "|---|---|---|"
for d in ${1:-$ALL}; do
  if ! why=$(healthy); then printf '| %s | NOT RUN: stack unhealthy (%s) | |\n' "$d" "$why"; continue; fi
  API=$(api)
  run_drill "$d"
done
