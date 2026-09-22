# ADR-0009: Performance defaults chosen from load tests

**Status:** accepted — supersedes the 50 ms limiter budget in ADR-0004
**Date:** 2026-09-23

## Context

The performance lab (k6, Locust and four other tools against the
production-shaped stack, 2 million seeded alerts; numbers in the
performance chapter of the handbook) turned up these problems, each
reproduced and then re-measured after the change:

1. Newest-first reads were parallel sequential scans of the whole table:
   p95 7 s and 45% failures at 40 req/s.
2. Under load, `max_requests` worker recycling closed keep-alive
   connections that nginx then reused: bursts of 502 on POSTs.
3. The rate limiter's 50 ms budget expired while replies waited for a busy
   event loop, and its 64-connection pool ran out: thousands of fail-open
   decisions at under 50% CPU per worker.
4. SQLAlchemy discards *overflow* connections on return, so irregular
   arrivals churned them (196 new logins in 20 s, each a SCRAM handshake):
   a self-sustaining slow state at ~90 ms per request.
5. The openai SDK's client-side stream handling is two thirds of a
   worker's CPU when streaming (126 µs per chunk vs 55 µs for raw HTTP +
   `json.loads`).

## Decision

| Setting | Before | Now | Why |
|---|---|---|---|
| Index on `alerts (created_at, id)` | none | yes (built concurrently) | 152 ms → 0.1 ms newest-first, 43 ms → 3.8 ms by severity; one index, not two: a second one on (severity, created_at, id) reaches 0.06 ms but doubles insert cost - added when a rarer filter shows up in `SlowRequests` |
| `GUNICORN_MAX_REQUESTS` | 10000 | 0 (off) | a leak mitigation without a measured leak; its side effect was 502 bursts |
| `RATELIMIT_TIMEOUT_S` | 0.05 | 0.2 | wall-clock budgets include event-loop queueing |
| `REDIS_MAX_CONNECTIONS` (per worker) | 64 | 256 | ≥ concurrent requests per worker |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` (per worker) | 5 / 5 | 20 / 0 | size for peak, no churn; PgBouncer keeps it cheap for Postgres |
| `event_loop_lag_seconds` + `EventLoopLagHigh` | — | added | the saturation signal every other timeout depends on |
| openai SDK for streaming | — | kept | ~2× streams per core is not worth owning the provider protocol; provider quotas bind first |

## Consequences

- Measured at 500 concurrent streams on 2 CPUs (saturated): 0% errors
  (was 0.59%), 239 answers/s (was 213), no upstream resets.
- Connection budget: 2 workers × 20 = 40 PgBouncer clients per api
  replica; `MAX_CLIENT_CONN` (500) caps replicas × workers × pool.
- If memory is ever measured to grow, re-enable `GUNICORN_MAX_REQUESTS`
  together with nginx `proxy_next_upstream` for idempotent routes.
