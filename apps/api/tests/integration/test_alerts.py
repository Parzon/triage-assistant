from collections.abc import Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.config import Settings
from app.main import create_app
from tests.integration.conftest import ClientFactory, started

ALERT = {"source": "prometheus", "severity": "critical", "message": "disk 95% full on db-1"}


async def create(client: AsyncClient, **overrides: str) -> dict[str, object]:
    response = await client.post("/alerts", json={**ALERT, **overrides})
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_create_returns_the_stored_alert(client: AsyncClient) -> None:
    alert = await create(client)
    assert alert["id"]
    assert alert["created_at"]
    assert {k: alert[k] for k in ALERT} == ALERT


@pytest.mark.parametrize(
    "body",
    [
        {**ALERT, "severity": "apocalyptic"},
        {**ALERT, "message": ""},
        {**ALERT, "message": "x" * 4001},
        {"source": "prometheus"},
    ],
)
async def test_invalid_input_is_a_422_with_details(
    client: AsyncClient, body: dict[str, str]
) -> None:
    response = await client.post("/alerts", json=body)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]
    assert error["request_id"] == response.headers["x-request-id"]


async def test_get_one_and_404(client: AsyncClient) -> None:
    alert = await create(client)
    assert (await client.get(f"/alerts/{alert['id']}")).json() == alert
    missing = await client.get("/alerts/999999999")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


async def test_list_is_newest_first_and_filters_by_severity(client_for: ClientFactory) -> None:
    async with client_for("203.0.113.10") as client:
        first = await create(client, severity="info")
        second = await create(client, severity="critical")
        body = (await client.get("/alerts")).json()
        assert [a["id"] for a in body["items"]] == [second["id"], first["id"]]
        critical = (await client.get("/alerts", params={"severity": "critical"})).json()
        assert [a["id"] for a in critical["items"]] == [second["id"]]


async def test_pagination_visits_every_alert_exactly_once(client_for: ClientFactory) -> None:
    created = []
    for n in range(7):  # more than one client's rate limit, so spread the source IPs
        async with client_for(f"203.0.113.{20 + n}") as c:
            created.append((await create(c, message=f"alert {n}"))["id"])
    async with client_for("203.0.113.99") as client:
        seen, cursor, pages = [], None, 0
        while True:
            params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
            page = (await client.get("/alerts", params=params)).json()
            seen += [a["id"] for a in page["items"]]
            pages += 1
            cursor = page["next_cursor"]
            if cursor is None:
                break
    assert pages == 3
    assert seen == sorted(created, reverse=True)


async def test_bad_cursor_is_a_400(client: AsyncClient) -> None:
    response = await client.get("/alerts", params={"cursor": "garbage"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


async def test_database_outage_is_a_503_not_a_500(with_database: Callable[[str], Settings]) -> None:
    app = create_app(with_database("postgresql://x:y@127.0.0.1:1/triage"))
    async with (
        started(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/alerts")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["error"]["code"] == "database_unavailable"


async def test_app_role_cannot_change_the_schema(app: FastAPI) -> None:
    async with app.state.engine.connect() as conn:
        with pytest.raises(ProgrammingError, match="must be owner"):
            await conn.execute(text("DROP TABLE alerts"))


async def test_trailing_slash_is_a_404_not_a_redirect(client: AsyncClient) -> None:
    # A redirect here would drop the /api prefix the proxy stripped.
    response = await client.get("/alerts/")
    assert response.status_code == 404
