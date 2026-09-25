"""Personal data (app/privacy.py, docs/privacy.md): retention by age, a
person's export, and their erasure - against the real database, as the
operator commands run them."""

import json

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text

from app import privacy
from app.cli import main as cli
from app.config import Settings
from app.db import set_transaction_settings
from app.models import Alert, AuditEvent, Membership, User, UserSession
from tests.integration.conftest import SignIn
from tests.integration.test_audit import trail, user_id

ALERT = {"team": "payments", "source": "prom", "severity": "info", "message": "disk"}


async def as_org_admin(app: FastAPI, sql: str) -> None:
    async with app.state.sessionmaker() as db:
        await set_transaction_settings(db, {"app.org_admin": "on"})
        await db.execute(text(sql))
        await db.commit()


async def count(app: FastAPI, model: type[object]) -> int:
    async with app.state.sessionmaker() as db:
        await set_transaction_settings(db, {"app.org_admin": "on"})
        return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def run_retention(app: FastAPI, settings: Settings, *, apply: bool) -> privacy.Retained:
    async with app.state.sessionmaker() as db:
        await set_transaction_settings(db, {"app.org_admin": "on"})
        return await privacy.retention(db, settings, apply=apply)


async def test_retention_deletes_only_what_is_past_it_and_a_dry_run_nothing(
    app: FastAPI, settings: Settings, sign_in_as: SignIn
) -> None:
    active = await sign_in_as("team:payments:responder", email="active@example.com")
    await active.post("/alerts", json=ALERT)
    await sign_in_as("team:payments:viewer", email="gone@example.com")
    await as_org_admin(
        app,
        "UPDATE users SET last_login_at = now() - interval '400 days' "
        "WHERE email = 'gone@example.com'",
    )
    await as_org_admin(
        app,
        "UPDATE sessions SET expires_at = now() - interval '1 hour' "
        "WHERE user_id = (SELECT id FROM users WHERE email = 'active@example.com')",
    )
    await as_org_admin(
        app,
        "INSERT INTO alerts (team_id, source, severity, message, created_at) "
        "SELECT id, 'prom', 'info', 'old', now() - interval '400 days' "
        "FROM teams WHERE slug = 'payments'",
    )

    dry = await run_retention(app, settings, apply=False)
    assert dry == privacy.Retained(
        expired_sessions=1, expired_sign_ins=0, inactive_users=1, old_alerts=1
    )
    assert await count(app, User) == 2  # nothing was deleted
    assert await count(app, Alert) == 2

    assert await run_retention(app, settings, apply=True) == dry
    async with app.state.sessionmaker() as db:
        emails = set(await db.scalars(select(User.email)))
    assert emails == {"active@example.com"}
    assert await count(app, Alert) == 1  # the recent one
    (event,) = await trail(app, "retention.applied")
    assert event.detail["inactive_users"] == 1
    assert event.detail["alert_retention_days"] == settings.alert_retention_days


async def test_a_retention_left_empty_keeps_for_ever(
    app: FastAPI, settings: Settings, sign_in_as: SignIn
) -> None:
    await sign_in_as("team:payments:viewer", email="gone@example.com")
    await as_org_admin(app, "UPDATE users SET last_login_at = now() - interval '4000 days'")
    keep = settings.model_copy(update={"user_retention_days": None, "alert_retention_days": None})
    assert (await run_retention(app, keep, apply=True)).inactive_users == 0
    assert await count(app, User) == 1


async def test_an_export_holds_everything_about_the_person_and_no_credential(
    app: FastAPI, sign_in_as: SignIn
) -> None:
    alice = await sign_in_as("team:payments:responder", email="alice@example.com")
    await alice.post("/alerts", json=ALERT)
    await sign_in_as("team:platform:viewer", email="someone-else@example.com")

    async with app.state.sessionmaker() as db:
        await set_transaction_settings(db, {"app.org_admin": "on"})
        exported = await privacy.export(db, "alice@example.com")

    (account,) = exported["accounts"]
    assert account["email"] == "alice@example.com"
    assert account["teams"] == [{"team": "payments", "role": "responder"}]
    assert len(account["sessions"]) == 1
    assert [e["action"] for e in account["audit_events"]] == ["alert.created"]
    assert exported["held_elsewhere"]
    text_of_it = json.dumps(exported)
    assert "someone-else" not in text_of_it
    assert "id_hash" not in text_of_it
    assert "id_token" not in text_of_it
    (event,) = await trail(app, "user.exported", target_id=await user_id(alice))
    assert event.via == "cli"


async def test_forgetting_erases_the_person_and_keeps_a_pseudonymous_trail(
    app: FastAPI, sign_in_as: SignIn
) -> None:
    bob = await sign_in_as("team:payments:responder", email="bob@example.com")
    await bob.post("/alerts", json=ALERT)
    bob_id = await user_id(bob)

    async with app.state.sessionmaker() as db:
        await set_transaction_settings(db, {"app.org_admin": "on"})
        dry = await privacy.forget(db, "bob@example.com", apply=False)
    assert [(f.user_id, f.audit_events_kept) for f in dry] == [(bob_id, 1)]
    assert await count(app, User) == 1  # a dry run erases nothing

    async with app.state.sessionmaker() as db:
        await set_transaction_settings(db, {"app.org_admin": "on"})
        await privacy.forget(db, "bob@example.com", apply=True)

    assert await count(app, User) == 0
    assert await count(app, Membership) == 0
    assert await count(app, UserSession) == 0
    assert (await bob.get("/me")).status_code == 401  # signed out everywhere
    # The trail stays, naming an id that resolves to nobody.
    kept = await trail(app, "alert.created", actor_user_id=bob_id)
    assert len(kept) == 1
    (forgotten,) = await trail(app, "user.forgotten", target_id=bob_id)
    assert forgotten.detail == {"audit_events_kept": 1}
    async with app.state.sessionmaker() as db:
        await set_transaction_settings(db, {"app.org_admin": "on"})
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) >= 2


def test_the_operator_commands_are_dry_runs_unless_told(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli(["retention"])
    assert "would delete (dry run" in capsys.readouterr().out
    cli(["user-forget", "--email", "nobody@example.com"])
    assert "no account with the email nobody@example.com" in capsys.readouterr().err
    cli(["user-export", "--email", "nobody@example.com"])
    out = capsys.readouterr()
    assert json.loads(out.out)["accounts"] == []
