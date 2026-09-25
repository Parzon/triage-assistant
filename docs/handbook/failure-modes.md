# Failure modes, failure points and bottlenecks

What happens when each part of the system fails, measured rather than
guessed. `make drills` injects one fault at a time into the
production-shaped stack, records what a user sees, restores the stack, and
checks it comes back. Run it yourself, and run it again after any change
to the paths it covers.

✅ = measured here, 📘 = expected behaviour or recommended practice, not
exercised in this repo.

## How it was measured

- **Stack:** `make prod-up`, the production images on one host: nginx →
  gunicorn (2 uvicorn workers, 2 CPUs, 1 GiB) → PgBouncer → Postgres, plus
  Valkey and the mock LLM.
- **Rate limits** raised (`ALERTS_RATE_LIMIT=1000000 CHAT_RATE_LIMIT=1000000`)
  so the probes are never throttled.
- **Versions:** Docker 28.4.0, nginx 1.30.5, gunicorn 26.2.0, uvicorn
  0.53.0, FastAPI 0.141.1, SQLAlchemy 2.0.54, asyncpg 0.31.0, PgBouncer
  1.25.2, Postgres 17.11, Valkey 8.1.10, openai 3.17.0. Measured
  2026-09-23.

There are three kinds of drill (`scripts/failure-drills.sh`):

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
- `docker stop` for "down".
- `docker pause` for "frozen": SIGSTOP; the process is alive, its TCP
  connections stay open, and nothing answers. This is the nastiest
  failure there is.
- `docker network disconnect`.
- SIGKILL from the host PID namespace for a crash.
- `docker update --memory` for OOM.
- The mock LLM's admin API for provider failures.

Before each drill, the harness waits until every container is healthy.
An injection that fails is reported as such, never as a result: an early
version recorded "LLM 429 → answer OK" because the mock was still booting
when the fault was sent.

## The matrix ✅

After the fixes described below. "Alert if it lasts" names the alert rule
that covers the fault. The drills end long before any rule's `for:`
duration (1–5 min, so blips don't page anyone), so these fired only in
their own tests: the promtool rule tests (`make obs-check`), the Redis
loop measured end to end (~2 minutes from failure to notification), and
`ContainerOOMKilled`, observed firing in the OOM drill. A fault shorter
than the 15 s scrape interval can leave no trace in the metrics at all:
the two 4-second Valkey drills never show in `redis_up`. The drill log
prints each drill's start time (UTC) so a run can be matched against
Grafana.

| Fault | What users see while it lasts | Alert if it lasts | Back after restore |
|---|---|---|---|
| Valkey stopped | everything works; `/ready` says `redis: degraded`; **no rate limiting** (fail-open, ADR-0004) | `RedisDown` (1 min), `RateLimiterFailingOpen` | 0 s |
| Valkey frozen | same; each limiter call gives up after its 200 ms budget | same | 0 s |
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
| nginx stopped | the TLS edge holds each request for 5 s (it may be a restart), then answers `/api` with the JSON `upstream_unavailable` (502) | **nothing in the stack** (see gaps) | 1 s |
| the TLS edge stopped | connection refused: the site is down (the edge owns ports 80/443) | **nothing in the stack** (see gaps) | 1 s |
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

## What the drills found, and changed

### The database path hung, leaked, and answered 500 (ADR-0010)

Before:
- Postgres stopped: reads hung for 36 s, and chat answered **500**.
- Postgres frozen: chat got nginx's HTML **504 after 120 s**.
- `/ready` ignored its own 2 s timeout.
- Worst of all, freezing Postgres for 20 s under load left the 13
  requests caught mid-query **stuck forever**, holding 13 of the 40
  pooled connections with Postgres healthy again.

Each hiccup would take more pool slots, until only a restart helped.

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

The full story, and the alternatives rejected, are in ADR-0010.

**Test pattern worth copying:** a freeze *under load*, followed by a
check that the pools are empty. Single-request drills never showed the
leak; the gauge `db_pool_connections_in_use` did. Build that gauge from
the pool's own accounting: checkout *events* fire only after the
pre-ping succeeds, and a gauge built on them showed 2 while 10 requests
were stuck.

### nginx answered in HTML

When the api is unreachable, nginx answers for it. It used to send its
own HTML error page, so clients got a second error format with no request
id. Now `error_page 502 504 @api_error` returns the api's JSON shape
(`upstream_unavailable`, `request_id`), keeps the status code, and adds
`X-Request-ID` and `Retry-After`. The api's own JSON errors pass through
untouched, because `proxy_intercept_errors` stays off.

### Memory limits were soft

On a host with swap, a compose memory limit allows as much again in
swap: `MemorySwap` defaults to 2× the limit. A container growing past its
limit then swaps. It gets slower, logs nothing, and raises no OOM event,
so nothing alerts.

Measured with a process allocating 5 MB at a time under `--memory 50m`:
- Swap left at Docker's default: it reached **95 MB** before exit 137.
- `--memory-swap 50m`: it died at **45 MB**.

In the first drill run, the api put 48 MiB in swap and kept serving
without a single error. Every limited service in `compose.prod.yaml` now
sets `memswap_limit` equal to its memory limit. That matches how
Kubernetes and ECS run containers, and it makes an OOM loud:
- The kernel kills the process.
- gunicorn replaces a killed worker.
- `ContainerOOMKilled` fires.

The whole path was verified in the drill.

What an OOM looks like, for the runbook:
- **One worker killed:** the container stays up and "healthy". The only
  traces are gunicorn's `Worker (pid:N) was sent SIGKILL! Perhaps out of
  memory?`, the cgroup's `memory.events` `oom_kill` counter, cAdvisor's
  `container_oom_events_total` (the alert), and the kernel log:
  `journalctl -k | grep -i "out of memory"` names the process and its
  RSS.
- **Limit below the working set:** a crash loop. The kernel killed
  workers, then gunicorn's master (PID 1), so the whole container
  restarted.
- **Do not trust Docker's `State.OOMKilled`:** in one run the kernel log
  showed PID 1 OOM-killed, and the flag read `false` after the restart.
- **Lowering a live container's limit** (`docker update`) OOM-killed
  processes even with swap allowed, because reclaim gives up quickly
  during a limit change. That's a blunt tool: use it for drills, not
  in production.

### `docker kill` is not a crash

After `docker kill`, the api never came back: exit 137, RestartCount 0.
Docker records `docker kill` as a manual stop, and `restart:
unless-stopped` respects that. A real crash (SIGKILL to PID 1 from the
host, as the kernel's OOM killer would send) was restarted in under a
second. Test restart policies with a real crash, not with `docker kill`.
Inside the container, PID 1 ignores SIGKILL, so the drill sends it from
the host PID namespace.

### A single container means a deploy takes the site down

`docker compose up -d` replaces a container by stopping the old one
first:
1. gunicorn stops accepting connections and lets in-flight requests
   finish (up to `graceful_timeout`, 120 s).
2. Only then does the new container start.

In-flight streams survived, but new requests got 502 for 6.7 s, which is
the length of the longest in-flight stream plus boot time. With a
2-minute answer in flight, that's 2 minutes of 502s. The only way to have
neither cut streams nor refused requests is two instances with traffic
moved between them.

`make deploy` (`scripts/deploy.sh`, ADR-0011) does that on one host:
1. Start the new api next to the old one.
2. Wait until it is healthy, and for nginx to resolve it (`resolve`,
   `valid=10s`).
3. Stop the old one, which drains.

Measured at 10 probes a second: zero failed or slow requests while the
api was swapped. Replacing nginx at the end refused connections for
~0.3 s, because one container owns the published port. After a rolling
deploy the api container is `api-2`, `api-3`, and so on: tooling must
look it up (`docker compose ps -q api`), never assume `api-1`.

### Bind mounts pin the directory, not the path

A `git checkout` deleted and recreated `infra/observability/prometheus`.
The running Prometheus and Alertmanager kept a view of the old, now
empty, directory. They ran fine on their in-memory configuration, and the
next config reload failed with "no such file". Recreate containers after
switching branches under a running stack, or mount files through a path
git does not replace.

### Keep-alive connections hide a network partition

With the api cut off the network, requests that needed a new connection
failed in 2 s (`proxy_connect_timeout`). The first request after the cut
went out on an existing keep-alive connection and waited out the whole
read timeout. Connect timeouts don't protect requests already on a
connection; read timeouts do, so keep them only as long as the slowest
legitimate response.

## Failure modes by component

For each part: what failure looks like, what limits the damage, and what
risk remains.

**nginx (one container).**
- When it's down, the site is down: connection refused.
- Nothing in the stack alerts, because Prometheus scrapes the api
  directly, not through nginx. 📘 Add an external uptime check (a
  blackbox probe from outside the VM, or the cloud's health checks)
  before a real launch; it's in the backlog.

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
- 📘 There's no restart-count alert. `changes(container_start_time_seconds[15m]) > 3`
  on cAdvisor data would catch a loop that is briefly up between crashes.

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
- *Down or frozen:* 503 within 5–15 s (matrix).
- *Slow query:* `statement_timeout` (10 s), then 503, and
  `SlowRequests` fires.
- *Lock queue:* a DDL statement waiting behind a long transaction makes
  every later query on the table wait behind the DDL (✅ measured, `make
  db-locks`). Migrations set `lock_timeout` 5 s so they give up instead.
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
- *Misconfigured* (a rotated client secret, a changed redirect URI,
  clock skew): the provider answers, and every sign-in fails.
  `SignInsFailing` fires, and the api log names the reason.
- *Slow:* the back channel has a 5 s timeout (`OIDC_TIMEOUT_S`); a
  sign-in then fails with "the sign-in service is unavailable" rather
  than hanging.

**Valkey.**
- *Down or frozen:* requests pass unlimited (fail-open, ADR-0004;
  `RateLimiterFailingOpen`).
- *Full:* `allkeys-lru` evicts counters, so some clients briefly get a
  fresh budget. That's acceptable for rate limiting, and wrong for
  anything you add later that must not be lost.

**LLM provider.**
- *Errors, rate limits, hangs, dropped streams:* each ends the stream
  with a typed `error` event and a request id. Nothing else in the app is
  affected (matrix).
- *Hang:* heartbeats keep proxies from closing the stream; the read
  timeout (60 s) ends it.
- 📘 A real provider adds quotas (tokens and requests per minute),
  content filters and region outages. `LLMErrors` and `LLMSlowFirstToken`
  cover them, and the cost panel shows runaway usage.

**Host / VM.**
- Everything shares one machine's CPU, memory and disk, so a noisy
  neighbour (a load generator on the same box, ✅ measured in the load tests)
  skews everything.
- Disk: logs are rotated (3 × 10 MB per container), Prometheus is capped
  (15 d / 2 GB), and the disk alerts fire first.
- 📘 A host reboot brings everything back through the restart policies,
  provided Docker is enabled at boot.

**Deploys and configuration.**
- Bad configuration fails loudly at startup: settings are validated,
  and an empty `WEB_CONCURRENCY` crashed gunicorn (✅, now documented).
- A missing runtime dependency is caught by `make image-check` in CI. ✅
  It happened once: `httpx2` versus `httpx`.
- Migrations run before the api starts, and a failed migration keeps the
  old api running.

**Observability itself.**
- *Prometheus down:* no alerts at all. 📘 An external "dead man's
  switch" (an always-firing alert that must keep arriving) catches that.
- *A removed service* has no `up` series, so only `absent()` notices it
  (`ApiMissing`, ✅ promtool test).
- *Stale bind mounts:* see above.

## Single points of failure on one VM

| SPOF | Effect | What removes it |
|---|---|---|
| the VM | everything | a second VM or a managed platform (ECS/Kubernetes) across availability zones |
| the TLS edge | site down; replacing it (only when its image changes) refuses connections for ~2 s | a cloud load balancer in front of ≥2 instances, with TLS there |
| nginx | behind the edge: 502 JSON after 5 s while down; its replacement during deploys is absorbed by the edge | ≥2 web replicas behind a load balancer |
| api container | a crash cuts in-flight requests (restarted in < 1 s); deploys no longer (`make deploy`) | a second replica on another host |
| Postgres | writes and reads down | a managed database with a standby (RDS Multi-AZ) |
| PgBouncer | database path down | a managed proxy (RDS Proxy) or one PgBouncer per api host |
| Valkey | rate limiting off (fail-open) | acceptable; ElastiCache with a replica if limits become a security control |
| the identity provider | new sign-ins; signed-in users continue | a managed provider (Entra ID, Okta) with its own availability; a self-hosted Keycloak needs its own cluster and database |

## Scalability bottlenecks, in the order they bite ✅

Measured in the load tests. The performance chapter has the method
and the raw numbers.

1. **A missing index.** Newest-first reads were parallel scans of 2 M
   rows: p95 7 s and 45% failures at only 40 req/s. One index took them
   to 0.1 ms. The first bottleneck arrives before any real load does.
2. **Connection churn** (metastable). SQLAlchemy discards overflow
   connections on return, so irregular traffic kept reconnecting (196
   SCRAM logins in 20 s). That kept requests slow, which kept concurrency
   high. Fix: a fixed pool (20 per worker, no overflow).
3. **api CPU on reads.** About 500 simple reads/s per api core. With 2
   workers the knee is ~1,000 req/s (p95 1.4 ms at 500/s, 173 ms at
   1,000/s). Postgres was at a fraction of a core.
4. **api CPU on streaming.** The openai SDK costs 126 µs of CPU per
   streamed chunk (a raw HTTP client + `json.loads`: 55 µs). 500
   concurrent streams saturate 2 CPUs: 239 answers/s, 0% errors after
   the fixes.
5. **Event-loop saturation turns into errors elsewhere.** A busy loop
   makes every wall-clock timeout (limiter budget, pool wait) fire
   early. The first symptom of CPU saturation was rate-limiter fail-opens
   and pool timeouts, not slow responses. `event_loop_lag_seconds` is the
   signal.
6. **Connection budgets.** 2 workers × 20 = 40 PgBouncer clients per api
   replica. `MAX_CLIENT_CONN` (500) allows ~12 replicas. PgBouncer's 20
   server connections sit behind Postgres' 100.
7. 📘 **Provider quotas.** A real LLM provider's tokens-per-minute limit
   binds long before the api's CPU does. Plan capacity from the quota.

## Running a game day

1. `ALERTS_RATE_LIMIT=1000000 CHAT_RATE_LIMIT=1000000 make prod-up`, then
   `make obs-up ENV=prod` to watch alerts fire.
2. `make drills` for everything (~15 minutes), or `make drills d="db-freeze
   deploy"`. Output is one Markdown table row per drill.
3. Compare with the matrix above. A new row that differs is either a
   regression or a finding: write it down, fix it, add the drill.
4. Adding a drill: a `case` line in `scripts/failure-drills.sh` with an
   inject command, a restore command, and which drill type measures it.
