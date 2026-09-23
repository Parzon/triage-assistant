# Load testing: concepts, tools, reference scripts

How to put load on the system so that the numbers mean something, and
which tool to reach for. Six tools have a working script in
`tests/load/`, all hitting the same scenario, and `make load-compare`
runs them side by side. What was found with them is in the performance
chapter.

## Concepts that decide whether your numbers are real

**Open vs closed model.**
- A **closed** model has N virtual users, each sending a request, waiting
  for the answer, maybe pausing, then repeating. When the server slows
  down, the users slow down with it: the load you *offer* drops exactly
  when the system is struggling.
- An **open** model starts requests at a fixed *arrival rate*, however
  slow the responses are. That's how independent users behave: they
  don't wait for each other.

The missing-index test shows the difference. At a fixed 20 iterations/s
(open), k6 had to go from 16 to 157 concurrent users to keep the rate,
and exposed a 7 s p95. A closed test with 16 users would have quietly
offered less load and reported something far milder.

**Coordinated omission.** In a closed model, a request delayed by a stall
also delays the requests that would have been sent during the stall.
They are never sent, so their bad latencies are never recorded, and the
percentiles look far better than what users experienced. Open-model
tools, or tools that correct for it (`oha --latency-correction`, vegeta's
fixed rate), avoid it.

**When a closed model is right:** when the question is about concurrency.
"How many simultaneous streams can one worker hold?" is literally "N
users each holding one stream", which is `tests/load/k6/chat.js`
(`constant-vus`).

**Arrival shape matters as much as the rate.** 100 users sending 1
request/s each, in step, is 100 req/s arriving in bursts. On one event
loop, the last request of a burst waits for the other 99. Measured:
the same 100 req/s gave 1.5 ms p50 when spread evenly, and 29 ms when
clumped (k6 `paced-users.js`). Know which shape your real traffic has.

**Rules for any run:**
- **Seed production-sized data** (`make seed n=2000000 ENV=prod`); the
  first bottleneck here was invisible at small sizes.
- **Warm up, then measure a steady state** long enough for the p99 to
  settle (at least 30–60 s at the target rate).
- **Watch the load generator's own CPU.** A saturated generator reports
  its own queueing as server latency. Artillery used 534% CPU to offer
  200 req/s.
- **Raise the rate limits for the test**
  (`ALERTS_RATE_LIMIT=1000000 CHAT_RATE_LIMIT=1000000 make prod-up`), or
  you're measuring the limiter's 429s. All load comes from one IP.
- **Test through nginx**, as users arrive, not against the api port.
- **Turn off the tools' telemetry:** k6 `--no-usage-report`, Artillery
  `ARTILLERY_DISABLE_TELEMETRY=true`. k6 and Artillery phone home by
  default.

## The tools

All of them ran the same scenario: `GET /api/alerts?limit=50` through
nginx at 200 req/s for 30 s, on the same host (✅ `make load-compare`).

| Tool | Achieved | p50 / p95 / p99 | Tool CPU | Model |
|---|---|---|---|---|
| k6 2.3 | 200.0 req/s | 1.38 / 1.66 / 1.98 ms | 4.8% | open (`constant-arrival-rate`) |
| vegeta | 200.0 | 1.48 / 1.74 / 2.02 ms | 5.4% | open (fixed rate) |
| oha | 200.1 | 1.48 / 1.73 / 1.95 ms | 0.9% | open, with `--latency-correction` |
| JMeter 5.6.3 | 199.7 | 2 / 2 / 2 ms (whole milliseconds only) | 3.6% | closed (threads + throughput timer) |
| Artillery 2.0 | 200.0 | 2 / 3 / 4 ms (whole milliseconds only) | **534%** | open (arrival rate) |
| Locust 2.46 | 205.9 | 130 / 170 / 190 ms | 2.8% | closed (users × `constant_throughput`) |

The Locust row is not Locust being slow. It found a real problem the
other tools' smooth arrivals did not trigger: connection churn in the
database pool. After that fix, Locust measured p50 35 ms and p95 69 ms,
and the rest is its arrivals clumping (performance chapter,
bottleneck 3). The tools with only whole-millisecond resolution (JMeter,
Artillery) cannot resolve a 1.5 ms service.

### k6: the default choice

A JavaScript test script, run by a Go engine.
- **Pros:** open and closed models are explicit executors
  (`constant-arrival-rate`, `ramping-arrival-rate`, `constant-vus`).
  Thresholds make it pass/fail, which suits CI. Custom metrics (the chat
  script's `time_to_first_event`). Pushes results to Prometheus
  (`make load` does this when the monitoring stack is up), so the
  dashboard shows the load next to the system's response. Low CPU.
- **Cons:** JavaScript, but not Node: no npm packages. Browser-level
  tests are a separate module.
- **Here:** `tests/load/k6/`:
  - `alerts-read.js`: open model, reads
  - `chat.js`: closed model, concurrent streams, time to first event
  - `health.js`
  - `compare.js`: the shared scenario
  - `paced-users.js`: the clumped shape

  Run with `make load s=alerts-read RATE=200 DURATION=60s`.

### vegeta: a constant rate from the command line

- **Pros:** one binary; "this rate, this long" is its whole model, so no
  coordinated omission. Results pipe into `vegeta report` or plots.
  Easy in shell scripts.
- **Cons:** no scenarios or logic beyond a list of targets; no streaming
  measurement.
- **Here:** `tests/load/vegeta/alerts.txt` (targets);
  `tools/load/vegeta/Dockerfile` builds the pinned release, checksum
  verified.

### oha: a quick look

- **Pros:** the lowest CPU of the six; a live terminal UI; latency
  correction built in. Ideal for "how does this one endpoint behave at
  200/s?" in ten seconds.
- **Cons:** one URL at a time; no scripting.
- **Here:** the `oha` line in `scripts/load-compare.sh` (image pinned by
  digest).

### Locust: users written in Python

- **Pros:** users are Python classes, so realistic flows with state,
  logins and branching are natural. A web UI. Distributed workers for
  large tests.
- **Cons:** a closed model: user counts plus wait times. Python's cost
  per request limits one worker process. Its scheduling clumps arrivals,
  and that measures *your system's* response to bursts: sometimes that's
  what you want (it found the pool churn), often it isn't.
- **Here:** `tests/load/locust/locustfile.py`:
  - `AlertReader`: each user starts at a random offset unless `SYNC=1`,
    to show the herd.
  - `ChatUser`: measures client-side time to the first token.

  Run with `make load-tool TOOL=locust CLASS=ChatUser USERS=50`.

### JMeter: what many enterprises already have

- **Pros:** very mature; a GUI to build tests; plugins for every
  protocol; existing corporate test plans and dashboards.
- **Cons:** XML test plans (hard to review in a PR). Heavy (JVM).
  Whole-millisecond resolution in the summary. Its classic Thread Group
  is a closed model: use the Open Model Thread Group (5.5+) for arrival
  rates.
- **Here:** `tests/load/jmeter/alerts.jmx` (kept minimal enough to review);
  `tools/load/jmeter/Dockerfile` (Apache 5.6.3, sha512-verified).
  `make load-tool TOOL=jmeter`.

### Artillery: YAML scenarios

- **Pros:** scenarios in YAML, readable by non-programmers; plugins;
  cloud runners.
- **Cons:** the CPU cost measured above (534% to offer 200 req/s: it
  cannot load-test anything fast from one machine). Whole-millisecond
  resolution. The image is amd64-only (emulated, and slower still, on
  Apple Silicon). Telemetry is on by default.
- **Here:** `tests/load/artillery/alerts.yml`. `make load-tool
  TOOL=artillery`.

### Measuring streaming from the client

`tests/load/stream_client_bench.py` measures the client-side CPU cost per
streamed chunk. It compared the openai SDK (126 µs) against a raw HTTP
client with `json.loads` (55 µs).

## Which one

| Need | Use |
|---|---|
| a capacity or latency number you'll put in a PR or an ADR | k6, open model, results in Prometheus |
| a quick check of one endpoint | oha |
| a constant rate from a shell script | vegeta |
| realistic multi-step user journeys in Python | Locust (knowing it's a closed model) |
| the company already runs JMeter or Artillery | reuse them; watch the resolution and the generator's CPU |
| concurrent long-lived streams | k6 `constant-vus` (`chat.js`) |

## Running them

```
ALERTS_RATE_LIMIT=1000000 CHAT_RATE_LIMIT=1000000 make prod-up
make seed n=2000000 ENV=prod
make obs-up ENV=prod                          # optional: watch it in Grafana
make load s=alerts-read RATE=500 DURATION=60s
make load s=chat VUS=100 DURATION=60s
make load-compare RATE=200 DURATION=30        # all six tools, one table
make load-tool TOOL=locust CLASS=AlertReader USERS=100
```

Load tests run on demand, not in CI. A shared CI runner's performance
varies from run to run, and a latency threshold there is either flaky or
too loose to catch anything. Gate performance on a dedicated,
quiet environment if it must be gated.
