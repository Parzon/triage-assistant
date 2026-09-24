# Debugging

How to find out what the system is doing, from "one user saw an error" to
"every request is slow". Most tools are wired into a `make` target; every
one marked ✅ has been run against this repo, and the outputs quoted are
real. 📘 = recommended practice not exercised here.

Most targets act on the dev stack; add `ENV=prod` for the production-shaped
stack (`make prod-up`). The gunicorn target always targets the production
api container.

## Start here: symptom → tool

| Symptom | First | Then |
|---|---|---|
| One user got an error | `make trace id=<request id>` (id from the error message, the `X-Request-ID` header, or the UI) | the traceback in the api's log line |
| Everything is slow at once | Grafana: **event loop lag** panel | a slow request's trace (`make trace id=...`); asyncio debug mode locally |
| One route is slow | `make db-top-queries` | `EXPLAIN (ANALYZE, BUFFERS)` in `make psql` |
| Requests hang, then 503 | `make db-activity`, `make db-locks` | `curl /api/ready` says which dependency |
| nginx returns 502/504 | nginx log: `upstream_time` vs `request_time`, error lines (below) | `make ps ENV=prod`, `make gunicorn c="show workers"` |
| `WORKER TIMEOUT` in the api log | something blocks the event loop | asyncio debug mode locally (below) |
| Container restarted / exit 137 | `docker inspect` (ExitCode, OOMKilled, RestartCount) | Grafana **OOM kills** panel; `memory.events` |
| A service can't reach another | `make ps` (running and healthy?), `/api/ready` | `docker compose exec api getent hosts db` (does the name resolve?) |
| Rate limiting behaves oddly | `make redis-slowlog`, `make redis-cli` | the `ratelimit_decisions_total` panel |
| Nobody can sign in, or one person can't | the api log's `"sign-in failed"` lines (code and reason) | "Signing in" below: the provider's log, the flow with curl |
| Someone sees too much or too little | `GET /api/me` as them (their teams and roles) | the provider's groups for them; `make psql`: `memberships` |
| A logic bug you can reproduce | breakpoints: `make debug-up` + VS Code | `pdb` |
| A crash with no traceback | faulthandler (on in every image) | the exit code and `OOMKilled` in `docker inspect` |
| Something odd in the browser | DevTools Network tab | Playwright trace (`make e2e`, then the trace viewer) |

The first five minutes of any incident, in order: `make ps ENV=prod`
(what is unhealthy or restarting), `curl -s localhost:$HTTP_PORT/api/ready`
(which dependency), the Grafana dashboard (errors, latency, lag, pools),
then the logs of one failing request.

## Request ids: one request across every layer ✅

**How it works.** nginx generates a 32-hex `$request_id` for every
request and sends it upstream as `X-Request-ID` (`snippets/proxy.conf`).
The api's middleware adopts it: a malformed or oversized id is replaced,
never trusted. It puts the id in a `contextvar`, so every log line
written while handling that request carries it without being passed
around. It returns the id in the `X-Request-ID` response header and in
every error body. When nginx itself answers for a failed api, its JSON
error carries the same id (`@api_error` in `default.conf`).

```
$ make trace id=4f1c...  ENV=prod
{"logger":"app.triage","msg":"chat answered","duration_ms":1270,"ttft_ms":321.5,...}
{"logger":"app.access","status":200,"duration_ms":1280.8,"route":"/chat/stream",...}
{"request_id":"4f1c...","status":200,"request_time":1.281,"upstream_time":"1.281",...}   <- nginx
```

The same request, seen by the model call, the api and nginx. If nginx's
`request_time` is much larger than `upstream_time`, the time went into
the client connection (slow client, large body), not the api.

This is also the support workflow: a user pastes the id from the error
message, and you find the traceback in seconds.

## Logs ✅

Every service logs one JSON object per line to stdout; Docker keeps them
(rotated: 3 × 10 MB per container, `x-logging` in `compose.yaml`).

```
make logs S=api                                   # follow one service
docker logs --since 10m triage-assistant-prod-api-1 2>&1 | jq -c 'select(.level=="error")'
docker logs --since 10m triage-assistant-prod-api-1 2>&1 | jq -r 'select(.exc) | .exc'   # tracebacks
docker logs --since 5m triage-assistant-prod-web-1 | jq -c 'select(.status >= 500)'
```

Gotchas:
- `docker logs --since 2026-09-23T04:21:00` without a zone reads the time
  as **local** time. Use relative times (`--since 5m`) or add `Z`.
- `docker run --rm` deletes the container, and its logs with it, the
  moment it exits. For a one-off container whose output you need, drop
  `--rm` or redirect its stdout.
- A JSON log line is only as useful as its fields: `route` is the template
  (`/alerts/{alert_id}`), `request_id` is always present, and exceptions are
  in `exc` as one string. Do not log request bodies: alert text and chat
  questions can contain personal data.

## Breakpoints

### VS Code, attached to the containerised api (debugpy) ✅

**How it works.** `debugpy` is the debug server VS Code's Python
debugger talks to. It runs inside the Python process and speaks the Debug
Adapter Protocol (DAP) over TCP. `compose.debug.yaml` starts the dev api
under `python -m debugpy --listen 0.0.0.0:5678`, published on
127.0.0.1:5678 only. VS Code connects to it, sends your breakpoints as
file paths, and debugpy installs them in the running interpreter. The
paths differ: `apps/api/app/...` on your machine is `/api/app/...` in the
container. The `pathMappings` in `.vscode/launch.json` translate them.
Without that mapping, breakpoints show as "unverified" and never hit.

```
make debug-up          # api under debugpy (and asyncio debug mode)
# VS Code: Run and Debug -> "Attach to api (make debug-up)"; set a breakpoint; send a request
make debug-down        # back to the hot-reload server
```

Verified with a DAP client sending what VS Code sends: attach, then
`setBreakpoints` on `app/routes/health.py:30` (verified: true), then
`GET /health`. The result was a `stopped` event, reason `breakpoint`, top
frame `health app/routes/health.py:30`. The request waited while paused,
and `continue` returned `{"status":"ok"}`.

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

### pdb in a terminal ✅

`breakpoint()` in the code, or a breakpoint set from the command line
without editing anything:

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
to read from. `docker compose run` gives the server your terminal.
(`where 1` is not valid in Python 3.13's pdb; use `w`.)

In production images `PYTHONBREAKPOINT=0` turns a forgotten
`breakpoint()` into a no-op ✅ (measured 0.23 s for the request).
Otherwise a worker would wait for keyboard input forever, until gunicorn
killed it.

## Crashes without a Python traceback: faulthandler ✅

Every image sets `PYTHONFAULTHANDLER=1`. When the interpreter itself
crashes (a segfault in a C extension, an abort), Python prints the
traceback of every thread before dying. Without it, a segfault is a
silent exit code 139. Measured with `ctypes.string_at(0)`: "Fatal Python
error: Segmentation fault" followed by the Python stack down to the
handler line.

## "The loop is blocked": asyncio debug mode ✅

`PYTHONASYNCIODEBUG=1` (on under `make debug-up`) makes asyncio log any
callback that holds the event loop for more than 100 ms. It also logs
coroutines that were created but never awaited, with where they were
created. In the blocking-client lab (a synchronous HTTP client called
inside an async generator) it logged 60 lines of `Executing <Task ...>
took 1.3 seconds`, each naming the task. It is too slow for production.
There, the `event_loop_lag_seconds` metric and the `EventLoopLagHigh`
alert do the same job.

Linters did not catch that blocking call: `ruff --select ASYNC,B,S`
reported "All checks passed!". They don't know which SDK clients are
synchronous.

## Which profiler for which question

| Question | Tool | Why this one |
|---|---|---|
| Where does a *running* server spend its time? | 📘 py-spy | a sampling profiler that reads the process from outside: no code change, no restart, safe in production |
| How many times is each function called, exactly? | `cProfile` (standard library) ✅ | deterministic: counts every call; it slows the code it measures (+40% on the import below), so the absolute times are inflated |
| Why is startup slow? | `python -X importtime` ✅ | per-module import time; cProfile shows only `importlib` frames for this |
| Is memory growing per request? | `tracemalloc` (standard library) ✅ | compares live allocations between two snapshots, by source line. Call `gc.collect()` before each, and run two sizes: a leak grows with the number of requests, a cache settles |
| Which native allocations (C extensions) grow? | 📘 memray | must be installed in the *target* interpreter (`memray run`, or `memray attach`, which injects into it), so dev/test images only, never the production image |

Startup, measured in the production image:

```
$ docker run --rm --entrypoint python triage-assistant-api:check -X importtime -c "import app.main" 2>&1 | sort ...
   733.9 ms   app.main          (whole import)
   207.2 ms     fastapi
   175.0 ms     app.llm -> openai (openai.types alone: 162.8 ms)
   146.3 ms     asyncio
```

Every new worker pays this (deploys, scale-out, a worker restarted after
an OOM kill). The virtualenv ships precompiled (`UV_COMPILE_BYTECODE=1`,
3,029 `.pyc` files), so none of it is compile time.

## gunicorn's control socket ✅

gunicorn 25.1+ has a control socket. Here it lives at `/tmp/gunicorn.ctl`,
because the default under `$HOME` is not writable with a read-only root
filesystem.

```
make gunicorn c="show workers"     # PID  AGE  BOOTED  LAST_BEAT (heartbeat age per worker)
make gunicorn c="show stats"
make gunicorn c="worker add 1"     # temporarily; the configured count returns on restart
```

A worker whose `LAST_BEAT` keeps growing is blocked, and is about to be
killed with `WORKER TIMEOUT`.

## Postgres ✅

```
make db-activity   [ENV=prod]   # every connection: state, how long, which query
make db-locks      [ENV=prod]   # who is blocked, by whom (pg_blocking_pids)
make db-top-queries [ENV=prod]  # pg_stat_statements: calls, mean, total, % of all DB time
make psql                        # then EXPLAIN (ANALYZE, BUFFERS) <query>;
```

The lock-queue lab (`db-locks` shows the chain):
1. A long transaction held a lock.
2. A migration's `ALTER TABLE` (with no `lock_timeout`) queued behind it.
3. Every new `SELECT` on the table queued behind the `ALTER`: Postgres
   grants locks in arrival order.
4. Users got 503 after exactly 10.0 s, the app role's `statement_timeout`.

This is why migrations here set `lock_timeout = 5s` (`SET LOCAL` inside
Alembic's transaction), so a blocked `ALTER` gives up instead of stalling
the table.

Reading `db-top-queries`: `total_ms` (calls × mean) is what costs
capacity. On an idle database, postgres-exporter's own statistics query
was 24.9% of all DB time (67 calls, 244 ms each): monitoring has a cost.
Reset with `SELECT pg_stat_statements_reset();` before a measurement.

`EXPLAIN` gotcha: run it under the same conditions as the problem. The
missing-index query took 43–152 ms idle, which looks harmless. Under
40 req/s it was p95 7 s with 45% failures, because every copy scanned
the whole table in parallel. An idle `EXPLAIN` is not a load test (see
the performance chapter).

`db-activity` gotcha: pooled connections show `idle` with `ROLLBACK` as
their last query. That is SQLAlchemy resetting each connection when it
returns to the pool, not an error.

## Valkey (the rate limiter's store) ✅

```
make redis-slowlog [ENV=prod]   # SLOWLOG GET 10 + INFO commandstats
make redis-cli                  # then e.g. KEYS 'LIMITS*' (fine on a tiny dev store only)
```

Measured: an empty slowlog, `incrby` 36,509 calls at 0.52 µs each. The
store is never the bottleneck; the network round trip and the event loop
are. 📘 `MONITOR` streams every command to your terminal and slows the
server: never on a production instance.

## Row-level security ✅

"The table is empty" as the app role, but not as the owner: that is
row-level security with no caller named (ADR-0014). `make psql` connects
as the owner, which the policies do not apply to. To see what a caller
sees, as the app role in a direct session:
```
SELECT set_config('app.read_team_ids', '{2,3}', false);   -- their team ids
SELECT count(*) FROM alerts;
```
A 500 whose traceback says "new row violates row-level security policy"
means the app skipped its own role check: Postgres refused the write.

## Signing in ✅

A sign-in crosses the browser, the api and the identity provider, so look
at all three:
- **The api's log.**
  - `"sign-in failed"` carries a `code` (`login_failed`,
    `invalid_token`, `idp_unavailable`) and a `reason`: "code exchange
    refused" (with the provider's `error`, such as `invalid_client` for a
    wrong secret), "ID token rejected: ..." (the failed check: expired,
    audience, issuer), "nonce does not match".
  - `"identity provider unavailable"` carries the URL it could not
    reach.
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
- **Is the provider up, as the api sees it?**
  `curl -s localhost:8088/api/ready`, then `identity_provider` in the
  checks. The api checks it every 30 s.
- **What does the api think of a user?** `GET /api/me` with their session
  shows their teams and roles as the last sign-in recorded them. Roles
  change only at the next sign-in: `make revoke email=...` forces one.
- **Why a 403 on a POST?** `csrf_failed`: the request's `Origin` is not
  exactly `PUBLIC_URL`. The usual cause is opening the site under another
  name (`127.0.0.1` vs `localhost`, a different port).

## nginx ✅

Access log fields (JSON): `status`, `request_time` (whole request, client
included), `upstream_time` (time waiting for the api), `request_id`.
Error log lines seen in this repo and what they meant:

| Line | Meaning here |
|---|---|
| `recv() failed (104: Connection reset by peer) while reading response header from upstream` | the api closed a keep-alive connection nginx was reusing (gunicorn `max_requests` recycling under load); POSTs are not retried, so the user got a 502 |
| `upstream prematurely closed connection` | the api process died mid-response (a worker killed or crashed) |
| `connect() failed (111: Connection refused) ... upstream: "http://172.21.0.7:8010"` | nginx connecting to an address the api no longer has: without `resolve`, nginx resolves `api` once at startup |
| `upstream timed out (110: Connection timed out) while connecting` | the api's IP answers nothing (container gone or network cut): a 504 after `proxy_connect_timeout` (2 s) |

## Containers ✅

```
docker inspect -f '{{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} restarts={{.RestartCount}}' triage-assistant-prod-api-1
docker inspect -f '{{json .State.Health.Log}}' triage-assistant-prod-api-1 | jq   # last healthcheck outputs
docker exec triage-assistant-prod-api-1 cat /sys/fs/cgroup/memory.events         # oom_kill counter
docker stats --no-stream
```

Gotchas, each measured in the failure drills:
- **Exit code 137 = SIGKILL.** It is usually the kernel's OOM killer, but
  `docker kill` also produces it. And `docker kill` counts as a manual
  stop, so `restart: unless-stopped` leaves the container down. To
  simulate a crash, kill PID 1 from the host PID namespace (see the `CRASH`
  drill).
- **A gunicorn worker killed by the OOM killer leaves no trace on the
  container**: the master starts a new worker, the container stays
  "healthy", `OOMKilled` stays false. The traces are gunicorn's log line
  `Worker (pid:N) was sent SIGKILL! Perhaps out of memory?`, the cgroup's
  `oom_kill` counter, cAdvisor's `container_oom_events_total`, and the
  `ContainerOOMKilled` alert.
- **The kernel log says who it killed, and why.** `journalctl -k | grep
  -iE "out of memory|killed process"` shows the process name, its
  RSS and the container's cgroup. In one drill it showed a worker (81 MB)
  and then gunicorn's master (PID 1) killed, while `OOMKilled` read
  `false` after the restart. Don't rely on that flag. The cgroup counters
  reset on restart too; cAdvisor's counter and the kernel log don't.
- **`docker events --since` cannot look back.** The daemon keeps only its
  last 256 events in memory, and every healthcheck adds three (`exec_create`,
  `exec_start`, `exec_die`). On this stack, all 256 retained events were
  healthchecks from the last 44 seconds; the api's restarts a few
  minutes earlier were gone. To have that history in an incident, stream it
  continuously into a log you keep (📘 e.g. a systemd unit running
  `docker events --format '{{json .}}'` into journald), and filter out
  `exec_*`.
- **Docker does not restart unhealthy containers.** Restart policies act
  on process exit only. "Unhealthy" matters to `depends_on`, and to
  orchestrators (ECS, Kubernetes) that replace tasks on it.
- **A directory bind mount is pinned to the directory, not its path.**
  When `git checkout` deleted and recreated
  `infra/observability/prometheus`, the running Prometheus kept a view of
  the deleted, empty directory. It ran on its in-memory config until the
  next reload failed ("no such file"). Fix: recreate the container.

## Browser and end-to-end ✅

- DevTools, Network tab: our chat is a `fetch` stream, not an
  `EventSource`, so there is no "EventStream" tab. Read the response
  timing, and the request id from the response headers.
- Playwright records a trace for failed tests (`make e2e`). The trace
  viewer (`npx playwright show-trace <zip>`) shows every request and DOM
  snapshot. It exposed a bug jsdom could not reproduce: clicking Stop
  re-submitted the question, and the trace showed two
  `POST /api/chat/stream`.

## Failure drills

`make drills` injects each dependency failure into the production stack,
records what users see, and restores the stack. `docs/handbook/failure-modes.md`
has the results and what each one taught. Run it after upgrading anything
on the database path (ADR-0010).
