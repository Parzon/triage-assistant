# Runbook: alerts

One section per alert in `infra/observability/prometheus/alerts.yml`,
each rule linking here through its `runbook_url`: what it means for
users, what to run first, what caused it before. Commands assume the
production stack on the host (`ENV=prod`). [Operations](../handbook/operations.md#debugging-tools)
explains each tool.

First, for any alert: `make ps ENV=prod` (what is unhealthy or
restarting), `curl -s localhost/api/ready` (which dependency), and the
Grafana dashboard (errors, latency, lag, pools).

## ApiDown

**Users:** the site shows JSON 502/504 errors (nginx answering for a dead
api).
**Check:** `make ps ENV=prod`, `make logs ENV=prod S=api`, `docker inspect`
on the api (`RestartCount`, `ExitCode`: 137 = killed, often OOM).
**Seen before:**
- A crash is restarted by the restart policy in under a second, and
  doesn't fire this alert.
- A crash *loop* does fire it. Look for the traceback at startup: a
  missing dependency in the production image, invalid settings
  (`WEB_CONCURRENCY=""`), or an unreachable database at migration time.
- "APP_ENV=prod refuses to start with these settings": a production check
  failed (ADR-0023). The log names each one and why. Fix the setting, or,
  if this deployment means it (a demo on the mock model), waive it by
  name in `PROD_CHECKS_WAIVED`. `make deploy` never swaps in an api that
  does not become healthy, so the old one keeps serving meanwhile.
- A container stopped with `docker kill` stays down: it counts as a
  manual stop.

## ApiMissing

**Users:** as ApiDown. There is no api container at all, so Prometheus
has nothing to scrape.
**Check:** `make ps ENV=prod`. A deploy that removed the old api and failed
to start the new one? `docker ps -a` shows exited containers.
**Fix:** `make deploy tag=<last good>`, or `make prod-up`.

## PostgresDown

**Users:** every page with data answers 503 `database_unavailable` in 5
seconds or less. Chat refuses before streaming.
**Check:** `make logs ENV=prod S=db`, the disk (`df -h`: a full disk
stops Postgres), `make db-activity ENV=prod` once it answers.
**Seen before:**
- A major version upgrade on an old data directory ("database files are
  incompatible"): restore from a dump.
- After Postgres restarts, PgBouncer reconnects within ~2 s.

## PgBouncerDown

**Users:** as PostgresDown.
**Check:** `make logs ENV=prod S=pgbouncer`. "wrong password type" means
the auth type does not match the server's (SCRAM). "server login has
been failing" means it can't reach Postgres: look at PostgresDown.
**Note:** PgBouncer's healthcheck doesn't authenticate. Only `/ready`
proves the whole path.

## RedisDown

**Users:** nothing visible, but **rate limiting is off**: requests pass
unlimited (fail-open, ADR-0004).
**Check:** `make logs ENV=prod S=redis`, and memory (`maxmemory 128mb`
with LRU eviction; a full store evicts counters, it doesn't stop).
**Fix:** restart it; the counters are disposable.

## ExporterDown

**Users:** nothing. **We are blind** for one dependency: its alerts can't
fire.
**Check:** `make logs ENV=prod S=<exporter>`. postgres-exporter needs the
monitor role's password; pgbouncer-exporter the app role's.

## HighErrorRate

**Users:** more than 5% of requests fail with 5xx.
**Check:** the dashboard's "Requests / s by route and status" (which
route, which status), then `make trace id=<id>` on one failing request.
**Seen before:**
- 503 `database_unavailable`: see PostgresDown / PgBouncerDown.
- 502/504 `upstream_unavailable` from nginx: the api is down or stuck
  (ApiDown, EventLoopLagHigh).
- Bursts of 502 on POSTs under load came from gunicorn worker recycling
  (now off).

## SlowRequests

**Users:** a route's p95 is above 1 s for 10 minutes.
**Check:** `make db-top-queries ENV=prod` (a query whose mean jumped),
then `EXPLAIN (ANALYZE, BUFFERS)` it in `make psql`, *under load*.
Also `make db-locks ENV=prod` (something waiting on a lock) and the
event-loop lag panel.
**Seen before:** a missing index (p95 7 s at 40 req/s), and a lock queue
behind a migration.

## EventLoopLagHigh

**Users:** everything on the api is slow; timeouts fire early (rate
limiter fail-opens, pool timeouts) while CPU may look moderate.
**Check:** CPU per container (the dashboard), then a slow request's trace
(`make trace id=...`): a worker blocked in a synchronous call shows as a
gap no span explains. Locally, asyncio debug mode names the blocking call.
**Seen before:**
- Saturation at 500 concurrent streams on 2 CPUs.
- A synchronous HTTP client inside async code (`/health` took 4.8 s).

**Fix:** remove the blocking call, or add CPUs or replicas.

## RateLimiterFailingOpen

**Users:** requests pass without limits.
**Check:** RedisDown first. If Valkey is up, the limiter is timing out
(200 ms budget) because the event loop is busy: EventLoopLagHigh.

## RateLimiterFailingClosed

**Users:** the assistant answers 503 `rate_limiter_unavailable` to every
question. Alerts and runbooks work (their limits fail open). Production
only: there the chat fails closed, since each question is a model call
and an unlimited chat is an unlimited bill (ADR-0023).
**Check:** as for RateLimiterFailingOpen: RedisDown, then EventLoopLagHigh.
**If Valkey cannot come back soon** and answers matter more than the
bill: `CHAT_RATE_LIMIT_FAIL_CLOSED=false` in `.env`, then `make deploy`
with the running tag. Put it back after.

## DatabasePoolSaturated

**Users:** requests wait for a Postgres connection inside PgBouncer, and
answer 503 after 5 s (`query_wait_timeout`).
**Check:** `make db-activity ENV=prod` (long-running or idle-in-transaction
sessions), `make db-top-queries ENV=prod`.
**Causes:** slow queries holding connections, or more concurrent work
than PgBouncer's 20 server connections.

## AppDatabasePoolExhausted

**Users:** requests wait for one of the api's own pooled connections,
then 503 after 5 s (`pool_timeout`).
**Check:** the "App database pools" panel. **In use near max with low
traffic means leaked connections.** That's requests stuck forever,
which the drills produced before ADR-0010: a timed-out query whose
cancellation Postgres never acknowledged. `make db-activity` and
`make gunicorn c="show workers"`.
**Fix, short term:** `make deploy tag=<current tag>` replaces the workers
without refusing requests. **Long term:** find what leaks; the ADR
explains how the last leak worked.

## LLMErrors

**Users:** chat answers end with an error: `llm_unavailable`,
`llm_rate_limited`, `llm_timeout`, `llm_empty_answer` or `llm_error`.
Everything else works.
**Check:** the "Model calls by outcome" panel (which code), the
provider's status page, and the key's quota in the provider console.
**Codes:**
- `llm_rate_limited`: quota. Raise it, or lower `CHAT_RATE_LIMIT`.
- `llm_error`: the provider refused the request. A revoked or expired
  key (401) or a wrong model name (404); the api log has the status.
- `llm_unavailable`: the provider is down, returns 5xx, or the
  connection broke mid-answer.
- `llm_timeout`: the provider is slow (LLMSlowFirstToken).
- `llm_empty_answer`: the model finished without a word. Almost always a
  reasoning model that spent the whole `LLM_MAX_OUTPUT_TOKENS` thinking:
  see LLMAnswersTruncated.

## LLMSlowFirstToken

**Users:** 10 s or more before the first word of an answer (p95).
**Check:** the provider's latency and status. The model and prompt
size: `CHAT_CONTEXT_ALERTS` alerts go into every prompt. Then our
event-loop lag (a busy api delays tokens too).

## RetrievalDegraded

**Users:** answers still come, but grounded in worse runbook sections:
keyword search alone misses questions worded differently from the
runbook. Nothing looks broken.
**Check:**
- `embedding_requests_total{kind="query"}` by outcome: an `llm_*` error
  means the embedding model fails or is slow (`EMBEDDING_TIMEOUT_S`).
- The api log: "no section has a current vector" means the embedding
  settings changed (model, dimensions or document prefix) and the stored
  vectors were made the old way.

**Fix:** for the model, as for LLMErrors. For the settings: `make reembed
ENV=prod`. It embeds every runbook again; keyword search serves in the
meantime.

## LLMAnswersTruncated

**Users:** answers stop mid-sentence, with "The answer was cut short: it
reached the length limit." under them.
**Check:** the `finish_reason` in the api's "chat answered" log lines,
and whether the model or `LLM_REASONING_EFFORT` changed recently. A
reasoning model's hidden thinking counts against `LLM_MAX_OUTPUT_TOKENS`.
**Fix:** lower `LLM_REASONING_EFFORT`, or raise `LLM_MAX_OUTPUT_TOKENS`,
which raises cost and latency. Run the evals before and after either
change (`make evals`).

## IdentityProviderDown

**Users:** anyone signing in gets the edge's "unavailable" page (the bundled
Keycloak) or the provider's error; **signed-in users are unaffected** -
sessions are this service's own, and last up to 12 hours.
**Check:** `curl -s localhost:8088/api/ready` shows
`"identity_provider": "degraded"`; the api log says why ("identity provider
unavailable", with the URL). Bundled Keycloak: `make logs ENV=prod S=keycloak`.
Also: `"metadata names another issuer"` means OIDC_ISSUER does not match the
provider's - a configuration change, not an outage.
**Fix:** the provider (or the network path to it: DNS, egress firewall,
proxy). Nothing to restart here; the check recovers within 30s.

## SignInsFailing

**Users:** nobody can sign in, though the provider answers.
**Check:** the api log's `"sign-in failed"` lines carry the reason:
`invalid_client` / "code exchange refused" (the client secret was rotated
at the provider but not in OIDC_CLIENT_SECRET), `invalid_token` with "iat"
or "exp" (the api host's clock is off - check NTP), "issued to another
client" (OIDC_CLIENT_ID). A redirect URI refused by the provider never
reaches this service at all: the provider shows its own error page.
**Fix:** align the setting with the provider, then `make deploy` (or
restart the api) to pick it up.

## DiskWillFillIn6h / DiskAlmostFull

**Users:** nothing yet. At 100%, Postgres stops accepting writes.
**Check:** `df -h`, `docker system df` (images, build cache, volumes),
`du -sh backups/`.
**Fix:** `docker builder prune`, `docker image prune`, move old backups
off the host. Prometheus is capped at 2 GB, and container logs at
30 MB each.

## ContainerNearMemoryLimit

**Users:** nothing yet. Next comes an OOM kill.
**Check:** the dashboard's memory-by-container panel: a steady climb (a
leak) or a step (a larger working set after a deploy)? `tracemalloc`
snapshots show growth per request ([operations](../handbook/operations.md#which-profiler-for-which-question)).

## ContainerOOMKilled

**Users:** requests on the killed process failed. For an api worker,
gunicorn has already started a new one, and the container looks healthy.
**Check:**
- `journalctl -k | grep -iE "out of memory|killed process"`: which
  process, its RSS.
- `docker logs` on the api: `Worker (pid:N) was sent SIGKILL! Perhaps out
  of memory?`
- A restart count that keeps growing means PID 1 died too: the limit is
  below the working set (the OOM drill reproduced this).

**Fix:** a limit below what the service needs at rest is a
configuration bug. Raise it in `compose.prod.yaml` from measurements. A
leak is a code bug: ContainerNearMemoryLimit.
