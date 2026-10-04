# ADR-0027: Drop database connections whose address vanished

**Status:** accepted
**Date:** 2026-10-04

## Context

Once, during the failure drills, the api's pool stayed at 40 of 40 connections
in use while the database was healthy: a fresh connection through PgBouncer
answered in 0.02 s, yet `/ready` said `database: unavailable` and every request
waited out the pool timeout and got a 503, until the api was restarted
(issue #99). Six attempts to reproduce it by repeating the faults that were
active failed.

The variable none of them changed was the api's address. Docker hands out the
next free address, not the old one, when a container rejoins a network: here
172.21.0.19, then 172.21.0.16. Every connection opened before the cut stays
`ESTABLISHED`, bound to an address the container no longer has. Data written to
it is never acknowledged, and Linux retransmits for about 15 minutes before
giving up. `pool_pre_ping` has no timeout of its own (deliberately, ADR-0010),
so each pooled connection hangs at its next checkout and keeps its slot.

Reproduced on demand: a full pool (2 workers x 20), the api disconnected and
reconnected on a new address, `/ready` polled once a second: 503 every time,
and connections in use climbing by one per poll, towards 40 of 40. On a
connection with `TCP_USER_TIMEOUT` set to 5 s, the same query failed after
5.3 s; on one without it, still waiting after 45 s.

## Decision

Set `TCP_USER_TIMEOUT` on every connection to PgBouncer, 10 s by default
(`DB_TCP_USER_TIMEOUT_S`), through asyncpg's `connection_class`. A connection
whose sent data goes unacknowledged that long is dropped by the kernel; the
pre-ping then fails as a disconnect, and the pool replaces the connection.

## Alternatives considered

- **A client-side query or ping timeout.** Cancelling an asyncpg call is what
  ADR-0010 measured leaking connections; still rejected.
- **TCP keepalives.** They probe only idle connections; once data is waiting for
  an acknowledgment, only the retransmission limit or `TCP_USER_TIMEOUT` applies.
- **`net.ipv4.tcp_retries2` for the whole container.** A kernel setting per
  network namespace: it would affect every connection, and not every platform
  lets a container set it.
- **Restarting the api when the pool stays full.** Treats the symptom, and
  drops every in-flight request with it.

ADR-0010 set TCP timeouts aside because a frozen process's kernel still
acknowledges, so they never fire for a frozen database. That remains true and
is why this option is safe: it ends only connections whose packets reach no
one, never a slow or frozen database. The freeze drills are unchanged by it.

## Consequences

- After an address change, a request that meets a stale connection waits up to
  10 s, then gets a fresh one, and succeeds: the pre-ping's failure marks the
  whole pool stale, and the pool reconnects. Each other stale connection is
  replaced at its next checkout, after SQLAlchemy's 2 s graceful close.
  Measured, one checkout at a time: 10.2 s, then 2.0 s each, then fresh.
- `make drills d=address-change` (a full pool, then a new address): before,
  `/ready` was still failing after 180 s, and the pool stayed full until a
  restart; after, ready again in 50 s, with probes one at a time. Concurrent
  traffic replaces stale connections in parallel. The other database drills
  are unchanged.
- The same holds for any network fault that silently drops packets between the
  api and PgBouncer: requests fail after 10 s instead of about 15 minutes.
- Linux only; elsewhere the option does not exist and nothing changes.
