"""The limiter against real Redis. ALERTS_RATE_LIMIT=5 in compose.test.yaml.
Limits are per signed-in user; test_sessions.py shows two users behind one
address keep separate budgets."""

import time
from collections.abc import Callable

import pytest

from app.config import Settings
from app.main import create_app
from tests.integration.conftest import MockLLM, SignIn, signed_in, started

ALERT = {"team": "default", "source": "prometheus", "severity": "info", "message": "m"}


async def test_limit_is_enforced_per_user(sign_in_as: SignIn) -> None:
    a = await sign_in_as("team:default:responder", email="a@example.com")
    b = await sign_in_as("team:default:responder", email="b@example.com")
    statuses = [(await a.post("/alerts", json=ALERT)).status_code for _ in range(6)]
    assert statuses == [201] * 5 + [429]
    assert (await b.post("/alerts", json=ALERT)).status_code == 201


async def test_rejection_tells_the_client_when_to_retry(sign_in_as: SignIn) -> None:
    client = await sign_in_as("team:default:responder")
    for _ in range(5):
        ok = await client.post("/alerts", json=ALERT)
    assert ok.headers["ratelimit-remaining"] == "0"
    rejected = await client.post("/alerts", json=ALERT)
    assert rejected.status_code == 429
    assert rejected.json()["error"]["code"] == "rate_limited"
    assert 0 < int(rejected.headers["retry-after"]) <= 60
    assert rejected.headers["ratelimit-limit"] == "5"


async def test_sign_in_is_limited_per_address(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"auth_rate_limit": 3}))
    async with started(app):
        await app.state.redis.flushdb()
        async with await signed_in(app, ip="192.0.2.77") as client:
            statuses = [(await client.get("/auth/callback")).status_code for _ in range(4)]
    assert statuses == [302, 302, 302, 429]


async def post_many(settings: Settings, n: int) -> list[int]:
    app = create_app(settings)
    async with started(app), await signed_in(app, "team:default:responder") as client:
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
    # 3 requests, each waiting at most the limiter budget plus the insert.
    assert elapsed < 1.5


@pytest.mark.parametrize(("fail_closed", "status"), [(True, 503), (False, 200)])
async def test_the_chat_fails_closed_when_its_setting_says_so(
    with_redis: Callable[[str], Settings],
    mock_llm: MockLLM,
    caplog: pytest.LogCaptureFixture,
    fail_closed: bool,
    status: int,
) -> None:
    """Production's default (ADR-0023): with Redis down, a question is
    refused before any model call, while every other route still fails
    open."""
    settings = with_redis("redis://127.0.0.1:1/0").model_copy(
        update={"chat_rate_limit_fail_closed": fail_closed}
    )
    app = create_app(settings)
    async with started(app), await signed_in(app, "team:default:responder") as client:
        chat = await client.post("/chat/stream", json={"message": "what is on fire?"})
        alert = await client.post("/alerts", json=ALERT)
    assert chat.status_code == status
    assert alert.status_code == 201
    if fail_closed:
        assert chat.json()["error"]["code"] == "rate_limiter_unavailable"
        assert chat.headers["retry-after"] == "5"
        assert "ratelimit-remaining" not in chat.headers
        assert (await mock_llm.stats())["requests"] == 0
        assert "failing closed" in caplog.text
