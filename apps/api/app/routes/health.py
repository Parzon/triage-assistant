"""Liveness vs readiness - two different questions, two endpoints.

/health (liveness): "is this process alive and its event loop turning?"
No dependency checks: if the database is down, restarting the api does
not help, and a liveness probe that checks it would restart every replica
at once. `async def` on purpose: it runs on the event loop, so it fails
exactly when the loop is blocked, and never waits for a worker thread.

/ready (readiness): "should traffic be sent here right now?" Checks hard
dependencies. Postgres is one; Redis is not - the rate limiter fails open,
so a Redis outage is reported as "degraded" but keeps the instance ready.
Nor is the identity provider: signed-in users carry on without it. Its
state comes from the background check (app/oidc.py), never a call per
probe. Marking every instance unready over a soft dependency turns a
partial outage into a total one.
"""

import asyncio
from collections.abc import Awaitable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.metrics import metrics_response

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    state = request.app.state
    database, redis = await asyncio.gather(
        probe(_ping_database(request), timeout_s=2.0),
        probe(state.redis.ping(), timeout_s=0.25),
    )
    idp = state.oidc.reachable
    body = {
        "status": "ready" if database else "not_ready",
        "checks": {
            "database": "ok" if database else "unavailable",
            "redis": "ok" if redis else "degraded",
            "identity_provider": "unknown" if idp is None else "ok" if idp else "degraded",
        },
    }
    return JSONResponse(body, status_code=200 if database else 503)


async def _ping_database(request: Request) -> None:
    async with request.app.state.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


# Checks that overran their deadline, still running. Referenced here so they
# are not garbage-collected mid-flight.
_abandoned: set[asyncio.Future[object]] = set()


async def probe(check: Awaitable[object], *, timeout_s: float) -> bool:
    """Did `check` succeed within timeout_s? Answers on time, always.

    A check that overruns is abandoned, not cancelled. Cancelling an asyncpg
    call makes it send the server a cancel request, and every later use of
    that connection - including the cleanup that returns it to the pool -
    waits, with no timeout, for the server to acknowledge it. Against a
    frozen database whose connection then dies (PgBouncer's query_timeout),
    that wait never ends and the connection leaks. Left alone, the check
    ends when the database answers or its connection is closed.
    (asyncio.timeout() would cancel it, and then wait for that cleanup.)
    """
    task = asyncio.ensure_future(check)
    done, _ = await asyncio.wait({task}, timeout=timeout_s)
    if task in done:
        return not task.cancelled() and task.exception() is None
    _abandoned.add(task)
    task.add_done_callback(_forget)
    return False


def _forget(task: asyncio.Future[object]) -> None:
    _abandoned.discard(task)
    if not task.cancelled():
        task.exception()  # retrieved: no "exception was never retrieved" noise


# Scraped by Prometheus on the private network; nginx refuses /api/metrics.
@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return metrics_response()
