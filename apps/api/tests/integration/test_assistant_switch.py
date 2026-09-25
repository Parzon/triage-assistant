"""The assistant's off switch (app/switch.py, ADR-0024), through the API:
who may switch it, what an asker sees, what keeps working, and that no
model is called while it is off."""

import hashlib
import json

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.cli import main as cli
from app.config import Settings
from app.db import set_transaction_settings
from app.main import create_app
from tests.integration.conftest import ClientFactory, MockLLM, SignIn, signed_in, started
from tests.integration.test_audit import trail, user_id
from tests.integration.test_chat import events_of

OFF = {"enabled": False, "reason": "the provider is leaking prompts"}


async def ask(client: AsyncClient) -> tuple[int, dict[str, object]]:
    response = await client.post("/chat/stream", json={"message": "what is on fire?"})
    body = response.json() if response.headers["content-type"] == "application/json" else {}
    return response.status_code, body


async def test_everyone_signed_in_reads_it_and_it_starts_on(
    sign_in_as: SignIn, anonymous: ClientFactory
) -> None:
    viewer = await sign_in_as("team:default:viewer")
    status = (await viewer.get("/assistant")).json()
    assert (status["enabled"], status["reason"]) == (True, None)
    assert (await anonymous().get("/assistant")).status_code == 401


async def test_only_org_admins_switch_it(sign_in_as: SignIn, anonymous: ClientFactory) -> None:
    team_admin = await sign_in_as("team:default:admin")
    assert (await team_admin.put("/assistant", json=OFF)).status_code == 403
    assert (await anonymous().put("/assistant", json=OFF)).status_code == 401
    org = await sign_in_as("org:admin")
    cross_site = await org.put("/assistant", json=OFF, headers={"Origin": "https://evil.example"})
    assert cross_site.status_code == 403
    assert cross_site.json()["error"]["code"] == "csrf_failed"
    assert (await team_admin.get("/assistant")).json()["enabled"] is True
    switched = await org.put("/assistant", json=OFF)
    assert switched.status_code == 200
    assert (switched.json()["enabled"], switched.json()["reason"]) == (False, OFF["reason"])


@pytest.mark.parametrize(
    "body",
    [{"enabled": False}, {"enabled": False, "reason": ""}, {"enabled": False, "reason": "x" * 301}],
)
async def test_switching_off_needs_a_reason(sign_in_as: SignIn, body: dict[str, object]) -> None:
    org = await sign_in_as("org:admin")
    assert (await org.put("/assistant", json=body)).status_code == 422
    assert (await org.get("/assistant")).json()["enabled"] is True


async def test_while_off_questions_are_refused_before_any_model_call(
    app: FastAPI, sign_in_as: SignIn, mock_llm: MockLLM
) -> None:
    org = await sign_in_as("org:admin")
    await org.put("/assistant", json=OFF)
    viewer = await sign_in_as("team:default:responder")

    status, body = await ask(viewer)
    assert status == 503
    error = body["error"]
    assert isinstance(error, dict)
    assert (error["code"], error["reason"]) == ("assistant_disabled", OFF["reason"])
    assert error["since"]
    stats = await mock_llm.stats()
    assert (stats["requests"], stats["embedding_requests"]) == (0, 0)
    # What never reached a model is not recorded as asked.
    assert await trail(app, "chat.asked", actor_user_id=await user_id(viewer)) == []

    # Everything else keeps working.
    alert = {"team": "default", "source": "p", "severity": "info", "message": "m"}
    assert (await viewer.post("/alerts", json=alert)).status_code == 201
    assert (await viewer.get("/alerts")).status_code == 200
    assert (await viewer.get("/runbooks")).status_code == 200


async def test_back_on_it_answers_again(sign_in_as: SignIn, mock_llm: MockLLM) -> None:
    org = await sign_in_as("org:admin")
    await org.put("/assistant", json=OFF)
    back = await org.put("/assistant", json={"enabled": True})
    assert (back.json()["enabled"], back.json()["reason"]) == (True, None)
    response = await (await sign_in_as("team:default:viewer")).post(
        "/chat/stream", json={"message": "what is on fire?"}
    )
    assert events_of(response.text)[-1][0] == "done"


async def test_each_switch_is_audited_with_the_reason_hashed_never_written(
    app: FastAPI, sign_in_as: SignIn
) -> None:
    org = await sign_in_as("org:admin", email="oncall-lead@example.com")
    await org.put("/assistant", json=OFF)
    await org.put("/assistant", json={"enabled": True})
    (off,) = await trail(app, "assistant.disabled", actor_user_id=await user_id(org))
    (on,) = await trail(app, "assistant.enabled", actor_user_id=await user_id(org))
    assert off.detail == {"reason_sha256": hashlib.sha256(OFF["reason"].encode()).hexdigest()}
    assert OFF["reason"] not in json.dumps(off.detail)
    assert on.detail == {"reason_sha256": None}


async def test_the_agent_is_switched_off_too(settings: Settings, mock_llm: MockLLM) -> None:
    app = create_app(settings.model_copy(update={"chat_mode": "agent"}))
    async with started(app), await signed_in(app, "org:admin") as org:
        await org.put("/assistant", json=OFF)
        status, _ = await ask(org)
        await org.put("/assistant", json={"enabled": True})
    assert status == 503
    assert (await mock_llm.stats())["requests"] == 0


@pytest.mark.parametrize(
    ("statement", "refused"),
    [
        ("INSERT INTO assistant_switch (id) VALUES (2)", "permission denied"),
        ("DELETE FROM assistant_switch", "permission denied"),
        # Another user's name on the switch: row-level security's check.
        ("UPDATE assistant_switch SET enabled = false, changed_by = 1", "row-level security"),
    ],
)
async def test_the_app_role_cannot_add_remove_or_misattribute_it(
    app: FastAPI, statement: str, refused: str
) -> None:
    """Straight to Postgres as the app's role, even claiming org admin."""
    async with app.state.sessionmaker() as db:
        await set_transaction_settings(db, {"app.org_admin": "on", "app.user_id": "7"})
        with pytest.raises(DBAPIError, match=refused):
            await db.execute(text(statement))


async def test_only_an_org_admin_transaction_can_change_it(app: FastAPI) -> None:
    async with app.state.sessionmaker() as db:
        await set_transaction_settings(db, {"app.user_id": "7", "app.admin_team_ids": "{1}"})
        changed = await db.execute(text("UPDATE assistant_switch SET enabled = false"))
        await db.commit()
    assert changed.rowcount == 0  # type: ignore[attr-defined]


def test_an_operator_switches_it_without_signing_in(capsys: pytest.CaptureFixture[str]) -> None:
    """`make assistant off=...`: no identity provider, no org admin session."""
    try:
        cli(["assistant", "--off", "--reason", "provider incident"])
        assert "the assistant is off: provider incident" in capsys.readouterr().out
        with pytest.raises(SystemExit, match="needs a reason"):
            cli(["assistant", "--off"])
    finally:
        cli(["assistant", "--on"])
    assert "the assistant is on" in capsys.readouterr().out
