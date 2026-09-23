#!/usr/bin/env bash
# Runs ONE scenario - GET /api/alerts?limit=50 through nginx at RATE req/s for
# DURATION seconds - with every load tool in tests/load, one after another,
# and prints a comparable row per tool: achieved rate, latency percentiles,
# errors, and the CPU the load generator itself used (a generator that
# saturates its own CPU reports its own queueing as server latency).
#
#   make load-compare [RATE=200] [DURATION=30]     (needs: make prod-up)
set -euo pipefail

RATE=${RATE:-200}
DURATION=${DURATION:-30}
NET=triage-assistant-prod_default
URL="http://web:8080/api/alerts?limit=50"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$(mktemp -d)
chmod 777 "$OUT"
TOOLS="k6 vegeta oha locust artillery jmeter"
cleanup() {
  for t in $TOOLS; do docker rm -f "lt-$t" >/dev/null 2>&1 || true; done
  rm -rf "$OUT"
}
trap cleanup EXIT

docker build -q -t triage-assistant-vegeta "$ROOT/tools/load/vegeta" >/dev/null
docker build -q -t triage-assistant-jmeter "$ROOT/tools/load/jmeter" >/dev/null
OHA=ghcr.io/hatoo/oha@sha256:3ec3dbf549ea197793482d47a6324797411406bbf438c2fe8b91f244ec641a2f

# run <tool> <docker run args...>: runs detached, samples its CPU mid-run, waits.
run() {
  local tool=$1; shift
  echo ">> $tool" >&2
  docker rm -f "lt-$tool" >/dev/null 2>&1 || true  # a previous interrupted run
  docker run -d --name "lt-$tool" --network "$NET" -v "$OUT:/out" -v "$ROOT/tests/load:/load:ro" "$@" >/dev/null
  sleep $((DURATION / 2))
  docker stats --no-stream --format '{{.CPUPerc}}' "lt-$tool" >"$OUT/$tool.cpu" 2>/dev/null || echo "n/a" >"$OUT/$tool.cpu"
  docker wait "lt-$tool" >/dev/null
  docker logs "lt-$tool" >"$OUT/$tool.stdout" 2>&1
  docker rm "lt-$tool" >/dev/null
  sleep 5  # let the server settle between tools
}

run k6 grafana/k6:2.3.0 run --quiet --no-usage-report -e RATE="$RATE" -e DURATION="$DURATION" \
  --summary-export /out/k6.json /load/k6/compare.js
run vegeta --entrypoint sh triage-assistant-vegeta -c \
  "echo 'GET $URL' | vegeta attack -rate=$RATE/s -duration=${DURATION}s | vegeta report -type=json > /out/vegeta.json"
run oha "$OHA" -z "${DURATION}s" -q "$RATE" --latency-correction --no-tui --output-format json -o /out/oha.json "$URL"
run locust locustio/locust:2.46.6 -f /load/locust/locustfile.py AlertReader --headless \
  -u "$RATE" -r "$RATE" -t "${DURATION}s" --host http://web:8080 --csv /out/locust --only-summary
run artillery -e ARTILLERY_DISABLE_TELEMETRY=true artilleryio/artillery:2.0.34 run \
  --overrides "{\"config\":{\"phases\":[{\"duration\":$DURATION,\"arrivalRate\":$RATE}]}}" \
  --output /out/artillery.json /load/artillery/alerts.yml
run jmeter triage-assistant-jmeter -n -t /load/jmeter/alerts.jmx -Jduration="$DURATION" -l /out/jmeter.jtl -j /out/jmeter.log

python3 - "$OUT" "$RATE" "$DURATION" <<'PY'
import csv, json, sys
out, rate, duration = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]

def cpu(tool):
    return open(f"{out}/{tool}.cpu").read().strip() or "n/a"

rows = []
try:
    k = json.load(open(f"{out}/k6.json"))["metrics"]
    d = k["http_req_duration"]
    rows.append(("k6", k["http_reqs"]["rate"], d["p(50)"], d["p(95)"], d["p(99)"], k["http_req_failed"]["value"] * 100))
except Exception as e: rows.append(("k6", None, None, None, None, f"parse error: {e}"))
try:
    v = json.load(open(f"{out}/vegeta.json")); l = v["latencies"]
    rows.append(("vegeta", v["throughput"], l["50th"] / 1e6, l["95th"] / 1e6, l["99th"] / 1e6, (1 - v["success"]) * 100))
except Exception as e: rows.append(("vegeta", None, None, None, None, f"parse error: {e}"))
try:
    o = json.load(open(f"{out}/oha.json")); s = o["summary"]; p = o["latencyPercentiles"]
    rows.append(("oha", s["requestsPerSec"], p["p50"] * 1000, p["p95"] * 1000, p["p99"] * 1000, (1 - s["successRate"]) * 100))
except Exception as e: rows.append(("oha", None, None, None, None, f"parse error: {e}"))
try:
    agg = next(r for r in csv.DictReader(open(f"{out}/locust_stats.csv")) if r["Name"] == "Aggregated")
    n = int(agg["Request Count"])
    rows.append(("locust", float(agg["Requests/s"]), float(agg["50%"]), float(agg["95%"]), float(agg["99%"]), 100 * int(agg["Failure Count"]) / max(n, 1)))
except Exception as e: rows.append(("locust", None, None, None, None, f"parse error: {e}"))
try:
    a = json.load(open(f"{out}/artillery.json"))["aggregate"]; rt = a["summaries"]["http.response_time"]
    ok = sum(v for c, v in a["counters"].items() if c.startswith("http.codes.2"))
    total = a["counters"].get("http.requests", 0)
    rows.append(("artillery", total / duration, rt["p50"], rt["p95"], rt["p99"], 100 * (1 - ok / max(total, 1))))
except Exception as e: rows.append(("artillery", None, None, None, None, f"parse error: {e}"))
try:
    j = list(csv.DictReader(open(f"{out}/jmeter.jtl"))); el = [int(r["elapsed"]) for r in j]
    rows.append(("jmeter", len(j) / duration, pct(el, 50), pct(el, 95), pct(el, 99), 100 * sum(r["success"] != "true" for r in j) / max(len(j), 1)))
except Exception as e: rows.append(("jmeter", None, None, None, None, f"parse error: {e}"))

print(f"\nSame scenario for every tool: GET /api/alerts?limit=50 via nginx, target {rate} req/s for {duration}s\n")
print(f"{'tool':10} {'req/s':>7} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'errors':>8} {'tool CPU':>9}")
for tool, rps, p50, p95, p99, err in rows:
    if rps is None:
        print(f"{tool:10} {err}")
        continue
    print(f"{tool:10} {rps:7.1f} {p50:8.2f} {p95:8.2f} {p99:8.2f} {err:7.2f}% {cpu(tool):>9}")
PY
