"""Database engine and per-request sessions.

The app connects through PgBouncer in transaction-pooling mode: each
transaction borrows a real Postgres connection and returns it at COMMIT,
so hundreds of app connections share a handful of server ones. Two rules
follow: nothing may rely on session state (SET, advisory locks, LISTEN,
temp tables) outliving a transaction, and migrations bypass PgBouncer
(see migrations/env.py).
"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
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
        connect_args={
            "timeout": settings.db_connect_timeout_s,
            "command_timeout": settings.db_command_timeout_s,
            "server_settings": {"application_name": "triage-api"},
        },
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: returned ORM objects stay readable after
    # commit without a second round trip (and without implicit async I/O).
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session
