"""Database engine and per-request sessions.

The app connects through PgBouncer in transaction-pooling mode: each
transaction borrows a real Postgres connection and returns it at COMMIT,
so hundreds of app connections share a handful of server ones. Two rules
follow: nothing may rely on session state (SET, advisory locks, LISTEN,
temp tables) outliving a transaction, and migrations bypass PgBouncer
(see migrations/env.py).
"""

import asyncio
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import QueuePool

from app.config import Settings
from app.metrics import db_pool_in_use, db_pool_max


def create_engine(settings: Settings) -> AsyncEngine:
    engine = create_async_engine(
        settings.async_database_url,
        # Per worker process: total connections to PgBouncer =
        # workers x (pool_size + max_overflow) x replicas.
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Waiting longer than this for a pooled connection raises instead
        # of queueing requests behind a stuck database forever.
        pool_timeout=settings.db_pool_timeout_s,
        # Checks a pooled connection before use, so a PgBouncer or Postgres
        # restart costs one reconnect instead of one failed request.
        pool_pre_ping=True,
        # Query time is capped on the server side only: statement_timeout on
        # the app's role (10s; transaction pooling ignores session SETs) and
        # PgBouncer's query_timeout (15s) for a Postgres that cannot answer.
        # Deliberately no asyncpg command_timeout: when it fires, asyncpg
        # sends a cancel, and every later use of the connection - including
        # the rollback that returns it to the pool - waits, with no timeout,
        # for the server to acknowledge it. If the connection dies first
        # (Postgres frozen, PgBouncer's query_timeout), that wait never ends:
        # the request hangs and its connection leaks (make drills d=db-freeze).
        connect_args={
            "timeout": settings.db_connect_timeout_s,
            "server_settings": {"application_name": "triage-api"},
        },
    )
    return engine


async def watch_db_pool(engine: AsyncEngine, capacity: int, interval_s: float = 1.0) -> None:
    """Sample how many pooled connections are in use. Read from the pool's
    own accounting, not checkout/checkin events: a checkout is only reported
    after the pre-ping succeeds, so a request stuck in the pre-ping holds a
    connection the events never saw."""
    db_pool_max.set(capacity)
    pool = engine.pool
    if not isinstance(pool, QueuePool):  # e.g. NullPool in a one-off script
        return
    while True:
        db_pool_in_use.set(pool.checkedout())
        await asyncio.sleep(interval_s)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: returned ORM objects stay readable after
    # commit without a second round trip (and without implicit async I/O).
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session
