from collections.abc import Callable

import httpx
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from tests.integration.conftest import SignIn, started

TOKEN = "test-webhook-token-0123456789"


def payload(*alerts: dict[str, object]) -> dict[str, object]:
    return {"version": "4", "status": "firing", "alerts": list(alerts)}


def am_alert(name: str = "RedisDown", status: str = "firing", **labels: str) -> dict[str, object]:
    return {
        "status": status,
        "labels": {"alertname": name, "severity": "warning", **labels},
        "annotations": {"summary": f"{name} summary"},
        "startsAt": "2026-09-22T18:00:00Z",
        "fingerprint": f"fp-{name}",
    }


async def post(settings: Settings, body: dict[str, object], auth: str | None) -> httpx.Response:
    app = create_app(settings)
    headers = {"Authorization": auth} if auth else {}
    async with (
        started(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        await client.get("/health")  # warm up; also proves the app is running
        return await client.post("/alerts/alertmanager", json=body, headers=headers)


def with_token(settings: Settings, token: str | None = TOKEN) -> Settings:
    value = SecretStr(token) if token is not None else None
    return settings.model_copy(update={"alertmanager_webhook_token": value})


async def test_firing_alerts_are_stored_once(settings: Settings, app: object) -> None:
    body = payload(am_alert("RedisDown"), am_alert("PostgresDown", severity="critical"))
    first = await post(with_token(settings), body, f"Bearer {TOKEN}")
    again = await post(with_token(settings), body, f"Bearer {TOKEN}")  # Alertmanager re-send
    assert first.json() == {"received": 2, "created": 2}
    assert again.json() == {"received": 2, "created": 0}


async def test_alerts_keep_severity_and_summary(settings: Settings, sign_in_as: SignIn) -> None:
    await post(
        with_token(settings),
        payload(am_alert("DiskAlmostFull", severity="critical")),
        f"Bearer {TOKEN}",
    )
    await post(
        with_token(settings), payload(am_alert("Odd", severity="page-everyone")), f"Bearer {TOKEN}"
    )
    org_admin = await sign_in_as("org:admin")
    items = (await org_admin.get("/alerts")).json()["items"]
    by_source = {item["source"]: item for item in items}
    assert by_source["alertmanager/DiskAlmostFull"]["severity"] == "critical"
    assert by_source["alertmanager/DiskAlmostFull"]["message"] == "DiskAlmostFull summary"
    assert by_source["alertmanager/Odd"]["severity"] == "info"  # unknown label -> info


async def test_alerts_are_routed_by_their_team_label(
    settings: Settings, sign_in_as: SignIn
) -> None:
    payments = await sign_in_as("team:payments:viewer")  # the team exists once someone signs in
    await post(
        with_token(settings),
        payload(
            am_alert("PaymentsDown", team="payments"),
            am_alert("Orphan", team="no-such-team"),
            am_alert("Unlabelled"),
        ),
        f"Bearer {TOKEN}",
    )
    org_admin = await sign_in_as("org:admin")
    teams = {i["source"]: i["team"] for i in (await org_admin.get("/alerts")).json()["items"]}
    assert teams == {
        "alertmanager/PaymentsDown": "payments",
        "alertmanager/Orphan": "default",  # unknown team: logged, kept, not lost
        "alertmanager/Unlabelled": "default",
    }
    assert [i["source"] for i in (await payments.get("/alerts")).json()["items"]] == [
        "alertmanager/PaymentsDown"
    ]


async def test_resolved_alerts_are_not_stored(settings: Settings, app: object) -> None:
    response = await post(
        with_token(settings), payload(am_alert(status="resolved")), f"Bearer {TOKEN}"
    )
    assert response.json() == {"received": 1, "created": 0}


async def test_wrong_or_missing_token_is_rejected(settings: Settings) -> None:
    body = payload(am_alert())
    assert (await post(with_token(settings), body, "Bearer nope")).status_code == 401
    assert (await post(with_token(settings), body, None)).status_code == 401


async def test_disabled_without_a_token(
    settings: Settings, with_redis: Callable[[str], Settings]
) -> None:
    for disabled in (with_token(settings, None), with_token(settings, "")):
        response = await post(disabled, payload(am_alert()), "Bearer ")
        assert response.status_code == 404


def test_empty_env_value_means_disabled(settings: Settings) -> None:
    rebuilt = Settings.model_validate({**settings.model_dump(), "alertmanager_webhook_token": ""})
    assert rebuilt.alertmanager_webhook_token is None
