"""The limiter against real Redis. ALERTS_RATE_LIMIT=5 in compose.test.yaml."""

import time
from collections.abc import Callable

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from tests.integration.conftest import ClientFactory, started

ALERT = {"source": "prometheus", "severity": "info", "message": "m"}


async def test_limit_is_enforced_per_client(client_for: ClientFactory) -> None:
    async with client_for("192.0.2.1") as a, client_for("192.0.2.2") as b:
        statuses = [(await a.post("/alerts", json=ALERT)).status_code for _ in range(6)]
        assert statuses == [201] * 5 + [429]
        assert (await b.post("/alerts", json=ALERT)).status_code == 201


async def test_rejection_tells_the_client_when_to_retry(client_for: ClientFactory) -> None:
    async with client_for("192.0.2.3") as client:
        for _ in range(5):
            ok = await client.post("/alerts", json=ALERT)
        assert ok.headers["ratelimit-remaining"] == "0"
        rejected = await client.post("/alerts", json=ALERT)
    assert rejected.status_code == 429
    assert rejected.json()["error"]["code"] == "rate_limited"
    assert 0 < int(rejected.headers["retry-after"]) <= 60
    assert rejected.headers["ratelimit-limit"] == "5"


async def post_many(settings: Settings, n: int) -> list[int]:
    app = create_app(settings)
    async with (
        started(app),
        AsyncClient(
            transport=ASGITransport(app=app, client=("192.0.2.9", 1)), base_url="http://test"
        ) as client,
    ):
        return [(await client.post("/alerts", json=ALERT)).status_code for _ in range(n)]


async def test_fails_open_when_redis_is_down(
    with_redis: Callable[[str], Settings], caplog: pytest.LogCaptureFixture
) -> None:
    statuses = await post_many(with_redis("redis://127.0.0.1:1/0"), 8)
    assert statuses == [201] * 8  # past the limit of 5: nothing is limiting
    assert "failing open" in caplog.text


async def test_fails_open_fast_when_redis_hangs(
    with_redis: Callable[[str], Settings], blackhole_port: int
) -> None:
    start = time.perf_counter()
    statuses = await post_many(with_redis(f"redis://127.0.0.1:{blackhole_port}/0"), 3)
    elapsed = time.perf_counter() - start
    assert statuses == [201] * 3
    # 3 requests, each waiting at most the limiter budget (50ms) plus the insert.
    assert elapsed < 1.5
