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
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import Connection, Select, event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction
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


class ContextSession(Session):
    """A Session that tells Postgres who is asking, in every transaction.

    Values given to set_transaction_settings() (by the request's
    authentication: app/sessions.py) are applied with set_config(..., true)
    to the open transaction and as each later one begins. Row-level security
    policies read them with current_setting() (ADR-0013, ADR-0014).

    Transaction-local on purpose: through PgBouncer's transaction pooling
    the next transaction on this server connection may be another user's.
    A session-level SET would still be there for them.
    """


def _set_config(values: dict[str, str]) -> Select[Any]:
    return select(*(func.set_config(k, v, True) for k, v in values.items()))


@event.listens_for(ContextSession, "after_begin")
def _apply_transaction_settings(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    values: dict[str, str] | None = session.info.get("transaction_settings")
    if values:
        connection.execute(_set_config(values))


async def set_transaction_settings(session: AsyncSession, values: dict[str, str]) -> None:
    """Applied to the open transaction, if any, and to every later one."""
    session.info["transaction_settings"] = values
    if session.in_transaction():
        await session.execute(_set_config(values))


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: returned ORM objects stay readable after
    # commit without a second round trip (and without implicit async I/O).
    return async_sessionmaker(engine, expire_on_commit=False, sync_session_class=ContextSession)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


# One database session per request, closed as soon as the route returns -
# scope="function". The default scope closes it after the response is SENT,
# which for a streamed answer is a minute later: its connection (and any
# open transaction) would stay checked out the whole time.
DbSession = Annotated[AsyncSession, Depends(get_session, scope="function")]
