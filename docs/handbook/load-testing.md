# Load testing: concepts, k6, reference scripts

How to put load on the system so that the numbers mean something. The
scripts are in `tests/load/k6/`; what was found with them is in the
performance chapter.

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
percentiles look far better than what users experienced. An open model
avoids it.

**When a closed model is right:** when the question is about concurrency.
"How many simultaneous streams can one worker hold?" is literally "N
users each holding one stream", which is `tests/load/k6/chat.js`
(`constant-vus`).

**Arrival shape matters as much as the rate.** 100 users sending 1
request/s each, in step, is 100 req/s arriving in bursts. On one event
loop, the last request of a burst waits for the other 99. Measured:
the same 100 req/s gave 1.5 ms p50 when spread evenly, and 29 ms when
clumped (`paced-users.js`). Know which shape your real traffic has.

**Rules for any run:**
- **Seed production-sized data** (`make seed n=2000000 ENV=prod`); the
  first bottleneck here was invisible at small sizes.
- **Warm up, then measure a steady state** long enough for the p99 to
  settle (at least 30–60 s at the target rate).
- **Watch the load generator's own CPU.** A saturated generator reports
  its own queueing as server latency.
- **Raise the rate limits for the test**
  (`ALERTS_RATE_LIMIT=1000000 CHAT_RATE_LIMIT=1000000 make prod-up`), or
  you're measuring the limiter's 429s. All load comes from one signed-in
  user, and limits are per user.
- **Load runs signed in.** Every data endpoint needs a session. `make
  load` mints one with `app.cli` (a real session row, no identity
  provider): a user in two of the seeded teams (`team:payments:viewer`,
  `team:platform:viewer`), so reads take the multi-team path. POSTs
  (chat) also send `Origin: <PUBLIC_URL>`, which the api's CSRF check
  requires. By hand: `make session` prints a cookie (`groups="org:admin"`
  for the global list).
- **Test through nginx**, as users arrive, not against the api port.
- **Turn off the tool's telemetry:** k6 `--no-usage-report`.

## k6

A JavaScript test script, run by a Go engine.
- **Why this one:** open and closed models are explicit executors
  (`constant-arrival-rate`, `ramping-arrival-rate`, `constant-vus`).
  Thresholds make a run pass or fail. Custom metrics (the chat script's
  `time_to_first_event`). It pushes results to Prometheus (`make load`
  does when the monitoring stack is up), so the dashboard shows the load
  next to the system's response. Low CPU: 5% to offer 200 req/s here.
- **Limits:** JavaScript, but not Node: no npm packages. Browser-level
  tests are a separate module.
- **The scripts** (`tests/load/k6/`):
  - `alerts-read.js`: open model, reads;
  - `chat.js`: closed model, concurrent streams, time to first event;
  - `health.js`;
  - `compare.js`: `GET /api/alerts?limit=50` at a steady rate;
  - `paced-users.js`: the same load from paced users, arriving in clumps.

Other tools (Locust, JMeter, Gatling, vegeta, oha) follow the same
concepts. Before trusting one, check whether it is an open or a closed
model, its timer resolution (some report whole milliseconds), and its own
CPU at the rate you need.

## Measuring streaming from the client

`tests/load/stream_client_bench.py` measures the client-side CPU cost per
streamed chunk. It compared the openai SDK (126 µs) against a raw HTTP
client with `json.loads` (55 µs).

## Running

```
ALERTS_RATE_LIMIT=1000000 CHAT_RATE_LIMIT=1000000 make prod-up
make seed n=2000000 ENV=prod
make obs-up ENV=prod                          # optional: watch it in Grafana
make load s=alerts-read RATE=500 DURATION=60s
make load s=chat VUS=100 DURATION=60s
```

Load tests run on demand, not in CI. A shared CI runner's performance
varies from run to run, and a latency threshold there is either flaky or
too loose to catch anything. Gate performance on a dedicated,
quiet environment if it must be gated.
