# Operations: watching it, debugging it, breaking it, loading it

How the system tells you what it is doing, how to find out why, what
happens when each part fails, and how much it serves. ✅ = run against
this repo, and the outputs quoted are real; 📘 = recommended practice,
not exercised here.

Under pressure, start from [the alert runbook](../runbooks/alerts.md).
One answer that got worse or slower: [AI
observability](ai-observability.md). The objectives the service is held
to: [production](production.md#service-level-objectives). The decisions:
ADR-0008 (observability), ADR-0009 (performance defaults), ADR-0010
(database timeouts).

Most commands act on the dev stack; add `ENV=prod` for the
production-shaped one (`make prod-up`).

- [Start here](#start-here): symptom → tool
- [Watching](#watching): metrics, dashboards, alerts, logs, traces
- [Debugging tools](#debugging-tools)
- [Failure modes](#failure-modes): the drills, what they found, single
  points of failure
- [Performance and load](#performance-and-load): the method, capacity,
  the bottlenecks in the order they bite
- [Not here yet](#not-here-yet)

## Start here

| Symptom | First | Then |
|---|---|---|
| One user got an error | `make trace id=<request id>` (from the error message, the `X-Request-ID` header, or the UI) | the traceback in the api's log line |
| Everything is slow at once | Grafana: **event loop lag** panel | a slow request's trace (`make trace id=...`); asyncio debug mode locally |
| One route is slow | `make db-top-queries` | `EXPLAIN (ANALYZE, BUFFERS)` in `make psql` |
| Requests hang, then 503 | `make db-activity`, `make db-locks` | `curl /api/ready` says which dependency |
| nginx returns 502/504 | nginx log: `upstream_time` vs `request_time`, [its error lines](#nginx) | `make ps ENV=prod`, `make gunicorn c="show workers"` |
| `WORKER TIMEOUT` in the api log | something blocks the event loop | [asyncio debug mode](#a-blocked-event-loop-asyncio-debug-mode) locally |
| Container restarted / exit 137 | `docker inspect` (ExitCode, OOMKilled, RestartCount) | Grafana **OOM kills** panel; `memory.events` |
| A service can't reach another | `make ps` (running and healthy?), `/api/ready` | `docker compose exec api getent hosts db` (does the name resolve?) |
| Rate limiting behaves oddly | `make redis-slowlog`, `make redis-cli` | the `ratelimit_decisions_total` panel |
| The chat answers 503 | the error's code: `assistant_disabled` (someone turned it off: `make assistant`), `rate_limiter_unavailable` (Valkey, in production), `database_unavailable` | `/api/ready` |
| Nobody can sign in, or one person can't | the api log's `"sign-in failed"` lines (code and reason) | [signing in](#signing-in): the provider's log, the flow with curl |
| Someone sees too much or too little | `GET /api/me` as them (their teams and roles) | the provider's groups for them; `make psql`: `memberships` |
| A logic bug you can reproduce | breakpoints: `make debug-up` + VS Code | `pdb` |
| A crash with no traceback | faulthandler (on in every image) | the exit code and `OOMKilled` in `docker inspect` |
| Something odd in the browser | DevTools Network tab | Playwright trace (`make e2e`, then the trace viewer) |

The first five minutes of any incident, in order: `make ps ENV=prod`
(what is unhealthy or restarting), `curl -s localhost:$HTTP_PORT/api/ready`
(which dependency), the Grafana dashboard (errors, latency, lag, pools),
then the logs of one failing request.

## Watching

```
api (every worker) ──/metrics──┐
postgres-exporter ─────────────┤
pgbouncer-exporter ────────────┤   Prometheus ──rules──► Alertmanager ──webhook──► api: POST /alerts/alertmanager
redis-exporter ────────────────┼──► (scrape      │                                  (alerts appear in the app)
node-exporter (host) ──────────┤    every 15 s)  └──► Grafana (dashboard as code)
cAdvisor (containers) ─────────┤
mock-llm (the provider's view)─┘
api ──OTLP──► Jaeger (traces)
```

`make obs-up` starts it next to the dev stack (`ENV=prod` next to the
production one) and turns tracing on. Grafana, Prometheus, Alertmanager
and Jaeger listen on 127.0.0.1 only; on a server, reach them through an
SSH tunnel.

### What is measured

| Signal | Metric | Why |
|---|---|---|
| **R**ate, **E**rrors | `http_requests_total{method, route, status}` | route = the template (`/alerts/{alert_id}`), never the raw path |
| **D**uration | `http_request_duration_seconds` (histogram, 5 ms – 10 s) | whole response; for a stream, the whole stream |
| saturation | `event_loop_lag_seconds` | how late the event loop runs a timer: the first sign of a busy or blocked worker |
| saturation | `db_pool_connections_in_use` / `_max` | the app's database pools; stuck above 0 at idle = leaked connections (ADR-0010) |
| in flight | `http_requests_in_progress`, `llm_active_streams` | |
| the model | `llm_requests_total{outcome}`, `llm_time_to_first_token_seconds`, `llm_stream_duration_seconds`, `llm_tokens_total{kind}` | outcome is `ok`, `truncated` (delivered, but cut off by `LLM_MAX_OUTPUT_TOKENS`), `cancelled` (the user left) or an `llm_*` error code (`llm_empty_answer`: finished without a word); tokens × price = cost |
| refusals | `chat_refusals_total{reason}` | questions refused before any model call: `assistant_disabled` (the off switch, ADR-0024) |
| rate limiter | `ratelimit_decisions_total{scope, decision}` | `fail_open` = Valkey did not answer in time, request allowed; `fail_closed` = refused instead (the chat, in production) |
| runbook search | `retrieval_duration_seconds{mode}`, `embedding_requests_total{kind, outcome}` | mode `hybrid`, or `keyword_only`: the question not embedded, or no current vectors (alert `RetrievalDegraded`) |
| answers | `chat_citations_total{validity}`, `prompt_redactions_total` | an `invalid` citation is a number the model invented; each redaction is a secret that reached an alert or a runbook |
| what runs | `app_info{version, prompt, model, embedding_model}` | 1 per combination: when answers change, line their change up with this one first |
| sign-in | `auth_logins_total{outcome}` | callbacks from the identity provider: `ok`, `access_denied`, `invalid_state`, `expired`, `login_failed`, `invalid_token`, `idp_unavailable` |
| access | `auth_rejections_total{reason}` | requests refused before any route: `no_session`, `expired`, `cross_origin` (a CSRF attempt, or a script without `Origin`) |
| the identity provider | `identity_provider_up` | 1 if it answered the api's last check (every 30 s per worker); the lowest across workers (`livemin`) |
| dependencies | `pg_up`, `pgbouncer_up`, `redis_up` + their exporters' metrics | |
| host / containers | node-exporter, cAdvisor (`container_*`, including `container_oom_events_total`) | CPU, memory, disk, OOM kills |

The four golden signals (latency, traffic, errors, saturation) are all
there. Saturation is the one teams forget. Here it is the event-loop lag
and the pool gauges, because an async worker saturates long before its
CPU graph looks full: the load tests saw timeouts at 46% CPU ([a
saturation cascade](#a-saturation-cascade)).

**Multiprocess mode.** In production, gunicorn runs several worker
*processes*. With a normal Prometheus client, each scrape would be
answered by whichever worker got it. Measured with 4 workers and 40
requests, six scrapes returned `15, 15, 5, 6, 14, 5`: each is one
worker's share. In multiprocess mode (`PROMETHEUS_MULTIPROC_DIR`), each
worker writes its samples to files, and `/metrics` sums them: `40, 40,
40, 40, 40, 40`. The costs:
- no per-process CPU or memory metrics (cAdvisor provides those per
  container);
- every gauge needs a `multiprocess_mode` (`livesum`, `max`...);
- the client calls `getpid()` on every metric update (~8 per request,
  measured with strace);
- the directory must exist before the first import, and must never be set
  to an empty string.

**Labels.** Each distinct combination of label values is a separate time
series, held in Prometheus' memory. Values must come from a small, fixed
set: route **templates**, never raw paths; outcomes and error codes,
never messages; never user ids, IPs, free text, or a tool name the model
chose (`tools.label()` maps it first). A test asserts that a request to
`/alerts/424242` is recorded as `/alerts/{alert_id}`. cAdvisor by default
turns every container label into a Prometheus label:
`--store_container_labels=false` plus a whitelist of the two compose
labels keeps it bounded.

### Dashboards are code

`infra/observability/grafana/build_dashboard.py` generates
`dashboards/service.json`, which Grafana provisions at startup. Change the
Python, run `make dashboard`, and review the JSON diff in the PR. `make
obs-check` fails if the committed JSON is not what the generator
produces. An edit made in the Grafana UI survives only until the next
reload.

The rows, top to bottom: traffic and errors (RED), the model, sign-in and
access (the identity provider's state, sign-ins by outcome, refused
requests by reason), dependencies (limiter, pools, PgBouncer, Postgres),
containers and host.

![The service dashboard under load](../images/grafana-service-dashboard.png)

What the first version of the dashboard got wrong: 4 of its 30 queries
showed "No data" on a healthy system.
- **A ratio whose numerator does not exist yet.** Until the first 5xx
  happens, `http_requests_total{status=~"5.."}` has no series, and a
  division by a missing series is "no data", not 0. Write `(… or
  vector(0))`.
- **`rate()` needs two samples.** Just after a restart it returns nothing,
  so a panel goes blank for one scrape interval.
- **A new series' first increment is invisible to `rate()` and
  `increase()`.** They need a previous sample, so an alert on "any 5xx"
  can miss the very first one.
- **One outlier route flattens a latency panel.** `/chat/stream` lasts as
  long as the answer (seconds), so it's excluded from the p95-by-route
  panel and has its own LLM row.

### Alerts

The rules are in `infra/observability/prometheus/alerts.yml`, each with a
section in [the alert runbook](../runbooks/alerts.md). Each alert says
what users experience ("more than 5% of api requests fail"), not only
which component is unhappy. `for:` is how long the condition must hold,
so a blip pages nobody. Severity maps onto the app's alert severities.

| Alert | Fires when | For |
|---|---|---|
| `ApiDown` / `ApiMissing` | the api can't be scraped / there is no api at all | 1 min |
| `PostgresDown`, `PgBouncerDown` | the exporter can't reach it | 1 min |
| `RedisDown` | Valkey is down: rate limiting is off, the chat's refuses in production | 1 min |
| `ExporterDown` | a monitoring exporter is down (blind spot) | 5 min |
| `HighErrorRate` | > 5% of requests are 5xx, not counting the off switch's refusals | 5 min |
| `SlowRequests` | p95 of non-streaming routes > 1 s | 10 min |
| `EventLoopLagHigh` | loop lag p95 > 100 ms | 5 min |
| `RateLimiterFailingOpen` | requests pass unlimited | 2 min |
| `RateLimiterFailingClosed` | questions are refused because the limiter's store does not answer | 1 min |
| `DatabasePoolSaturated` | clients queue in PgBouncer | 2 min |
| `AppDatabasePoolExhausted` | the api's pools are > 90% in use | 2 min |
| `LLMErrors` | > 10% of model calls fail | 5 min |
| `LLMSlowFirstToken` | p95 time to first token > 10 s | 10 min |
| `LLMAnswersTruncated` | > 10% of answers cut off by `LLM_MAX_OUTPUT_TOKENS` (a reasoning model thinking inside the limit) | 15 min |
| `RetrievalDegraded` | most runbook searches fall back to keywords: the embedding model fails, or needs `make reembed` | 15 min |
| `IdentityProviderDown` | the api's check of the provider fails: new sign-ins fail, signed-in users don't notice | 2 min |
| `SignInsFailing` | ≥ 5 sign-ins failed in 15 min and none succeeded (a rotated secret, a changed redirect URI, clock skew) | 5 min |
| `DiskWillFillIn6h`, `DiskAlmostFull` | the trend says full in 6 h / < 10% free | 15 / 5 min |
| `ContainerNearMemoryLimit`, `ContainerOOMKilled` | > 90% of the limit / the kernel killed a process | 5 min / at once |

**Alert rules are code with tests.** `alerts.test.yml` feeds synthetic
series to `promtool test rules` and asserts when each alert must and must
not fire. `make obs-check` runs them, as does CI. A test that expects the
wrong thing fails with the exact difference, so they are real tests.

**`absent()` for things that vanish.** When a service is removed rather
than stopped, its `up` series disappears, and `up == 0` can never be
true. `ApiMissing` uses `absent(up{job="api"})` (✅ covered by a promtool
test).

**How fast it notices** (✅ measured end to end, Valkey stopped):
`RedisDown` fired after 90 s (a 15 s scrape, plus 1 min `for:`, plus
evaluation), and reached the app 30 s later (Alertmanager's
`group_wait`). That's about 2 minutes from failure to notification. A
fault shorter than a scrape interval can leave no trace at all: the two
4-second Valkey drills never show in `redis_up`.

**Alertmanager** sends alerts to the app itself (`POST
/alerts/alertmanager`, bearer token, compared in constant time), so a
demo shows its own alerts. Its config file can't read environment
variables, so the token arrives as a compose secret file. For a real
team, add a receiver (email, Slack, a pager) in `alertmanager.yml`.

### Logs

One JSON object per line on stdout from every service. The api's fields:
`ts`, `level`, `logger`, `msg`, `request_id`, plus fields, and, when
tracing is on and the trace is kept, `trace_id` and `span_id`. `route` is
the template, and exceptions are in `exc` as one string. Docker keeps the
logs, rotated at 3 × 10 MB per container (`x-logging` in `compose.yaml`).
Log levels:
- `info`: one line per request (`app.access`) and lifecycle events;
- `warning`: a degradation that is handled (the limiter failing open);
- `error`: something failed for a user, with a traceback.

What not to log: request bodies, alert text and chat questions (personal
data), secrets, tokens, cookies, authorization codes. The access log
carries the user's id: enough to say who did what, without their name or
email in every log store. A failed sign-in logs its reason (`"sign-in
failed"`, with `code` and `reason`), never the token. The chatty `httpx2`
logger (one line per model call) is set to WARNING.

```
make logs S=api                                   # follow one service
docker logs --since 10m $(docker compose -p triage-assistant-prod ps -q api) 2>&1 | jq -c 'select(.level=="error")'
docker logs --since 10m $(docker compose -p triage-assistant-prod ps -q api) 2>&1 | jq -r 'select(.exc) | .exc'   # tracebacks
docker logs --since 5m $(docker compose -p triage-assistant-prod ps -q web) | jq -c 'select(.status >= 500)'
```

Gotchas:
- `docker logs --since 2026-09-23T04:21:00` without a zone reads the time
  as **local** time. Use relative times (`--since 5m`) or add `Z`.
- `docker run --rm` deletes the container, and its logs with it, the
  moment it exits. For a one-off container whose output you need, drop
  `--rm` or redirect its stdout.

### Traces

✅ One trace per request: retrieval, the embedding call, each SQL
statement, the model call, with the GenAI conventions' attributes
(tokens, time to first chunk, prompt version, which sections). The
metrics say something moved; a trace says what one answer was given, and
where its time went. Production keeps a tenth of them (ADR-0023).
Everything else, the privacy rules included, is in [AI
observability](ai-observability.md).

## Debugging tools

### Request ids

✅ nginx generates a 32-hex `$request_id` for every request and sends it
upstream as `X-Request-ID` (`snippets/proxy.conf`). The api's middleware
adopts it: a malformed or oversized id is replaced, never trusted. It
puts the id in a `contextvar`, so every log line written while handling
that request carries it without being passed around. It returns the id
in the `X-Request-ID` response header and in every error body. When nginx
itself answers for a failed api, its JSON error carries the same id
(`@api_error` in `default.conf`).

```
$ make trace id=4f1c...  ENV=prod
{"logger":"app.triage","msg":"chat answered","duration_ms":1270,"ttft_ms":321.5,...}
{"logger":"app.access","status":200,"duration_ms":1280.8,"route":"/chat/stream",...}
{"request_id":"4f1c...","status":200,"request_time":1.281,"upstream_time":"1.281",...}   <- nginx
```

The same request, seen by the model call, the api and nginx. If nginx's
`request_time` is much larger than `upstream_time`, the time went into
the client connection (slow client, large body), not the api. This is
also the support workflow: a user pastes the id from the error message,
and you find the traceback in seconds.

### Breakpoints

**VS Code, attached to the containerised api (debugpy) ✅.** `debugpy` is
the debug server VS Code's Python debugger talks to. It runs inside the
Python process and speaks the Debug Adapter Protocol (DAP) over TCP.
`compose.debug.yaml` starts the dev api under `python -m debugpy --listen
0.0.0.0:5678`, published on 127.0.0.1:5678 only. VS Code connects to it,
sends your breakpoints as file paths, and debugpy installs them in the
running interpreter. The paths differ: `apps/api/app/...` on your machine
is `/api/app/...` in the container. The `pathMappings` in
`.vscode/launch.json` translate them. Without that mapping, breakpoints
show as "unverified" and never hit.

```
make debug-up          # api under debugpy (and asyncio debug mode)
# VS Code: Run and Debug -> "Attach to api (make debug-up)"; set a breakpoint; send a request
make debug-down        # back to the hot-reload server
```

Verified with a DAP client sending what VS Code sends: attach, then
`setBreakpoints` on `app/routes/health.py:30` (verified: true), then `GET
/health`. The result was a `stopped` event, reason `breakpoint`, top frame
`health app/routes/health.py:30`. The request waited while paused, and
`continue` returned `{"status":"ok"}`.

Gotchas:
- **A paused breakpoint in async code stops the whole event loop**: every
  other request on that process waits too. That's fine locally; it's why
  you never debug a shared environment this way.
- No `--reload` under debugpy: the reloader runs your code in a child
  process that the debugger is not attached to. Restart (`make debug-up`)
  after code changes.
- **Never in production**: an open debugpy port is remote code execution
  for anyone who can reach it. The overlay publishes on 127.0.0.1 only and
  is not part of `compose.prod.yaml`.
- `-f compose.debug.yaml` turns off the automatic `compose.override.yaml`
  merge, so `make debug-up` lists all three files explicitly.

**pdb in a terminal ✅.** `breakpoint()` in the code, or a breakpoint set
from the command line without editing anything:

```
docker compose stop api
docker compose run --rm --service-ports api \
  python -m pdb -c "b app/routes/health.py:30" -c c -m uvicorn app.asgi:app --host 0.0.0.0 --port 8010
# another terminal: curl localhost:8010/health
> /api/app/routes/health.py(30)health()
-> return {"status": "ok"}
(Pdb) p 6*7
42
(Pdb) c
docker compose up -d api          # afterwards
```

Why not with the normal dev server: `uvicorn --reload` runs the app in a
multiprocessing child whose stdin is `/dev/null`, so pdb has no keyboard
to read from. `docker compose run` gives the server your terminal. (`where
1` is not valid in Python 3.13's pdb; use `w`.)

In production images `PYTHONBREAKPOINT=0` turns a forgotten `breakpoint()`
into a no-op ✅ (measured 0.23 s for the request). Otherwise a worker
would wait for keyboard input forever, until gunicorn killed it.

### Crashes without a Python traceback

✅ Every image sets `PYTHONFAULTHANDLER=1`. When the interpreter itself
crashes (a segfault in a C extension, an abort), Python prints the
traceback of every thread before dying. Without it, a segfault is a
silent exit code 139. Measured with `ctypes.string_at(0)`: "Fatal Python
error: Segmentation fault" followed by the Python stack down to the
handler line.

### A blocked event loop: asyncio debug mode

✅ `PYTHONASYNCIODEBUG=1` (on under `make debug-up`) makes asyncio log any
callback that holds the event loop for more than 100 ms, and coroutines
that were created but never awaited, with where they were created. It
named the blocking call in [the experiment below](#what-a-blocked-event-loop-looks-like)
60 times. It is too slow for production. There, the
`event_loop_lag_seconds` metric and the `EventLoopLagHigh` alert do the
same job.

### Which profiler for which question

| Question | Tool | Why this one |
|---|---|---|
| Where does a *running* server spend its time? | 📘 py-spy | a sampling profiler that reads the process from outside: no code change, no restart, safe in production |
| How many times is each function called, exactly? | `cProfile` (standard library) ✅ | deterministic: counts every call; it slows the code it measures (+40% on an import), so the absolute times are inflated |
| Why is startup slow? | `python -X importtime` ✅ | per-module import time ([measured](#startup-and-memory)); cProfile shows only `importlib` frames for this |
| Is memory growing per request? | `tracemalloc` (standard library) ✅ | compares live allocations between two snapshots, by source line. Call `gc.collect()` before each, and run two sizes: a leak grows with the number of requests, a cache settles |
| Which native allocations (C extensions) grow? | 📘 memray | must be installed in the *target* interpreter (`memray run`, or `memray attach`, which injects into it), so dev/test images only, never the production image |

### gunicorn's control socket

✅ gunicorn 25.1+ has a control socket. Here it lives at
`/tmp/gunicorn.ctl`, because the default under `$HOME` is not writable
with a read-only root filesystem.

```
make gunicorn c="show workers"     # PID  AGE  BOOTED  LAST_BEAT (heartbeat age per worker)
make gunicorn c="show stats"
make gunicorn c="worker add 1"     # temporarily; the configured count returns on restart
```

A worker whose `LAST_BEAT` keeps growing is blocked, and is about to be
killed with `WORKER TIMEOUT`.

### Postgres

✅
```
make db-activity   [ENV=prod]   # every connection: state, how long, which query
make db-locks      [ENV=prod]   # who is blocked, by whom (pg_blocking_pids)
make db-top-queries [ENV=prod]  # pg_stat_statements: calls, mean, total, % of all DB time
make psql                        # then EXPLAIN (ANALYZE, BUFFERS) <query>;
```

The lock-queue experiment (`db-locks` shows the chain):
1. A long transaction held a lock.
2. A migration's `ALTER TABLE` (with no `lock_timeout`) queued behind it.
3. Every new `SELECT` on the table queued behind the `ALTER`: Postgres
   grants locks in arrival order.
4. Users got 503 after exactly 10.0 s, the app role's `statement_timeout`.

This is why migrations here set `lock_timeout = 5s` (`SET LOCAL` inside
Alembic's transaction), so a blocked `ALTER` gives up instead of stalling
the table.

- **Reading `db-top-queries`:** `total_ms` (calls × mean) is what costs
  capacity. On an idle database, postgres-exporter's own statistics query
  was 24.9% of all DB time (67 calls, 244 ms each): monitoring has a
  cost. Reset with `SELECT pg_stat_statements_reset();` before a
  measurement.
- **Run `EXPLAIN` under the same conditions as the problem.** The
  missing-index query took 43–152 ms idle, which looks harmless; under 40
  req/s it was p95 7 s with 45% failures ([a missing
  index](#a-missing-index)).
- **`db-activity`:** pooled connections show `idle` with `ROLLBACK` as
  their last query. That is SQLAlchemy resetting each connection when it
  returns to the pool, not an error.

### Valkey (the rate limiter's store)

✅
```
make redis-slowlog [ENV=prod]   # SLOWLOG GET 10 + INFO commandstats
make redis-cli                  # then e.g. KEYS 'LIMITS*' (fine on a tiny dev store only)
```

Measured: an empty slowlog, `incrby` 36,509 calls at 0.52 µs each. The
store is never the bottleneck; the network round trip and the event loop
are. 📘 `MONITOR` streams every command to your terminal and slows the
server: never on a production instance.

### Row-level security

✅ "The table is empty" as the app role, but not as the owner: that is
row-level security with no caller named (ADR-0014). `make psql` connects
as the owner, which the policies do not apply to. To see what a caller
sees, as the app role in a direct session:
```
SELECT set_config('app.read_team_ids', '{2,3}', false);   -- their team ids
SELECT count(*) FROM alerts;
```
A 500 whose traceback says "new row violates row-level security policy"
means the app skipped its own role check: Postgres refused the write.

### Signing in

✅ A sign-in crosses the browser, the api and the identity provider, so
look at all three:
- **The api's log.**
  - `"sign-in failed"` carries a `code` (`login_failed`, `invalid_token`,
    `idp_unavailable`) and a `reason`: "code exchange refused" (with the
    provider's `error`, such as `invalid_client` for a wrong secret), "ID
    token rejected: ..." (the failed check: expired, audience, issuer),
    "nonce does not match".
  - `"identity provider unavailable"` carries the URL it could not reach.
  - `"metadata names another issuer"`: `OIDC_ISSUER` is wrong.
- **The browser.** Where did the flow stop? After `/api/auth/login` the
  URL is the provider's. A failure that never comes back to
  `/api/auth/callback` happened at the provider (a redirect URI it does
  not allow, a user not assigned to the app), and its page says why. One
  that does come back lands on `/?auth_error=<code>`.
- **The provider's log.** For the bundled one: `make logs S=keycloak`.
  Keycloak logs each refusal with its reason (`invalid_redirect_uri`,
  `invalid_client_credentials`, `user_not_found`).
- **The whole flow from a terminal**, without a browser:
  `scripts/lib/session.sh`'s `sign_in`:
  ```
  . scripts/lib/session.sh
  sign_in http://localhost:5173 alice "$(sed -n 's/^DEMO_USER_PASSWORD=//p' .env)" /tmp/jar -v
  ```
  `-v` shows every redirect and cookie.
- **Is the provider up, as the api sees it?** `curl -s
  localhost:8088/api/ready`, then `identity_provider` in the checks. The
  api checks it every 30 s.
- **What does the api think of a user?** `GET /api/me` with their session
  shows their teams and roles as the last sign-in recorded them. Roles
  change only at the next sign-in: `make revoke email=...` forces one.
- **Why a 403 on a POST?** `csrf_failed`: the request's `Origin` is not
  exactly `PUBLIC_URL`. The usual cause is opening the site under another
  name (`127.0.0.1` vs `localhost`, a different port).

### nginx

✅ Access log fields (JSON): `status`, `request_time` (whole request,
client included), `upstream_time` (time waiting for the api),
`request_id`. Error log lines seen in this repo and what they meant:

| Line | Meaning here |
|---|---|
| `recv() failed (104: Connection reset by peer) while reading response header from upstream` | the api closed a keep-alive connection nginx was reusing (gunicorn `max_requests` recycling under load); POSTs are not retried, so the user got a 502 |
| `upstream prematurely closed connection` | the api process died mid-response (a worker killed or crashed) |
| `connect() failed (111: Connection refused) ... upstream: "http://172.21.0.7:8010"` | nginx connecting to an address the api no longer has: without `resolve`, nginx resolves `api` once at startup |
| `upstream timed out (110: Connection timed out) while connecting` | the api's IP answers nothing (container gone or network cut): a 504 after `proxy_connect_timeout` (2 s) |

### Containers

✅
```
API=$(docker compose -p triage-assistant-prod ps -q api)    # api-2, api-3... after a rolling deploy: never hardcode it
docker inspect -f '{{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} restarts={{.RestartCount}}' $API
docker inspect -f '{{json .State.Health.Log}}' $API | jq   # last healthcheck outputs
docker exec $API cat /sys/fs/cgroup/memory.events           # oom_kill counter
docker stats --no-stream
```

Gotchas, each measured in the failure drills:
- **Exit code 137 = SIGKILL.** It is usually the kernel's OOM killer, but
  `docker kill` also produces it, and [is not a
  crash](#docker-kill-is-not-a-crash). An OOM kill often leaves no trace
  on the container: [what an OOM looks like](#memory-limits-were-soft).
- **`docker events --since` cannot look back.** The daemon keeps only its
  last 256 events in memory, and every healthcheck adds three
  (`exec_create`, `exec_start`, `exec_die`). On this stack, all 256
  retained events were healthchecks from the last 44 seconds; the api's
  restarts a few minutes earlier were gone. To have that history in an
  incident, stream it continuously into a log you keep (📘 e.g. a systemd
  unit running `docker events --format '{{json .}}'` into journald), and
  filter out `exec_*`.
- **Docker does not restart unhealthy containers.** Restart policies act
  on process exit only. "Unhealthy" matters to `depends_on`, and to
  orchestrators (ECS, Kubernetes) that replace tasks on it.

### Browser and end-to-end

✅
- DevTools, Network tab: the chat is a `fetch` stream, not an
  `EventSource`, so there is no "EventStream" tab. Read the response
  timing, and the request id from the response headers.
- Playwright records a trace for failed tests (`make e2e`). The trace
  viewer (`npx playwright show-trace <zip>`) shows every request and DOM
  snapshot. It exposed a bug jsdom could not reproduce: clicking Stop
  re-submitted the question, and the trace showed two `POST
  /api/chat/stream`.

## Failure modes

What happens when each part of the system fails, measured rather than
guessed. `make drills` injects one fault at a time into the
production-shaped stack, records what a user sees, restores the stack,
and checks it comes back. Run it after any change to the paths it
covers, and after upgrading anything on the database path (ADR-0010).

### How the drills work

- **Stack:** `make prod-up`, the production images on one host: the TLS
  edge → nginx → gunicorn (2 uvicorn workers, 2 CPUs, 1 GiB) → PgBouncer
  → Postgres, plus Valkey, Keycloak and the mock LLM.
- **Rate limits** raised (`ALERTS_RATE_LIMIT=1000000
  CHAT_RATE_LIMIT=1000000`) so the probes are never throttled.
- **Versions:** Docker 28.4.0, nginx 1.30.5, gunicorn 26.2.0, uvicorn
  0.53.0, FastAPI 0.141.1, SQLAlchemy 2.0.54, asyncpg 0.31.0, PgBouncer
  1.25.2, Postgres 17.11, Valkey 8.1.10, openai 3.17.0. Measured
  2026-09-23.

Three kinds of drill (`scripts/failure-drills.sh`):

| Kind | What it does |
|---|---|
| dependency fault | while the fault is active, one request on each user path: `/api/health` (liveness), `/api/ready` (readiness), `GET /api/alerts` (a database read), `POST /api/chat/stream` (a streamed answer), and sign-in (`/api/auth/login`, then the provider's login page) |
| in-flight fault | 6 slow streams (~10 s each) are running when the fault hits, and `/api/health` is probed 4× a second throughout |
| freeze under load | 20 client threads read continuously while a dependency is frozen for 20 s; afterwards, the api's database pools must hold 0 connections |

Probes go where users go: HTTPS through the TLS edge (with the edge's
local CA), or nginx on loopback when the edge is not running. They act as
a signed-in user: a session minted by `app.cli`, stored in Postgres like
anyone's.

Faults are injected with plain Docker:
- `docker stop` for "down";
- `docker pause` for "frozen": SIGSTOP; the process is alive, its TCP
  connections stay open, and nothing answers. This is the nastiest
  failure there is;
- `docker network disconnect`;
- SIGKILL from the host PID namespace for a crash;
- `docker update --memory` for OOM;
- the mock LLM's admin API for provider failures.

Before each drill, the harness waits until every container is healthy.
An injection that fails is reported as such, never as a result: an early
version recorded "LLM 429 → answer OK" because the mock was still booting
when the fault was sent.

**Adding a drill:** a `case` line in `scripts/failure-drills.sh` with an
inject command, a restore command, and which drill kind measures it.
`make drills` runs them all (~15 minutes); `make drills d="db-freeze
deploy"` a selection. The output is one Markdown table row per drill:
compare it with the matrix below. A row that differs is either a
regression or a finding: write it down, fix it, keep the drill.

### The matrix

✅ After the fixes described below. "Alert if it lasts" names the rule
that covers the fault. The drills end long before any rule's `for:`
duration, so these fired only in their own tests: the promtool rule
tests, [the Valkey loop measured end to end](#alerts), and
`ContainerOOMKilled`, observed firing in the OOM drill. The drill log
prints each drill's start time (UTC), to match a run against Grafana.

| Fault | What users see while it lasts | Alert if it lasts | Back after restore |
|---|---|---|---|
| Valkey stopped | reads, writes and sign-in work, **without rate limiting** (fail-open, ADR-0004); `/ready` says `redis: degraded`. The chat answers 503 `rate_limiter_unavailable` in 14 ms: in production it fails closed (ADR-0023) | `RedisDown` (1 min), `RateLimiterFailingOpen`, `RateLimiterFailingClosed` | 0 s |
| Valkey frozen | same; each limiter call gives up after its 200 ms budget (the chat's 503 came after 217 ms) | same | 0 s |
| PgBouncer stopped | JSON 503 `database_unavailable` in 2 ms; chat refused before streaming | `PgBouncerDown`, `HighErrorRate` | 1 s |
| PgBouncer frozen | 503 after 5.0 s (connect timeout) | `PgBouncerDown` (exporter can't query), `HighErrorRate` | 0 s |
| Postgres stopped | 503 in 15 ms (PgBouncer refuses at once while logins fail), sessions included: nobody is signed in without the database. Sign-in answers 503 JSON too (it stores its state there first) | `PostgresDown`, `HighErrorRate` | 1–2 s |
| Postgres frozen | 503 after 5.3 s, or 10.6 s when a pooled connection's pre-ping fails first (two PgBouncer waits in a row) | `PostgresDown`, `HighErrorRate`, `SlowRequests` | 1 s |
| Postgres frozen 20 s under load | 67,121 reads OK; 28 × 503 (slowest 15.2 s: PgBouncer's `query_timeout`); in-flight ones slowest 20.1 s; **pool connections held afterwards: 0** | as above | 0 s |
| PgBouncer frozen 20 s under load | 67,751 reads OK (in-flight ones waited, slowest 20.2 s); 4 × 503 (20.0 s); **pool held afterwards: 0** | as above | 0 s |
| LLM down | chat: `error llm_unavailable` event in 0.4 s; everything else works | `LLMErrors` (>10% for 5 min) | 0 s |
| LLM 429 | `llm_rate_limited` after 2.0 s (one SDK retry with backoff) | `LLMErrors` | 0 s |
| LLM 500 | `llm_unavailable` in 0.4 s | `LLMErrors` | 0 s |
| LLM hangs | stream stays open (heartbeat every 15 s), then `llm_timeout` at 60 s | `LLMSlowFirstToken`, `LLMErrors` | 0 s |
| LLM drops mid-answer | partial answer, then `llm_unavailable` (0.8 s) | `LLMErrors` | 0 s |
| api cut from the network | JSON 504 `upstream_unavailable` after 2 s. A request that nginx sends on an **existing keep-alive connection** waits the full 30 s read timeout instead | `ApiDown` (Prometheus can't scrape it) | 0 s |
| nginx stopped | the TLS edge holds each request for 5 s (it may be a restart), then answers `/api` with the JSON `upstream_unavailable` (502) | **nothing in the stack** ([not here yet](#not-here-yet)) | 1 s |
| the TLS edge stopped | connection refused: the site is down (the edge owns ports 80/443) | **nothing in the stack** | 1 s |
| the identity provider stopped (Keycloak) | **signed-in users notice nothing**: reads 200 in 5 ms, chat streams to the end. New sign-ins: `/api/auth/login` still redirects to the provider (its metadata is cached), and the provider's page fails (502 from the edge). Within 30 s `/ready` reports `identity_provider: degraded`, still 200 | `IdentityProviderDown` (2 min) | 0 s |
| one gunicorn worker killed | streams on that worker cut (3 of 6); other requests unaffected; a new worker starts | nothing (by design: normal) | 0 s |
| api crashes (PID 1 SIGKILL) | every in-flight stream cut; 502 for ~0.5 s; the restart policy brings it back | `ApiDown` only if it stays down 1 min | 0 s |
| deploy (`up --force-recreate api`) | in-flight streams **finish** (graceful drain); **new requests get 502 for 6.7 s** | — | 0 s |
| rolling deploy (`make deploy`), through the TLS edge | in-flight streams finish; **no failed request in three runs**: the edge holds requests while nginx is replaced (worst case one request waited 2.0 s). v0.2.0 from GHCR: 134,917 signed-in requests, 0 failed | — | 0 s |
| a deploy that replaces the edge (its build inputs changed) | **~2 s of refused connections**; requests in flight on the old edge cut | — | 2 s |
| v0.3.0's contract migration (row-level security on) while v0.2.0 serves | nothing: no request failed | — | — |
| a rollback across a migration (v0.3.0 → v0.2.0) | nothing, outside an edge swap: the code rolls back, the schema stays (ADR-0015). Before the fix, the deploy refused to start ("Can't locate revision") | the deploy says "the database is ahead" | — |
| a broken release (`make deploy` of an image that never gets healthy) | nothing: the new api is removed after 90 s, and the old one never stopped (1,018 of 1,018 requests OK) | the deploy fails loudly (exit 1) | — |
| memory limit below the working set | workers and then PID 1 OOM-killed in a loop (one run: 8 container restarts in 30 s); all streams cut; site down until the limit is fixed | `ContainerOOMKilled` (verified firing) | 3 s |

### What the drills found, and changed

#### The database path hung, leaked, and answered 500

Before (ADR-0010):
- Postgres stopped: reads hung for 36 s, and chat answered **500**.
- Postgres frozen: chat got nginx's HTML **504 after 120 s**.
- `/ready` ignored its own 2 s timeout.
- Worst of all, freezing Postgres for 20 s under load left the 13 requests
  caught mid-query **stuck forever**, holding 13 of the 40 pooled
  connections with Postgres healthy again. Each hiccup would take more
  pool slots, until only a restart helped.

The cause is in asyncpg. When a query's client-side timeout fires, or its
task is cancelled, asyncpg asks the server to cancel it. Every later
operation on that connection, including the rollback that returns it to
the pool, first waits for the server's acknowledgement, with no timeout.
If the connection dies first, which is exactly what happens when Postgres
is frozen, that wait never ends. The fixes:
- Query time is capped on the server side only (`statement_timeout`, and
  PgBouncer's `query_timeout`).
- PgBouncer fails fast: queue 5 s, connect 5 s, login retry 2 s, DNS
  negative cache 1 s.
- A connection closed mid-query (SQLSTATE 08003) is mapped to 503.
- The readiness probe abandons a slow check instead of cancelling it.
  Cancelling it leaked a connection too, which was measured, then fixed.

**Test pattern worth copying:** a freeze *under load*, followed by a
check that the pools are empty. Single-request drills never showed the
leak; the gauge `db_pool_connections_in_use` did. Build that gauge from
the pool's own accounting: checkout *events* fire only after the
pre-ping succeeds, and a gauge built on them showed 2 while 10 requests
were stuck.

#### nginx answered in HTML

When the api is unreachable, nginx answers for it. It used to send its
own HTML error page, so clients got a second error format with no request
id. Now `error_page 502 504 @api_error` returns the api's JSON shape
(`upstream_unavailable`, `request_id`), keeps the status code, and adds
`X-Request-ID` and `Retry-After`. The api's own JSON errors pass through
untouched, because `proxy_intercept_errors` stays off.

#### Memory limits were soft

On a host with swap, a compose memory limit allows as much again in swap:
`MemorySwap` defaults to 2× the limit. A container growing past its limit
then swaps. It gets slower, logs nothing, and raises no OOM event, so
nothing alerts. Measured with a process allocating 5 MB at a time under
`--memory 50m`:
- swap left at Docker's default: it reached **95 MB** before exit 137;
- `--memory-swap 50m`: it died at **45 MB**.

In the first drill run, the api put 48 MiB in swap and kept serving
without a single error. Every limited service in `compose.prod.yaml` now
sets `memswap_limit` equal to its memory limit. That matches how
Kubernetes and ECS run containers, and it makes an OOM loud: the kernel
kills the process, gunicorn replaces a killed worker, `ContainerOOMKilled`
fires. The whole path was verified in the drill.

What an OOM looks like, for the runbook:
- **One worker killed:** the container stays up and "healthy", and
  `OOMKilled` stays false. The only traces are gunicorn's `Worker (pid:N)
  was sent SIGKILL! Perhaps out of memory?`, the cgroup's `memory.events`
  `oom_kill` counter, cAdvisor's `container_oom_events_total` (the alert),
  and the kernel log: `journalctl -k | grep -iE "out of memory|killed
  process"` names the process, its RSS and the container's cgroup.
- **Limit below the working set:** a crash loop. The kernel killed
  workers, then gunicorn's master (PID 1), so the whole container
  restarted.
- **Do not trust Docker's `State.OOMKilled`:** in one run the kernel log
  showed PID 1 OOM-killed, and the flag read `false` after the restart.
  The cgroup counters reset on restart too; cAdvisor's counter and the
  kernel log don't.
- **Lowering a live container's limit** (`docker update`) OOM-killed
  processes even with swap allowed, because reclaim gives up quickly
  during a limit change. That's a blunt tool: use it for drills, not in
  production.

#### `docker kill` is not a crash

After `docker kill`, the api never came back: exit 137, RestartCount 0.
Docker records `docker kill` as a manual stop, and `restart:
unless-stopped` respects that. A real crash (SIGKILL to PID 1 from the
host, as the kernel's OOM killer would send) was restarted in under a
second. Test restart policies with a real crash, not with `docker kill`.
Inside the container, PID 1 ignores SIGKILL, so the drill sends it from
the host PID namespace.

#### A single container means a deploy takes the site down

`docker compose up -d` replaces a container by stopping the old one
first: gunicorn stops accepting connections and lets in-flight requests
finish (up to `graceful_timeout`, 120 s), and only then does the new
container start. In-flight streams survived, but new requests got 502 for
6.7 s: the longest in-flight stream plus boot time. With a 2-minute answer
in flight, that's 2 minutes of 502s. The only way to have neither cut
streams nor refused requests is two instances with traffic moved between
them.

`make deploy` (`scripts/deploy.sh`, ADR-0011) does that on one host: it
starts the new api next to the old one, waits until it is healthy and
nginx resolves it (`resolve`, `valid=10s`), then stops the old one, which
drains. Measured at 10 probes a second: zero failed or slow requests
while the api was swapped. After a rolling deploy the api container is
`api-2`, `api-3`, and so on: tooling must look it up (`docker compose ps
-q api`), never assume `api-1`.

#### Bind mounts pin the directory, not the path

A `git checkout` deleted and recreated `infra/observability/prometheus`.
The running Prometheus and Alertmanager kept a view of the old, now
empty, directory. They ran fine on their in-memory configuration, and the
next config reload failed with "no such file". Recreate containers after
switching branches under a running stack, or mount files through a path
git does not replace.

#### Keep-alive connections hide a network partition

With the api cut off the network, requests that needed a new connection
failed in 2 s (`proxy_connect_timeout`). The first request after the cut
went out on an existing keep-alive connection and waited out the whole
read timeout. Connect timeouts don't protect requests already on a
connection; read timeouts do, so keep them only as long as the slowest
legitimate response.

### By component

For each part: what failure looks like, what limits the damage, and what
risk remains.

**The TLS edge and nginx (one container each).**
- The edge down: the site is down (connection refused). nginx down: the
  edge answers the JSON `upstream_unavailable` after 5 s.
- Nothing in the stack alerts on either, because Prometheus scrapes the
  api directly ([not here yet](#not-here-yet)).

**api worker.**
- *A crash or OOM kill* cuts that worker's in-flight requests; gunicorn
  replaces the worker.
- *A blocked event loop* (sync I/O or CPU work in async code) stalls every
  request on that worker. It shows as `event_loop_lag_seconds`
  (`EventLoopLagHigh`). After `timeout` (30 s) without a heartbeat,
  gunicorn kills it (`WORKER TIMEOUT`). ✅ in the load tests.
- The worker count follows the CPU limit. More workers than CPUs only
  adds contention.

**api container.**
- A crash is restarted by the restart policy (✅ < 1 s here). A crash
  *loop* shows as `ApiDown`.
- *Unsafe production settings* stop it at startup, with the list of what
  to fix (ADR-0023): loud, before any request.

**PgBouncer.**
- *Down:* instant 503.
- *Frozen:* new requests get a 503 after 5 s. Requests already in flight
  wait for it, and once nginx gives up, users get a JSON 504 at 30 s. It
  is the one fault that nothing inside the stack bounds (ADR-0010).
- *Misconfigured auth:* ✅ happened here. PgBouncer's healthcheck
  (`pg_isready`) passed for weeks while every real query failed with
  "wrong password type" (md5 vs SCRAM). Only `/ready`, which runs a real
  query, proves the path.

**Postgres.**
- *Down or frozen:* 503 within 5–15 s (the matrix).
- *Slow query:* `statement_timeout` (10 s), then 503, and `SlowRequests`
  fires.
- *Lock queue:* a DDL statement waiting behind a long transaction makes
  every later query on the table wait behind the DDL ([measured](#postgres)).
  Migrations set `lock_timeout` 5 s so they give up instead.
- *Out of connections:* PgBouncer caps server connections at 20, well
  under Postgres' 100.
- *Disk full:* 📘 Postgres stops accepting writes, and can PANIC if it
  cannot write WAL. `DiskWillFillIn6h` and `DiskAlmostFull` alert before
  that.
- *Major version upgrade:* ✅ happened here. A PG16 data directory under
  the PG17 image refuses to start ("database files are incompatible").
  Upgrading needs dump/restore or `pg_upgrade`, never just a new image
  tag.

**Identity provider.**
- *Down:* only sign-in stops. Sessions are this service's own and last up
  to 12 h, so a provider outage shorter than that is invisible to anyone
  already signed in (✅ `idp-stop` drill). Because sign-in redirects are
  built from cached metadata, users are sent to a login page that fails;
  the api's 30 s check is what notices (`identity_provider_up`,
  `IdentityProviderDown`).
- *Misconfigured* (a rotated client secret, a changed redirect URI, clock
  skew): the provider answers, and every sign-in fails. `SignInsFailing`
  fires, and the api log names the reason.
- *Slow:* the back channel has a 5 s timeout (`OIDC_TIMEOUT_S`); a
  sign-in then fails with "the sign-in service is unavailable" rather
  than hanging.

**Valkey.**
- *Down or frozen:* requests pass unlimited (fail-open, ADR-0004;
  `RateLimiterFailingOpen`), except the chat in production, which refuses
  (`RateLimiterFailingClosed`, ADR-0023): the chat spends money.
- *Full:* `allkeys-lru` evicts counters, so some clients briefly get a
  fresh budget. That's acceptable for rate limiting, and wrong for
  anything you add later that must not be lost.

**LLM provider.**
- *Errors, rate limits, hangs, dropped streams:* each ends the stream
  with a typed `error` event and a request id. Nothing else in the app is
  affected (the matrix).
- *Hang:* heartbeats keep proxies from closing the stream; the read
  timeout (60 s) ends it.
- *Harmful or wrong answers:* the off switch stops every model call
  without a deploy, and alerts and runbooks keep working ([the
  runbook](../runbooks/turn-the-assistant-off.md)).
- 📘 A real provider adds quotas (tokens and requests per minute),
  content filters and region outages. `LLMErrors` and `LLMSlowFirstToken`
  cover them, and the cost panel shows runaway usage.

**Host / VM.**
- Everything shares one machine's CPU, memory and disk, so a noisy
  neighbour (a load generator on the same box, ✅ measured in the load
  tests) skews everything.
- Disk: logs are rotated (3 × 10 MB per container), Prometheus is capped
  (15 d / 2 GB), and the disk alerts fire first.
- 📘 A host reboot brings everything back through the restart policies,
  provided Docker is enabled at boot.

**Deploys and configuration.**
- Bad configuration fails loudly at startup: settings are validated, and
  an empty `WEB_CONCURRENCY` crashed gunicorn (✅, now documented).
- A missing runtime dependency is caught by `make image-check` in CI. ✅
  It happened once: `httpx2` versus `httpx`.
- Migrations run before the api starts, and a failed migration keeps the
  old api running.

**Observability itself.**
- *Prometheus down:* no alerts at all ([not here yet](#not-here-yet)).
- *A removed service* has no `up` series, so only `absent()` notices it
  (`ApiMissing`, ✅ promtool test).
- *Stale bind mounts:* [above](#bind-mounts-pin-the-directory-not-the-path).

### Single points of failure on one VM

| SPOF | Effect | What removes it |
|---|---|---|
| the VM | everything | a second VM or a managed platform (ECS/Kubernetes) across availability zones |
| the TLS edge | site down; replacing it (only when its image changes) refuses connections for ~2 s | a cloud load balancer in front of ≥2 instances, with TLS there |
| nginx | behind the edge: 502 JSON after 5 s while down; its replacement during deploys is absorbed by the edge | ≥2 web replicas behind a load balancer |
| api container | a crash cuts in-flight requests (restarted in < 1 s); deploys no longer (`make deploy`) | a second replica on another host |
| Postgres | writes and reads down | a managed database with a standby (RDS Multi-AZ) |
| PgBouncer | database path down | a managed proxy (RDS Proxy) or one PgBouncer per api host |
| Valkey | rate limiting off (fail-open); the chat refused in production | ElastiCache with a replica |
| the identity provider | new sign-ins; signed-in users continue | a managed provider (Entra ID, Okta) with its own availability; a self-hosted Keycloak needs its own cluster and database |

## Performance and load

How performance was measured here, the bottlenecks, the numbers before
and after each fix, and the method to repeat it. ADR-0009 records the
settings that came out of it.

✅ Everything here was measured on this repo's box:
- the production-shaped stack (`make prod-up`), 2 api workers with 2
  CPUs;
- 2 million seeded alerts;
- load generated through nginx by k6 on the same host. Same-host means
  the load generator competes for CPU, so treat absolute numbers as this
  box's, and the ratios as the lesson.

### The method

1. **A question with a number in it.** "Can we serve 500 alert reads per
   second at p95 < 50 ms?", not "is it fast?".
2. **Realistic data.** `make seed n=2000000 ENV=prod`. The first
   bottleneck (below) was invisible with a few thousand rows.
3. **An open-model load** (below).
4. **Watch all four signals together:** request rate, errors, latency
   percentiles, and saturation (the event-loop lag, CPU per container, the
   database pools).
5. **Find the bottleneck with the right tool.** Database time: `make
   db-top-queries` then `EXPLAIN (ANALYZE, BUFFERS)`. api CPU: a sampling
   profiler. A blocked worker: asyncio debug mode.
6. **Change one thing, measure again, keep the numbers.** Every fix below
   has a before and an after under the same load.

**Open vs closed model.** A **closed** model has N virtual users, each
sending a request, waiting for the answer, maybe pausing, then
repeating. When the server slows down, the users slow down with it: the
load you *offer* drops exactly when the system is struggling. An **open**
model starts requests at a fixed *arrival rate*, however slow the
responses are, as independent users do. At a fixed 20 iterations/s
(open), k6 had to go from 16 to 157 concurrent users to keep the rate,
and exposed a 7 s p95 ([a missing index](#a-missing-index)). A closed
test with 16 users would have quietly offered less load and reported
something far milder.

**Coordinated omission.** In a closed model, a request delayed by a
stall also delays the requests that would have been sent during the
stall. They are never sent, so their bad latencies are never recorded,
and the percentiles look far better than what users experienced. An
open model avoids it. A closed model is right when the question is
about concurrency: "how many simultaneous streams can one worker hold?"
is literally N users each holding one stream (`chat.js`,
`constant-vus`).

**Arrival shape matters as much as the rate.** 100 users sending 1
request/s each, in step, is 100 req/s arriving in bursts. On one event
loop, the last request of a burst waits for the other 99. Measured: the
same 100 req/s gave 1.5 ms p50 when spread evenly, and 29 ms when clumped
(`paced-users.js`). Each request's CPU cost multiplies the queue a burst
builds: after sign-in doubled that cost, 200 users pacing 1 request/s in
step saw p50 182 ms, against 3 ms for the same rate spread out. Know
which shape your real traffic has.

**Rules for any run:**
- **Seed production-sized data** (`make seed n=2000000 ENV=prod`).
- **Warm up, then measure a steady state** long enough for the p99 to
  settle (at least 30–60 s at the target rate).
- **Watch the load generator's own CPU.** A saturated generator reports
  its own queueing as server latency: one tool needed 534% CPU to offer
  200 req/s, where k6 needed 5%.
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

**k6** (`tests/load/k6/`): a JavaScript test script, run by a Go engine.
Open and closed models are explicit executors (`constant-arrival-rate`,
`ramping-arrival-rate`, `constant-vus`). Thresholds make a run pass or
fail. Custom metrics measure what matters (the chat script's
`time_to_first_event`). It pushes results to Prometheus when the
monitoring stack is up, so the dashboard shows the load next to the
system's response. The scripts:
- `alerts-read.js`: open model, reads;
- `chat.js`: closed model, concurrent streams, time to first event;
- `health.js`;
- `compare.js`: `GET /api/alerts?limit=50` at a steady rate;
- `paced-users.js`: the same load from paced users, arriving in clumps.

Other tools follow the same concepts. Before trusting one, check whether
it is an open or a closed model, its timer resolution (some report whole
milliseconds), and its own CPU at the rate you need.
`tests/load/stream_client_bench.py` measures the client-side CPU cost per
streamed chunk.

```
ALERTS_RATE_LIMIT=1000000 CHAT_RATE_LIMIT=1000000 make prod-up
make seed n=2000000 ENV=prod
make obs-up ENV=prod                          # optional: watch it in Grafana
make load s=alerts-read RATE=500 DURATION=60s
make load s=chat VUS=100 DURATION=60s
```

Load tests run on demand, not in CI. A shared CI runner's performance
varies from run to run, and a latency threshold there is either flaky or
too loose to catch anything. Gate performance on a dedicated, quiet
environment if it must be gated.

### How much it serves

**Reads** (`GET /api/alerts?limit=50` through nginx), before sign-in
existed:

| Offered | Achieved | p95 | api CPU | Postgres CPU |
|---|---|---|---|---|
| 500 req/s | 500 | 1.4–1.9 ms | 0.5 core | 0.19 core |
| 1,000 req/s | 1,000 | 173 ms (p50 8 ms) | 1.0 core | |
| 2,000 req/s | 1,710 | 1.78 s (p99 3 s) | 2.0 cores (the limit) | 0.93 core |

The ceiling is api CPU, not the database: more capacity means more CPUs
or replicas; a faster database would not help. Signed in, each request
costs more: ~530 trivial reads/s per core against ~920 before; p95 4.0 ms
at 500 req/s and 19.7 ms at 1,000 ([the cost of signing
in](#the-cost-of-signing-in)).

**Streaming answers** (mock model at 50 tokens/s, ~49 tokens per
answer):

| Concurrent streams | First event p95 | Answer p95 | Failed | Answers/s | api CPU |
|---|---|---|---|---|---|
| 50 | 114 ms | 1.45 s | 0% | 37 | 0.37 core |
| 200 | 435 ms | 1.82 s | 1.14% | 126 | 0.92 core |
| 500 | 2.45 s | 3.89 s | 0.59% | 213 | 1.85 cores |
| 500, after [the cascade's fixes](#a-saturation-cascade) | 1.25 s | 3.05 s | **0%** | **239** | 2 cores (saturated) |

### The bottlenecks, in the order they bite

1. [**A missing index**](#a-missing-index): the first bottleneck arrives
   before any real load does.
2. [**Connection churn**](#connection-churn-a-metastable-state): a
   metastable slow state; a fixed pool ended it.
3. **api CPU on reads:** ~530 signed-in reads/s per core ([the cost of
   signing in](#the-cost-of-signing-in)).
4. **api CPU on streaming:** the openai SDK's 126 µs per chunk; 500
   streams saturate 2 CPUs ([where the CPU goes](#where-the-cpu-goes-profiling-streaming)).
5. [**Event-loop saturation turns into errors elsewhere**](#a-saturation-cascade):
   every wall-clock timeout fires early; `event_loop_lag_seconds` is the
   signal.
6. **Connection budgets:** 2 workers × 20 = 40 PgBouncer clients per api
   replica; `MAX_CLIENT_CONN` (500) allows ~12 replicas; PgBouncer's 20
   server connections sit behind Postgres' 100.
7. 📘 **Provider quotas:** a real provider's tokens-per-minute limit binds
   long before the api's CPU does. Plan capacity from the quota.

### A missing index

**Symptom.** At only 20 iterations/s (40 req/s) of the dashboard's two
alert queries: p95 7 s, and 45% of requests failed; k6 had to start 157
virtual users to keep the arrival rate, which shows the queue building.
The api's errors were `TimeoutError` from the connection pool (5 s), and
nginx returned 503s: every connection was busy with a slow query.

**Finding it.** `make db-top-queries` showed two queries with a mean of
1.5–1.7 s under load. `EXPLAIN (ANALYZE, BUFFERS)` showed each one as a
parallel sequential scan of 2 M rows, reading ~30,000 buffers (~235 MB)
per query. **Run on an idle database, the same `EXPLAIN` said 43 ms and
152 ms**, which looks harmless: 40 copies at once, all scanning the whole
table, is a different workload.

**The fix, and the choice.**

| Index | Newest first | By severity | Size | Insert cost (200 k rows) |
|---|---|---|---|---|
| none | 152 ms | 43 ms | — | 244 ms |
| `(created_at, id)` | **0.125 ms** (index scan backward, 54 buffers) | 3.8 ms | 60 MB | 369 ms (+51%) |
| + `(severity, created_at, id)` | 0.125 ms | 0.063 ms | +78 MB | 726 ms (2 indexes) |

One index shipped. It meets the target 50× over, and the second index
would double the write cost for a query that is already fast. It is
documented with the trigger for adding it (the severity query showing up
in `SlowRequests`). Built with `CREATE INDEX CONCURRENTLY` (749 ms on 2 M
rows) so writes were never blocked. **After, same load:** p95 4.07 ms /
1.6 ms, 0% failed.

### A saturation cascade

At 200–500 streams, errors came from everywhere at once:
- nginx: 57 `recv() failed (104: Connection reset by peer)`;
- the api: 2,042 rate-limiter fail-opens, 407 "too many Redis
  connections", 68 database pool timeouts.

Each had its own cause, and they amplified each other:

| Cause | Why | Fix |
|---|---|---|
| gunicorn recycled workers every ~10,000 requests (`max_requests`) | a recycling worker closes keep-alive connections that nginx is about to reuse; nginx doesn't retry a POST on a reset connection, so the user gets a 502 | recycling off: it guarded against a leak nobody had measured |
| the rate limiter's 50 ms budget expired | the budget is wall-clock time: it includes the time Valkey's reply waits for a busy event loop to read it | 200 ms budget |
| the Redis pool (64 per worker) ran out | more concurrent requests than connections | 256 |
| database pool timeouts | [connection churn](#connection-churn-a-metastable-state) | pool 20, overflow 0 |

After: 0 errors in 10,056 answers, 239 answers/s, 0 connection resets.
The general lesson: **on a busy event loop, every timeout measured in
wall-clock time fires early**. The first symptoms of CPU saturation were
fail-opens and pool timeouts at 46% CPU, not slow responses. That's why
`event_loop_lag_seconds` exists, with an alert on it.

### Connection churn (a metastable state)

Locust showed ~90 ms per request where k6 showed 2 ms, at the same rate.
- PgBouncer counted 196 new logins in 20 s under Locust, against 6 under
  k6. Each login is a SCRAM handshake: p50 12.6 ms.
- SQLAlchemy *discards* overflow connections when they are returned.
  Irregular arrivals pushed the pool into overflow, the discarded
  connections had to be recreated, and that made requests slower.
- Slower requests kept concurrency high, which kept the pool in overflow.

A system stuck in a slow state by its own slowness is metastable: it
stays there after the trigger is gone. Fix: a fixed pool with no overflow
(20 per worker). Logins dropped from 196 to 5, and Locust's p50 from 97 to
35 ms. The remaining ~30 ms was not a bug: it was [the arrival
shape](#the-method) of Locust's load. The loop-lag metric (sampled every
250 ms) under-samples 50 ms bursts, so this is also where it stops being
a precise instrument.

### The cost of signing in

Sign-in (ADR-0013) put work in front of every request: read the session
cookie, look up the session, the user and their teams, and tell Postgres
who is asking (`set_config`, for row-level security). Measured A/B: the
previous release's image and this one, behind the same nginx, same data,
same load, one after the other (`k6 alerts-read.js`, org admin, whose
query is the global list the old version ran):

| | 500 req/s | 1,000 req/s | 1,500 req/s offered |
|---|---|---|---|
| before sign-in | p95 2.1 ms, api 0.53 core | p95 2.6 ms, 1.09 cores | p95 6.4 ms, 1.71 cores |
| sign-in, first version | p95 6.5 ms | **933 req/s achieved, p95 1.07 s** | — |
| sign-in, one query in the request's transaction | p95 4.0 ms, 0.95 core | p95 19.7 ms, 1.86 cores | saturated: 1,039 req/s, p95 2.4 s |

**Where it went.** Count the database round trips per request:
- *Before sign-in:* 3. The pool's pre-ping, the query, and the rollback
  when the session closes.
- *The first version:* 8. The session and the memberships were two
  queries; authentication then committed its own transaction, so the
  request's query started a new one: the pool's check-in and check-out,
  another pre-ping, and `set_config` in the new transaction.
- *The fix:* 5. One query returns the session, the user and one row per
  membership (LEFT JOINs). It runs in the request's own transaction, and
  `set_config` follows in the same transaction. Only the 5-minutely
  `last_seen_at` touch commits on its own.

Each round trip costs the api CPU (SQLAlchemy and asyncpg, per statement),
and the api's CPU is the ceiling. So authentication still roughly doubles
the cost of the cheapest request: ~530 trivial reads per second per core,
against ~920 before. Levers left, not taken:
- **A per-worker cache of sessions** (10–30 s): one round trip less. A
  revoked session would stay valid for up to the cache's lifetime.
- **`set_config` inside the authentication query**: one round trip less.
  But the role logic would then be written twice, in Python and in SQL.
- **More cores or replicas.** The cost is per request and scales out
  linearly.

For chat, none of this shows: a streamed answer takes seconds.

### Where the CPU goes: profiling streaming

py-spy (a sampling profiler) at 300 concurrent streams, 2,276 samples,
workers 57% busy:

```
openai SDK          40.1%    starlette/fastapi   4.5%
httpx2 / httpcore2  21.9%    our code            2.5%
asyncio loop        18.2%    sqlalchemy          2.2%
pydantic             5.0%    SSE json + logging  2.5%
```

**Reading a flame graph:** each box is a function, and its *width* is the
share of samples in which it was on the stack. Wide boxes are where time
goes. Height is only call depth. Look for wide plateaus near the top:
functions that spend time themselves rather than calling others.

The suspects, measured one at a time: `json.loads` of a chunk, 1.6 µs;
the SDK's pydantic validation of it, 4.0 µs. Neither is the bottleneck.
End to end per chunk, the SDK's client side costs **126 µs** against
**55 µs** for the raw HTTP client plus `json.loads`
(`tests/load/stream_client_bench.py`). About 12,500 chunks/s × 126 µs ≈
1.6 cores, which matches the saturation.

**Decision: keep the SDK** (ADR-0009). Dropping it would roughly double
streams per core, but we'd own the provider protocol, and a real
provider's quota binds long before 500 concurrent streams.

### What a blocked event loop looks like

An experimental copy of the chat code called a *synchronous* HTTP client
inside the async stream (never committed):
- `/health`, which does no I/O, went from 1.2 ms to up to 4.8 s with 20
  streams.
- Answers fell to 1.57/s with 20 users (the correct code: 37/s with 50).
- `ruff --select ASYNC,B,S` said "All checks passed!": linters don't know
  which SDK clients block.

How each tool showed it:
- **py-spy dump:** the worker's main thread sitting in `read
  (httpcore2/_backends/sync.py:127)`.
- **asyncio debug mode:** `Executing <Task ...> took 1.3 seconds`, 60
  times, each naming the task.
- **gunicorn:** with the provider stalled for 35 s (beyond the 30 s
  `timeout`), `WORKER TIMEOUT`, then the workers killed. The result: 2 ×
  502, and 2 answers cut off.

### Reading several teams at once

A user in several teams reads "the newest alerts of these teams". The
obvious query hands the choice to the planner:
```sql
SELECT ... WHERE team_id = ANY(:teams) ORDER BY created_at DESC, id DESC LIMIT 50
```
With 2–2.5 M rows and a team index `(team_id, created_at, id)` (`EXPLAIN
(ANALYZE, BUFFERS)`, `make psql`):

| Teams read | `= ANY(...)` | LATERAL, one team at a time |
|---|---|---|
| one small team | 0.24 ms (the team index) | same |
| a small and a medium team | 0.19 ms (walks the global time index, filters) | 0.11 ms |
| two sparse teams | 2.9 ms (sorts all their rows) | 0.07 ms |
| a small team and a **large, quiet** one (500k rows, all 90+ days old) | **13.2 ms**: walked the time index, 103,036 rows filtered out | 0.06 ms |

The planner's choice depends on the data, and the bad case grows with the
table. `app/queries.py` reads each team through its own index and merges
the results. The cost is bounded by teams × limit index entries, whatever
the data:
```sql
SELECT a.* FROM unnest(:teams) AS t(id)
CROSS JOIN LATERAL (SELECT * FROM alerts WHERE team_id = t.id
                    ORDER BY created_at DESC, id DESC LIMIT 50) a
ORDER BY a.created_at DESC, a.id DESC LIMIT 50
```
Measured through the api: a user in two of three seeded teams (333k
alerts each), at 500 req/s, p95 7.7 ms for the list and 9.6 ms for the
severity-filtered one (`make load s=alerts-read RATE=250`).

**Row-level security** (ADR-0014) adds its policy as a filter on the rows
a query fetches. A page of a two-team read still walks the team index:
0.53 ms, on 1 M rows, as the app role with a caller set. The policies
read their settings through scalar subqueries, which Postgres evaluates
once per query (an InitPlan). Written as plain function calls they are
evaluated per row: a count over 1 M rows (666 k visible) took 100 ms that
way, 44 ms with the subqueries.

**The migration that added teams** ran on the 2 M-row table while the
previous release served reads and writes: a column with a default
(instant on Postgres 11+); a foreign key added `NOT VALID`, then
validated in its own transaction; the team index built `CONCURRENTLY`.
It took 1.5 s, and the 116,708 requests made meanwhile had 0 errors and
no latency spike.

### Startup and memory

- **Import time is 734 ms** per worker, measured in the production image:
  ```
  $ docker run --rm --entrypoint python triage-assistant-api:check -X importtime -c "import app.main" 2>&1 | sort ...
     733.9 ms   app.main          (whole import)
     207.2 ms     fastapi
     175.0 ms     app.llm -> openai (openai.types alone: 162.8 ms)
     146.3 ms     asyncio
  ```
  Every new worker pays it: deploys, scale-out, a worker restarted after
  an OOM kill. The virtualenv ships precompiled (`UV_COMPILE_BYTECODE=1`,
  3,029 `.pyc` files), so none of it is compile time.
- **Memory per request:** no growth, measured with tracemalloc over 1,000
  and 4,000 requests: the same ~48 KiB both times, a bounded cache. The
  first attempt reported a false leak: without `gc.collect()` before each
  snapshot, objects in reference cycles still look allocated.

### Checklist for the next investigation

- [ ] A target with a number, and seeded data at production volume
- [ ] An open-model load (k6 `constant-arrival-rate`), a warm-up, then a
      steady state long enough to see the p99
- [ ] The dashboard open: rate, errors, p95/p99, loop lag, pools, CPU per
      container
- [ ] The load generator's own CPU checked: a saturated generator
      measures itself
- [ ] `make db-top-queries` reset before (`pg_stat_statements_reset()`)
      and read after
- [ ] A profile during the steady state, if the api's CPU is the limit
- [ ] One change at a time; before and after numbers in the PR; an ADR if
      it changes a default

## Not here yet

📘
- **Outside-in checks.** Nothing notices when the whole VM, the edge or
  nginx is down, and nothing notices when Prometheus itself stops. Add an
  external uptime check on `/healthz` (a blackbox probe from outside the
  VM, or the cloud's health checks), and an always-firing "dead man's
  switch" alert that an external service expects to keep receiving.
- **Central log storage** (Loki, CloudWatch Logs, ELK). Docker's rotation
  keeps about a day of busy logs. Ship them off the host before you need
  last week's.
- **A restart-count alert:** `changes(container_start_time_seconds[15m])
  > 3` on cAdvisor data would catch a loop that is briefly up between
  crashes.
- **Burn-rate alerts on the SLOs**, once they are agreed
  ([production](production.md#service-level-objectives)).
