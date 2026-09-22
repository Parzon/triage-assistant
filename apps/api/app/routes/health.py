"""Liveness vs readiness - two different questions, two endpoints.

/health (liveness): "is this process alive and its event loop turning?"
No dependency checks: if the database is down, restarting the api does
not help, and a liveness probe that checks it would restart every replica
at once. `async def` on purpose: it runs on the event loop, so it fails
exactly when the loop is blocked, and never waits for a worker thread.

/ready (readiness): "should traffic be sent here right now?" Checks hard
dependencies. Postgres is one; Redis is not - the rate limiter fails open,
so a Redis outage is reported as "degraded" but keeps the instance ready.
Marking every instance unready over a soft dependency turns a partial
outage into a total one.
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
        _probe(_ping_database(request), timeout_s=2.0),
        _probe(state.redis.ping(), timeout_s=0.25),
    )
    body = {
        "status": "ready" if database else "not_ready",
        "checks": {
            "database": "ok" if database else "unavailable",
            "redis": "ok" if redis else "degraded",
        },
    }
    return JSONResponse(body, status_code=200 if database else 503)


async def _ping_database(request: Request) -> None:
    async with request.app.state.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _probe(check: Awaitable[object], *, timeout_s: float) -> bool:
    try:
        async with asyncio.timeout(timeout_s):
            await check
    except Exception:
        return False
    return True


# Scraped by Prometheus on the private network; nginx refuses /api/metrics.
@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return metrics_response()
