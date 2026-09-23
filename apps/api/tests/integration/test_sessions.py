"""Sessions and the checks every authenticated request passes
(app/sessions.py): no identity provider involved - test_auth_flow.py
covers signing in."""

import asyncio

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text

from app.cli import main, mint_session
from app.sessions import TOUCH_INTERVAL_S
from tests.integration.conftest import ClientFactory, SignIn

ALERT = {"team": "default", "source": "s", "severity": "info", "message": "m"}


async def test_no_session_is_a_401(anonymous: ClientFactory) -> None:
    async with anonymous() as client:
        for response in (await client.get("/alerts"), await client.get("/me")):
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "unauthenticated"


async def test_unknown_session_is_a_401(anonymous: ClientFactory, app: FastAPI) -> None:
    async with anonymous() as client:
        client.cookies.set("__Host-triage_session", "forged-or-long-gone")
        assert (await client.get("/alerts")).status_code == 401


async def test_expired_and_idle_sessions_are_refused(app: FastAPI, sign_in_as: SignIn) -> None:
    expired = await sign_in_as("team:default:viewer", email="expired@example.com")
    idle = await sign_in_as("team:default:viewer", email="idle@example.com")
    async with app.state.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE sessions SET expires_at = now() - interval '1 second' FROM users "
                "WHERE users.id = sessions.user_id AND users.email = 'expired@example.com'"
            )
        )
        await conn.execute(
            text(
                "UPDATE sessions SET last_seen_at = now() - interval '3 hours' FROM users "
                "WHERE users.id = sessions.user_id AND users.email = 'idle@example.com'"
            )
        )
    for client in (expired, idle):
        response = await client.get("/alerts")
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "session expired, sign in again"


async def test_activity_keeps_a_session_alive_without_a_write_per_request(
    app: FastAPI, sign_in_as: SignIn
) -> None:
    client = await sign_in_as("team:default:viewer", email="active@example.com")

    async def last_seen_age_s() -> float:
        async with app.state.engine.connect() as conn:
            age = await conn.scalar(
                text(
                    "SELECT extract(epoch FROM now() - last_seen_at) FROM sessions "
                    "JOIN users ON users.id = sessions.user_id WHERE email = 'active@example.com'"
                )
            )
        return float(age)

    async def seen_ago(seconds: int) -> None:
        async with app.state.engine.begin() as conn:
            await conn.execute(
                text("UPDATE sessions SET last_seen_at = now() - make_interval(secs => :s)"),
                {"s": seconds},
            )

    await seen_ago(TOUCH_INTERVAL_S - 60)
    await client.get("/alerts")
    assert await last_seen_age_s() > TOUCH_INTERVAL_S - 120  # recent enough: not rewritten
    await seen_ago(TOUCH_INTERVAL_S + 60)
    await client.get("/alerts")
    assert await last_seen_age_s() < 5  # stale: rewritten by this request


async def test_state_changes_need_this_sites_origin(client: AsyncClient) -> None:
    """A page on another site can make the browser POST here with the
    session cookie attached; it cannot fake the Origin header."""
    for origin in (None, "https://evil.example", "null", "https://test.evil.example"):
        headers = {"Origin": origin} if origin else {}
        client.headers.pop("Origin", None)
        response = await client.post("/alerts", json=ALERT, headers=headers)
        assert response.status_code == 403, origin
        assert response.json()["error"]["code"] == "csrf_failed"
    # Reads need no Origin (browsers do not always send one on GET).
    assert (await client.get("/alerts")).status_code == 200


async def test_logout_ends_the_session(client: AsyncClient) -> None:
    response = await client.post("/auth/logout")
    assert response.status_code == 200
    assert response.json()["logout_url"]  # the IdP's, or "/"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith('__Host-triage_session=""') or "Max-Age=0" in cookie
    assert "Secure" in cookie
    assert (await client.get("/alerts")).status_code == 401


async def test_logout_needs_this_sites_origin(client: AsyncClient) -> None:
    response = await client.post("/auth/logout", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert (await client.get("/me")).status_code == 200


async def test_rate_limits_are_per_user_not_per_address(sign_in_as: SignIn) -> None:
    # Two people behind one office NAT: separate budgets (ALERTS_RATE_LIMIT=5).
    alice = await sign_in_as("team:default:responder", email="a@example.com", ip="192.0.2.50")
    bob = await sign_in_as("team:default:responder", email="b@example.com", ip="192.0.2.50")
    statuses = [(await alice.post("/alerts", json=ALERT)).status_code for _ in range(6)]
    assert statuses == [201] * 5 + [429]
    assert (await bob.post("/alerts", json=ALERT)).status_code == 201


async def test_cli_minted_sessions_work_like_signed_in_ones(
    app: FastAPI, anonymous: ClientFactory
) -> None:
    cookie = await mint_session("script@example.com", ["team:default:responder"], hours=0.1)
    name, token = cookie.split("=", 1)
    assert name == "__Host-triage_session"
    async with anonymous() as client:
        client.cookies.set(name, token)
        assert (await client.post("/alerts", json=ALERT)).status_code == 201
        me = (await client.get("/me")).json()
    assert me["teams"] == [{"slug": "default", "name": "Default", "role": "responder"}]


async def test_revoke_ends_every_session_of_a_user(
    app: FastAPI, sign_in_as: SignIn, capsys: pytest.CaptureFixture[str]
) -> None:
    laptop = await sign_in_as("team:default:viewer", email="leaver@example.com")
    phone = await sign_in_as("team:default:viewer", email="leaver@example.com")
    other = await sign_in_as("team:default:viewer", email="stays@example.com")
    await asyncio.to_thread(main, ["revoke", "--email", "leaver@example.com"])
    assert "ended 2 session(s) of leaver@example.com" in capsys.readouterr().err
    for client in (laptop, phone):
        assert (await client.get("/me")).status_code == 401
    assert (await other.get("/me")).status_code == 200


async def test_session_command_prints_a_usable_cookie(
    app: FastAPI, anonymous: ClientFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    await asyncio.to_thread(
        main, ["session", "--email", "k6@example.com", "--group", "team:default:viewer"]
    )
    name, token = capsys.readouterr().out.strip().split("=", 1)
    async with anonymous() as client:
        client.cookies.set(name, token)
        assert (await client.get("/me")).json()["email"] == "k6@example.com"
