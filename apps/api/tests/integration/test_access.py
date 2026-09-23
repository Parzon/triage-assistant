"""Who sees and does what (app/access.py): every rule, through the API.

The cast, as their identity provider would describe them:
  payments  responder in payments
  platform  admin in platform
  viewer    viewer in platform
  org       org admin
  newcomer  in no team at all
"""

import asyncio

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text

from tests.integration.conftest import MockLLM, SignIn
from tests.integration.test_chat import events_of

ALERT = {"source": "prometheus", "severity": "high", "message": "latency up"}


@pytest.fixture
async def cast(sign_in_as: SignIn) -> dict[str, AsyncClient]:
    return {
        "payments": await sign_in_as("team:payments:responder", email="pay@example.com"),
        "platform": await sign_in_as("team:platform:admin", email="plat@example.com"),
        "viewer": await sign_in_as("team:platform:viewer", email="view@example.com"),
        "org": await sign_in_as("org:admin", email="org@example.com"),
        "newcomer": await sign_in_as(email="new@example.com"),
    }


async def post(client: AsyncClient, team: str, message: str = "m") -> int:
    response = await client.post("/alerts", json={**ALERT, "team": team, "message": message})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def listed(client: AsyncClient, **params: str) -> list[str]:
    response = await client.get("/alerts", params=params)
    assert response.status_code == 200, response.text
    return [item["message"] for item in response.json()["items"]]


async def test_each_user_sees_only_their_teams(cast: dict[str, AsyncClient]) -> None:
    await post(cast["payments"], "payments", "pay-1")
    await post(cast["platform"], "platform", "plat-1")
    assert await listed(cast["payments"]) == ["pay-1"]
    assert await listed(cast["platform"]) == ["plat-1"]
    assert await listed(cast["viewer"]) == ["plat-1"]
    assert await listed(cast["org"]) == ["plat-1", "pay-1"]
    assert await listed(cast["newcomer"]) == []


async def test_other_teams_alerts_are_not_found_not_forbidden(
    cast: dict[str, AsyncClient],
) -> None:
    secret = await post(cast["platform"], "platform")
    # Same answer as for an id that does not exist: ids cannot be probed.
    for response in (
        await cast["payments"].get(f"/alerts/{secret}"),
        await cast["payments"].delete(f"/alerts/{secret}"),
        await cast["payments"].get("/alerts", params={"team": "platform"}),
        await cast["newcomer"].get(f"/alerts/{secret}"),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


async def test_roles_are_ranked(cast: dict[str, AsyncClient]) -> None:
    alert = await post(cast["platform"], "platform")  # admin can create
    # viewer: read yes, create no, delete no
    assert (await cast["viewer"].get(f"/alerts/{alert}")).status_code == 200
    create = await cast["viewer"].post("/alerts", json={**ALERT, "team": "platform"})
    assert create.status_code == 403
    assert create.json()["error"] == {
        "code": "forbidden",
        "message": "needs the responder role in this team",
        "request_id": create.headers["x-request-id"],
    }
    assert (await cast["viewer"].delete(f"/alerts/{alert}")).status_code == 403
    # admin: delete yes
    assert (await cast["platform"].delete(f"/alerts/{alert}")).status_code == 204
    assert (await cast["platform"].get(f"/alerts/{alert}")).status_code == 404


async def test_responders_cannot_delete(cast: dict[str, AsyncClient]) -> None:
    alert = await post(cast["payments"], "payments")
    assert (await cast["payments"].delete(f"/alerts/{alert}")).status_code == 403


async def test_org_admin_can_act_in_any_team(cast: dict[str, AsyncClient]) -> None:
    alert = await post(cast["org"], "payments", "by org")
    assert await listed(cast["org"], team="payments") == ["by org"]
    assert (await cast["org"].delete(f"/alerts/{alert}")).status_code == 204


async def test_creating_for_an_unknown_team_is_a_404(cast: dict[str, AsyncClient]) -> None:
    for who in ("payments", "newcomer"):
        response = await cast[who].post("/alerts", json={**ALERT, "team": "nonexistent"})
        assert response.status_code == 404


async def test_the_assistant_only_sees_the_askers_alerts(
    cast: dict[str, AsyncClient], mock_llm: MockLLM
) -> None:
    await post(cast["payments"], "payments")
    for n in range(3):
        await post(cast["platform"], "platform", f"plat {n}")
    for who, visible in (("payments", 1), ("viewer", 3), ("org", 4), ("newcomer", 0)):
        response = await cast[who].post("/chat/stream", json={"message": "what is on fire?"})
        meta = events_of(response.text)[0]
        assert meta == ("meta", {**meta[1], "alerts_in_context": visible}), who


async def test_me_lists_teams_and_roles(cast: dict[str, AsyncClient]) -> None:
    await post(cast["payments"], "payments")  # creates nothing new: teams come from sign-in
    me = (await cast["payments"].get("/me")).json()
    assert me["email"] == "pay@example.com"
    assert me["org_admin"] is False
    assert me["teams"] == [{"slug": "payments", "name": "payments", "role": "responder"}]
    org = (await cast["org"].get("/me")).json()
    assert org["org_admin"] is True
    assert {t["slug"] for t in org["teams"]} >= {"default", "payments", "platform"}
    assert {t["role"] for t in org["teams"]} == {"admin"}
    assert (await cast["newcomer"].get("/me")).json()["teams"] == []


async def test_team_members_for_team_admins_only(cast: dict[str, AsyncClient]) -> None:
    members = await cast["platform"].get("/teams/platform/members")
    assert members.status_code == 200
    assert {(m["email"], m["role"]) for m in members.json()} == {
        ("plat@example.com", "admin"),
        ("view@example.com", "viewer"),
    }
    assert (await cast["viewer"].get("/teams/platform/members")).status_code == 403
    assert (await cast["payments"].get("/teams/platform/members")).status_code == 404
    assert (await cast["org"].get("/teams/platform/members")).status_code == 200


async def test_sign_in_replaces_memberships(app: FastAPI, sign_in_as: SignIn) -> None:
    """The identity provider is the source of truth: a role removed there is
    gone at the next sign-in; a role changed there is changed."""
    before = await sign_in_as("team:payments:admin", "team:platform:viewer", email="m@example.com")
    assert len((await before.get("/me")).json()["teams"]) == 2
    after = await sign_in_as("team:payments:viewer", email="m@example.com")
    assert (await after.get("/me")).json()["teams"] == [
        {"slug": "payments", "name": "payments", "role": "viewer"}
    ]
    # Memberships are per user, not per session: the older session sees it too.
    assert (await before.get("/me")).json()["teams"] == (await after.get("/me")).json()["teams"]


async def test_concurrent_sign_ins_of_one_user_do_not_collide(sign_in_as: SignIn) -> None:
    # Two tabs finishing sign-in at once: the membership sync must not fail
    # on the other's rows (it upserts rather than delete-then-insert).
    clients = await asyncio.gather(
        *(sign_in_as("team:payments:responder", email="twice@example.com") for _ in range(5))
    )
    for client in clients:
        assert (await client.get("/me")).status_code == 200


async def test_the_database_is_told_who_is_asking(app: FastAPI) -> None:
    """The tenant settings are local to a transaction: through PgBouncer's
    transaction pooling, the next transaction on the same server connection
    may belong to another user, and must not inherit them."""
    from app.db import set_transaction_settings

    async with app.state.sessionmaker() as db:
        await set_transaction_settings(db, {"app.user_id": "42", "app.read_team_ids": "{1,2}"})
        inside = (
            await db.execute(
                text(
                    "SELECT current_setting('app.user_id', true),"
                    " current_setting('app.read_team_ids', true)"
                )
            )
        ).one()
        await db.commit()
    assert tuple(inside) == ("42", "{1,2}")
    for _ in range(5):  # whichever pooled connection comes back
        async with app.state.engine.connect() as conn:
            after = await conn.scalar(text("SELECT current_setting('app.user_id', true)"))
        assert after in (None, "")
