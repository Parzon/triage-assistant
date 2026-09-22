# ADR-0005: Database access — two roles, PgBouncer for the app, direct for migrations

**Status:** accepted
**Date:** 2026-09-22

## Context

The API is async (SQLAlchemy 2 + asyncpg) and runs several worker
processes per container, several containers per environment; each keeps
its own connection pool. Postgres caps connections (100 by default) and
each one costs a backend process. Schema changes need DDL rights; serving
requests does not.

While building this, the stack's PgBouncer turned out never to have worked
for real queries: it authenticated with md5 while Postgres 17 requires
SCRAM (`server login failed: wrong password type`). Nothing had queried
the database, and `pg_isready` does not authenticate, so it looked healthy.

## Decision

- **Two roles.** `POSTGRES_USER` owns the schema and is used only by
  migrations. `APP_DB_USER` (created by `infra/postgres/initdb/`) can
  read and write rows but cannot create, alter or drop anything; default
  privileges extend this to every future table.
- **The app connects through PgBouncer in transaction mode**
  (`AUTH_TYPE=scram-sha-256`). Per-process pools stay small
  (5 + 5 overflow); PgBouncer multiplexes them onto 20 server connections.
- **Timeouts live on the role** (`statement_timeout 10s`,
  `idle_in_transaction_session_timeout 30s`), because transaction pooling
  discards session-level `SET`. The client also has connect (5 s) and
  per-command (10 s) timeouts.
- **Migrations run as a one-shot `migrate` service**, directly against
  Postgres as the owner, before the API starts
  (`service_completed_successfully`). Never on API startup: several
  workers and replicas would race to migrate.
- `lock_timeout 5s` (`SET LOCAL` inside Alembic's transaction) so a
  migration waiting for a lock fails instead of queueing all traffic
  behind it.
- CI runs `alembic upgrade head && alembic check`: a model change without
  a migration fails the build.

## Alternatives considered

- **One role for everything.** Simpler env; a SQL injection or a bad code
  path can drop tables.
- **No PgBouncer, bigger app pools.** Fine for one small service;
  connections grow as workers × pool × replicas and exhaust Postgres
  first under scale-out. On AWS the managed equivalent is RDS Proxy.
- **Session pooling.** Keeps session state working but holds a server
  connection per client connection, which removes most of the benefit.
- **Migrations on app startup.** One fewer service; racy with multiple
  workers/replicas and couples deploys to schema changes.

## Consequences

- Nothing in the app may rely on session state across transactions
  (`SET`, advisory locks, `LISTEN/NOTIFY`, temp tables).
- The init script runs only on an empty data volume. Adding a role to an
  existing database is a migration or a runbook step, not an edit here.
- Connection budget to plan with: workers × (pool_size + max_overflow) ×
  replicas ≤ PgBouncer `MAX_CLIENT_CONN`, and PgBouncer
  `DEFAULT_POOL_SIZE` ≤ Postgres `max_connections` minus headroom.
