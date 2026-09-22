"""Alembic runtime: how migrations connect and run.

- Connects with MIGRATIONS_DATABASE_URL: the schema owner, directly to
  Postgres. Not through PgBouncer (transaction pooling breaks session
  state and statements like CREATE INDEX CONCURRENTLY), and not as the
  app's role (which may read and write rows but not change the schema).
- Sets lock_timeout: a migration waiting for a lock queues every later
  query on that table behind it, turning "slow migration" into "outage".
  Failing after a few seconds and retrying later is the safe outcome.
"""

import asyncio
import os
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.logs import configure_logging
from app.models import Base

configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
target_metadata = Base.metadata


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
    async with engine.connect() as connection:
        await connection.run_sync(run_sync_migrations)
    await engine.dispose()


if context.is_offline_mode():
    # `alembic upgrade head --sql`: print the SQL instead of running it
    # (for review, or for a DBA who applies changes by hand).
    run_migrations(url=database_url(), literal_binds=True)
else:
    asyncio.run(run_migrations_online())
