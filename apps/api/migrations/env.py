"""Alembic runtime: how migrations connect and run.

- Connects with MIGRATIONS_DATABASE_URL: the schema owner, directly to
  Postgres. Not through PgBouncer (transaction pooling breaks session
  state and statements like CREATE INDEX CONCURRENTLY), and not as the
  app's role (which may read and write rows but not change the schema).
- Sets lock_timeout: a migration waiting for a lock queues every later
  query on that table behind it, turning "slow migration" into "outage".
  Failing after a few seconds and retrying later is the safe outcome.
- Runs one at a time: on ECS every api task migrates in an init container,
  and two tasks that start together on a database that is behind would
  run the same DDL. One of them failed (measured: 3 runs of 3). An
  advisory lock makes the second wait, then find nothing left to do.
"""

import asyncio
import os
from typing import Any

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.logs import configure_logging
from app.models import Base

configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
target_metadata = Base.metadata

# The advisory lock's key: any number, the same in every migration run.
MIGRATION_LOCK = 7_420_001


def database_url() -> str:
    raw = os.environ["MIGRATIONS_DATABASE_URL"]
    url = make_url(raw).set(drivername="postgresql+asyncpg")
    return url.render_as_string(hide_password=False)


def run_migrations(**configure: Any) -> None:
    context.configure(target_metadata=target_metadata, **configure)
    with context.begin_transaction():
        # Inside Alembic's transaction, never on the connection before it:
        # any statement run first auto-begins a transaction on the
        # connection, Alembic's own becomes a no-op, and the whole migration
        # is rolled back on close - while the log still says it ran.
        context.execute("SET LOCAL lock_timeout = '5s'")
        context.run_migrations()


def run_sync_migrations(connection: Connection) -> None:
    run_migrations(connection=connection)


async def run_migrations_online() -> None:
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    async with engine.connect() as lock:
        await take_migration_lock(lock)
        async with engine.connect() as connection:
            await connection.run_sync(run_sync_migrations)
    await engine.dispose()


async def take_migration_lock(lock: AsyncConnection) -> None:
    """One migration run at a time, until `lock` closes.

    A session lock on a connection of its own, polled. Not a transaction lock:
    the migrations that build an index CONCURRENTLY commit midway, which
    releases it (measured: still 2 failed runs of 3). Not a blocking
    pg_advisory_lock either: a run blocked in it holds a snapshot, the other
    run's CREATE INDEX CONCURRENTLY waits for every older snapshot to end, and
    Postgres ends the two with "deadlock detected" (measured). Between polls
    the waiting run holds no snapshot. Alembic reads the current revision
    after this, so a run that waited sees the other's work.
    """
    lock = await lock.execution_options(isolation_level="AUTOCOMMIT")
    taken = text(f"SELECT pg_try_advisory_lock({MIGRATION_LOCK})")
    while True:
        if (await lock.execute(taken)).scalar():
            return
        await asyncio.sleep(1)


if context.is_offline_mode():
    # `alembic upgrade head --sql`: print the SQL instead of running it
    # (for review, or for a DBA who applies changes by hand).
    run_migrations(url=database_url(), literal_binds=True)
else:
    asyncio.run(run_migrations_online())
