"""Per-client rate limiting shared by every worker and replica.

Fixed-window counter in Redis (Valkey): one key per client per window,
INCR + EXPIRE in a single round trip. Simple and cheap; the known
trade-off is that a client can send up to 2x the limit across a window
boundary. Sliding-window or token-bucket algorithms remove that at the
cost of more Redis work per request (see ADR-0004).

Failure policy: fail OPEN. If Redis does not answer within `timeout_s`
the request is allowed, and the event is logged. Redis is then never a
hard dependency: an outage costs rate limiting, not the service.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    reset_s: int
    # True when Redis was unreachable and the request was let through.
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

    async def hit(self, scope: str, client: str, *, limit: int, window_s: int) -> Decision:
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
            log.warning(
                "rate limiter unavailable, failing open",
                extra={"scope": scope, "error": type(exc).__name__},
            )
            return Decision(True, limit, limit, reset_s, degraded=True)
        return Decision(count <= limit, limit, max(0, limit - count), reset_s)


def client_identity(request: Request) -> str:
    """Who is being limited. The client IP as rewritten by the proxy-headers
    middleware from the X-Forwarded-For that nginx sets. Behind SSO, key on
    the authenticated user instead: a whole office can share one egress IP.
    """
    return request.client.host if request.client else "unknown"


def rate_limit(scope: str, limit_setting: str) -> Callable[[Request], Awaitable[Decision]]:
    """FastAPI dependency enforcing the limit named by `limit_setting`.

    The decision's headers are stashed on the request state and written by
    RequestContextMiddleware, so they reach JSON and streaming responses
    alike.
    """

    async def dependency(request: Request) -> Decision:
        settings = request.app.state.settings
        limiter: RateLimiter = request.app.state.limiter
        decision = await limiter.hit(
            scope,
            client_identity(request),
            limit=getattr(settings, limit_setting),
            window_s=settings.ratelimit_window_s,
        )
        request.state.response_headers = decision.headers()
        if not decision.allowed:
            raise HTTPException(
                status_code=429, detail="rate limit exceeded", headers=decision.headers()
            )
        return decision

    return dependency
