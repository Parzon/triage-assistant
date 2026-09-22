# ADR-0004: The rate limiter fails open

**Status:** accepted
**Date:** 2026-09-22

## Context

Rate limits are counted in Redis (Valkey) so every worker and replica
shares one count. That makes Redis part of every limited request, so the
design must say what happens when Redis is down or hung.

Measured before this decision (2026-09-22 audit, 100 concurrent
`POST /alerts`, Redis frozen with `docker pause`): every request failed
with HTTP 500 after 5–10 s — redis-py's default 5 s socket timeout, plus
queueing for the 40-thread worker pool of a sync endpoint. `/health`
slowed to 2 s. There was no policy; the outcome was an accident.

## Decision

Fail **open**, fast:

- The limiter gets a 50 ms budget per request (`RATELIMIT_TIMEOUT_S`),
  enforced by the Redis client's socket timeouts and an `asyncio.timeout`
  backstop.
- On any Redis error or timeout the request is allowed, a warning is
  logged (`rate limiter unavailable, failing open`), and — once metrics
  exist — a counter is incremented and alerted on.
- `/ready` reports Redis as `degraded` but stays 200: Redis is a soft
  dependency, so its outage must not pull instances out of the load
  balancer.

Measured after (same test): 100 × HTTP 201, mean 251 ms, `/health` 0.5 ms.

## Alternatives considered

| Policy | What happens when Redis fails | Protects | Costs |
|---|---|---|---|
| **Fail open** (chosen) | Requests allowed, logged, alerted | Availability: Redis is not a hard dependency | No limiting during the outage — a runaway client or script can burn LLM budget until Redis is back |
| **Fail closed** | Requests rejected with 503 | Spend and downstream capacity | Redis becomes a hard dependency of every limited endpoint: its outage is your outage |
| **Per-endpoint** | Cheap endpoints fail open, the expensive LLM endpoint fails closed | Both, where each matters | One more concept to explain; the policy lives in code per route |

Also considered and rejected: an in-memory fallback limiter per process —
it silently multiplies the limit by the number of workers and replicas,
which is the bug that motivated Redis in the first place.

## Consequences

- An outage of Redis never takes the API down, and costs at most the
  50 ms budget per limited request.
- During a Redis outage, spend protection falls to the LLM provider's own
  quotas and any budget caps on the API key — configure those regardless.
- Switching to fail-closed or per-endpoint is a change inside
  `app/ratelimit.py` (return a rejecting `Decision` in the `except`
  branch, keyed on scope) plus a new ADR superseding this one.
