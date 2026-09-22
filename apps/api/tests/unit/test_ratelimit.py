import asyncio
import time
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.ratelimit import Decision, RateLimiter


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self._redis = redis
        self._key = ""

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def incr(self, key: str) -> None:
        self._key = key

    def expire(self, key: str, seconds: int, nx: bool = False) -> None:
        self._redis.ttls.setdefault(key, seconds)

    async def execute(self) -> list[Any]:
        if self._redis.delay_s:
            await asyncio.sleep(self._redis.delay_s)
        if self._redis.error:
            raise self._redis.error
        self._redis.counts[self._key] = self._redis.counts.get(self._key, 0) + 1
        return [self._redis.counts[self._key], True]


class FakeRedis:
    def __init__(self, *, delay_s: float = 0, error: Exception | None = None) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.delay_s = delay_s
        self.error = error

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)


def limiter(redis: FakeRedis, timeout_s: float = 0.05) -> RateLimiter:
    return RateLimiter(redis, timeout_s=timeout_s)  # type: ignore[arg-type]


async def test_allows_up_to_the_limit_then_rejects() -> None:
    rl = limiter(FakeRedis())
    decisions = [await rl.hit("alerts", "1.2.3.4", limit=3, window_s=60) for _ in range(4)]
    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert [d.remaining for d in decisions] == [2, 1, 0, 0]


async def test_clients_are_counted_separately() -> None:
    rl = limiter(FakeRedis())
    for _ in range(3):
        await rl.hit("alerts", "client-a", limit=3, window_s=60)
    assert (await rl.hit("alerts", "client-b", limit=3, window_s=60)).allowed


async def test_key_is_per_window_and_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(time, "time", lambda: 120.0)
    await limiter(redis).hit("alerts", "c", limit=1, window_s=60)
    monkeypatch.setattr(time, "time", lambda: 180.0)  # next window
    assert (await limiter(redis).hit("alerts", "c", limit=1, window_s=60)).allowed
    assert set(redis.counts) == {"rl:alerts:c:2", "rl:alerts:c:3"}
    assert redis.ttls == {"rl:alerts:c:2": 60, "rl:alerts:c:3": 60}


async def test_fails_open_when_redis_errors(caplog: pytest.LogCaptureFixture) -> None:
    decision = await limiter(FakeRedis(error=RedisConnectionError("down"))).hit(
        "alerts", "c", limit=1, window_s=60
    )
    assert decision.allowed
    assert decision.degraded
    assert "failing open" in caplog.text


async def test_fails_open_within_budget_when_redis_hangs() -> None:
    rl = limiter(FakeRedis(delay_s=10), timeout_s=0.02)
    start = time.perf_counter()
    decision = await rl.hit("alerts", "c", limit=1, window_s=60)
    assert decision.allowed
    assert decision.degraded
    assert time.perf_counter() - start < 0.5


def test_headers_only_carry_retry_after_when_rejected() -> None:
    allowed = Decision(True, limit=5, remaining=4, reset_s=30)
    rejected = Decision(False, limit=5, remaining=0, reset_s=30)
    assert "Retry-After" not in allowed.headers()
    assert rejected.headers()["Retry-After"] == "30"
    assert rejected.headers()["RateLimit-Remaining"] == "0"
