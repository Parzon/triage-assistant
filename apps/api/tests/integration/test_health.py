from collections.abc import Callable

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
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


async def test_redis_outage_degrades_but_stays_ready(
    with_redis: Callable[[str], Settings],
) -> None:
    status, body = await get(with_redis("redis://127.0.0.1:1/0"), "/ready")
    assert status == 200
    assert body["checks"] == {"database": "ok", "redis": "degraded"}


async def test_database_outage_is_not_ready(with_database: Callable[[str], Settings]) -> None:
    status, body = await get(with_database("postgresql://x:y@127.0.0.1:1/triage"), "/ready")
    assert status == 503
    assert body["status"] == "not_ready"
