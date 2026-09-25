import asyncio
from collections.abc import Callable

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from tests.integration.conftest import started


async def get(settings: Settings, path: str) -> tuple[int, dict[str, object]]:
    app = create_app(settings)
    async with (
        started(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get(path)
        return response.status_code, response.json()


async def test_liveness_needs_no_dependencies(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_when_database_and_redis_answer(client: AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"


async def test_redis_outage_degrades_but_stays_ready(
    with_redis: Callable[[str], Settings],
) -> None:
    status, body = await get(with_redis("redis://127.0.0.1:1/0"), "/ready")
    assert status == 200
    assert body["checks"]["redis"] == "degraded"


async def test_identity_provider_outage_degrades_but_stays_ready(
    settings: Settings, blackhole_port: int
) -> None:
    """Signed-in users carry on without the provider: never a reason to take
    an instance out of rotation. Reported from the background check."""
    down = settings.model_copy(
        update={
            "oidc_discovery_url": f"http://127.0.0.1:{blackhole_port}/.well-known/x",
            "oidc_timeout_s": 0.2,
        }
    )
    app = create_app(down)
    async with (
        started(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        for _ in range(50):  # the watcher's first check times out after 0.2s
            response = await client.get("/ready")
            if response.json()["checks"]["identity_provider"] != "unknown":
                break
            await asyncio.sleep(0.05)
    assert response.status_code == 200
    assert response.json()["checks"]["identity_provider"] == "degraded"


async def test_database_outage_is_not_ready(with_database: Callable[[str], Settings]) -> None:
    status, body = await get(with_database("postgresql://x:y@127.0.0.1:1/triage"), "/ready")
    assert status == 503
    assert body["status"] == "not_ready"


async def test_production_logs_each_waived_check_at_startup(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # model_copy, not a new Settings: the checks themselves are unit-tested
    # (tests/unit/test_config.py); this is what the log says once they pass.
    waived = settings.model_copy(
        update={"app_env": "prod", "prod_checks_waived": frozenset({"mock_model"})}
    )
    async with started(create_app(waived)):
        pass
    (record,) = [r for r in caplog.records if r.getMessage() == "production checks waived"]
    assert record.levelname == "WARNING"
    assert record.checks == ["mock_model"]  # type: ignore[attr-defined]
