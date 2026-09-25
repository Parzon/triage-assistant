"""Per-client rate limiting shared by every worker and replica.

Fixed-window counter in Redis (Valkey): one key per client per window,
INCR + EXPIRE in a single round trip. Simple and cheap; the known
trade-off is that a client can send up to 2x the limit across a window
boundary. Sliding-window or token-bucket algorithms remove that at the
cost of more Redis work per request (see ADR-0004).

Failure policy: fail OPEN. If Redis does not answer within `timeout_s`
the request is allowed, and the event is logged. Redis is then never a
hard dependency: an outage costs rate limiting, not the service. One
exception, per scope: the chat fails CLOSED in production
(chat_rate_limit_fail_closed, ADR-0023) - each request there is a model
call, and an unlimited chat is an unlimited bill.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.errors import ApiError
from app.metrics import ratelimit_decisions
from app.sessions import CurrentUser

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    reset_s: int
    # True when Redis was unreachable: the request was let through (fail
    # open), or refused (fail closed).
    degraded: bool = False

    def headers(self) -> dict[str, str]:
        headers = {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(self.remaining),
            "RateLimit-Reset": str(self.reset_s),
        }
        if not self.allowed:
            headers["Retry-After"] = str(self.reset_s)
        return headers


class RateLimiter:
    def __init__(self, redis: Redis, *, timeout_s: float) -> None:
        self._redis = redis
        self._timeout_s = timeout_s

    async def hit(
        self, scope: str, client: str, *, limit: int, window_s: int, fail_closed: bool = False
    ) -> Decision:
        now = time.time()
        window = int(now // window_s)
        reset_s = window_s - int(now % window_s)
        key = f"rl:{scope}:{client}:{window}"
        try:
            # Belt and braces with the client's own socket timeouts: also
            # bounds DNS lookups and connection-pool waits.
            async with asyncio.timeout(self._timeout_s * 2):
                async with self._redis.pipeline(transaction=True) as pipe:
                    pipe.incr(key)
                    pipe.expire(key, window_s, nx=True)
                    count, _ = await pipe.execute()
        except (RedisError, TimeoutError, OSError) as exc:
            how = "closed" if fail_closed else "open"
            log.warning(
                "rate limiter unavailable, failing %s",
                how,
                extra={"scope": scope, "error": type(exc).__name__},
            )
            ratelimit_decisions.labels(scope, f"fail_{how}").inc()
            if fail_closed:
                return Decision(False, limit, 0, reset_s, degraded=True)
            return Decision(True, limit, limit, reset_s, degraded=True)
        allowed = count <= limit
        ratelimit_decisions.labels(scope, "allowed" if allowed else "rejected").inc()
        return Decision(allowed, limit, max(0, limit - count), reset_s)


def client_ip(request: Request) -> str:
    """The client's address as the proxies report it (X-Forwarded-For, read
    by the proxy-headers middleware; the edge overwrites what clients send)."""
    return request.client.host if request.client else "unknown"


async def enforce(
    request: Request,
    scope: str,
    limit_setting: str,
    client: str,
    fail_closed_setting: str | None = None,
) -> Decision:
    """Count one request for `client`; 429 once over the limit named by
    `limit_setting`, 503 when the limiter is unavailable and the setting
    named by `fail_closed_setting` says to refuse. The decision's headers are
    stashed on the request state and written by RequestContextMiddleware, so
    they reach JSON and streaming responses alike."""
    settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.limiter
    decision = await limiter.hit(
        scope,
        client,
        limit=getattr(settings, limit_setting),
        window_s=settings.ratelimit_window_s,
        fail_closed=bool(fail_closed_setting and getattr(settings, fail_closed_setting)),
    )
    if not decision.allowed and decision.degraded:
        raise ApiError(
            503,
            "rate_limiter_unavailable",
            "the rate limiter is unavailable, so this is refused: retry shortly",
            {"Retry-After": "5"},
        )
    request.state.response_headers = decision.headers()
    if not decision.allowed:
        raise HTTPException(
            status_code=429, detail="rate limit exceeded", headers=decision.headers()
        )
    return decision


def rate_limit(
    scope: str, limit_setting: str, fail_closed_setting: str | None = None
) -> Callable[..., Awaitable[Decision]]:
    """Per signed-in user: a whole office can share one egress IP. Depends
    on the principal, so it also runs after authentication, whatever order
    a route declares its dependencies in."""

    async def dependency(request: Request, principal: CurrentUser) -> Decision:
        client = f"user:{principal.user_id}"
        return await enforce(request, scope, limit_setting, client, fail_closed_setting)

    return dependency


def rate_limit_by_ip(scope: str, limit_setting: str) -> Callable[..., Awaitable[Decision]]:
    """For requests made before anyone is signed in (the sign-in itself)."""

    async def dependency(request: Request) -> Decision:
        return await enforce(request, scope, limit_setting, f"ip:{client_ip(request)}")

    return dependency
