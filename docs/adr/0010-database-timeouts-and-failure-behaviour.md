# ADR-0010: Database timeouts live on the server side; failures answer in seconds

**Status:** accepted — supersedes the client-side "per-command (10 s)" timeout in ADR-0005
**Date:** 2026-09-23

## Context

The failure drills (`make drills`, issue #21) stop, freeze and cut each
dependency of the production-shaped stack and record what a user sees.
Before this change, for the database path:

| Fault | What users got |
|---|---|
| Postgres stopped | reads hung for 36 s; chat answered **500** (unmapped error) in 20 ms; recovery took 15 s after Postgres was back |
| Postgres frozen (`docker pause`) | reads hung beyond 20 s; chat got nginx's HTML **504 after 120 s** |
| PgBouncer frozen | `/ready` did not answer within 10 s, even though its check had a 2 s timeout |
| Postgres frozen 20 s under steady load | the 13 requests caught mid-query got nginx 504 at 30 s and **never finished inside the api**: 13 of the 40 pooled connections stayed checked out forever, with Postgres healthy again |

The last row is a permanent leak: every database hiccup takes more pool
slots, until every request fails with a pool timeout and only a restart
helps. Its cause is in asyncpg (0.31.0, `protocol.pyx`). When a query's
`command_timeout` fires, or its task is cancelled, asyncpg sends the server
a cancel request. From then on, every operation on that connection first
awaits the server's acknowledgement, with no timeout. That includes the
rollback that returns the connection to the pool, and `close(timeout=...)`,
whose timeout covers only its last step. The acknowledgement future is
resolved only when a result arrives, never when the connection is lost.
So if the connection dies first (PgBouncer's `query_timeout`, a restart,
a partition), the wait never ends. SQLAlchemy's cleanup cannot break it
either: it force-closes only if the task is cancelled again, and
`asyncio.timeout()` cancels once.

## Decision

1. **No client-side query timeout** (asyncpg `command_timeout` removed).
   Query time is capped where the query runs: `statement_timeout` on the
   app's role (10 s) for slow queries, and PgBouncer's `query_timeout`
   (15 s, just above it) for a Postgres that cannot answer at all. Either
   one ends the query with an error or a closed connection, and neither
   leaves a cancellation pending.
2. **PgBouncer fails fast** (defaults in brackets): `query_wait_timeout` 5 s
   [120 s], `server_connect_timeout` 5 s [15 s], `server_login_retry` 2 s
   [15 s], `dns_nxdomain_ttl` 1 s [15 s]. The last one matters with Docker:
   a stopped container vanishes from DNS, and PgBouncer kept failing from
   its cached "no such host" for 15 s after Postgres was back.
3. **Errors are classified by SQLSTATE, not only by exception class.** A
   connection PgBouncer closes mid-query arrives as a generic `DBAPIError`
   (asyncpg's `ConnectionDoesNotExistError`, 08003). Class 08, 57P01–57P03
   and 53300, or an invalidated connection, mean 503 `database_unavailable`.
   Everything else stays a 500.
4. **`/ready` answers within its deadline, always.** A check that overruns
   is left running and reported as failed. It is *abandoned, not
   cancelled*: cancelling an asyncpg call takes the same cancel path as
   `command_timeout` (measured: one leaked connection per frozen-database
   drill until this was changed).
5. **nginx answers in the api's error format** when it has to answer for
   the api (`error_page 502 504 @api_error`, status code kept): a JSON body
   with `upstream_unavailable` and the request id, plus `X-Request-ID` and
   `Retry-After`. The api's own JSON errors pass through untouched.
6. **Leaks are visible.** `db_pool_connections_in_use` samples the pools'
   own accounting each second. It does not use checkout events, which fire
   only after the pre-ping succeeds: a gauge built on them showed 2 while
   10 requests were stuck. `AppDatabasePoolExhausted` alerts at 90% for
   2 minutes.

## Alternatives considered

- **Keep `command_timeout`, larger than PgBouncer's `query_timeout`.** It
  still fires when PgBouncer itself is frozen or partitioned, which is
  exactly when the cancel cannot be acknowledged. And it gives no latency
  benefit, because the request then hangs in cleanup instead of in the
  query.
- **A request deadline in the api** (answer 503 after N seconds and abandon
  the work). It helps in only one case: a PgBouncer that is frozen while
  still accepting TCP. That case is already bounded by nginx's 30 s, which
  now answers in JSON. The cost is real: the abandoned work continues after
  the client has been told it failed, so a timed-out `POST /alerts` may
  still commit and the client's retry creates a duplicate. It would need
  the remaining budget propagated into each transaction
  (`SET LOCAL statement_timeout`) and idempotency keys on writes. Revisit
  it if the drills ever show a wait that no server-side timeout ends.
- **Cancel the abandoned readiness check.** Measured: it leaks, see
  Decision 4.
- **TCP keepalives or `tcp_user_timeout`.** A frozen process's kernel still
  ACKs, so these never fire in the freeze case. asyncpg does not expose
  `tcp_user_timeout` anyway.

## Consequences

- Measured after the change: every new request during a database, PgBouncer
  or DNS fault gets a JSON 503 within 5.3 s. Requests caught mid-query by
  a frozen Postgres get a 503 within 15.4 s. Requests caught by a frozen
  PgBouncer wait for it and then succeed. No pool connection is held
  afterwards. Recovery after Postgres restarts: 1 s (was 15–17 s).
- The longest a request can wait on the database is no longer one
  setting. It is the sum along the path: pool wait (5 s) + PgBouncer
  queue (5 s) + query (10 s server / 15 s PgBouncer). nginx's 30 s stays
  above that sum.
- A PgBouncer that is frozen (not down) is the one fault with no bound
  inside the stack: in-flight requests wait for it, users get nginx's JSON
  504 at 30 s, and the work completes when it recovers.
- Upgrading asyncpg or SQLAlchemy: re-run `make drills d="db-freeze
  db-hang pgbouncer-freeze"` and check that the pool gauge returns to 0.
  That is the regression test for the leak.
