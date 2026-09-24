"""Postgres enforces team isolation itself (ADR-0014), behind the app's own
checks. These tests go around the app, straight to the database as the
app's role, and show Postgres refusing: a bug in the app's checks is then
a wrong answer, not a leak."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import set_transaction_settings
from app.models import Alert, Runbook, RunbookChunk
from app.queries import newest_alerts
from tests.integration.conftest import SignIn


@pytest.fixture
async def teams(app: FastAPI, sign_in_as: SignIn) -> dict[str, int]:
    """Two alerts in each of payments and platform, created through the api."""
    seeder = await sign_in_as(
        "org:admin", "team:payments:viewer", "team:platform:viewer", email="seed@example.com"
    )
    for team in ("payments", "platform"):
        for n in range(2):
            body = {"team": team, "source": "rls", "severity": "info", "message": f"{team} {n}"}
            assert (await seeder.post("/alerts", json=body)).status_code == 201
    async with app.state.engine.connect() as conn:
        return dict((await conn.execute(text("SELECT slug, id FROM teams"))).tuples().all())


@asynccontextmanager
async def caller(app: FastAPI, **settings: str) -> AsyncIterator[AsyncSession]:
    """A database session telling Postgres what the app would for a caller."""
    async with app.state.sessionmaker() as db:
        await set_transaction_settings(db, {f"app.{k}": v for k, v in settings.items()})
        yield db


def ids(*team_ids: int) -> str:
    return "{" + ",".join(str(i) for i in team_ids) + "}"


async def count(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(Alert)) or 0)


async def test_a_query_that_forgets_who_is_asking_sees_nothing(
    app: FastAPI, teams: dict[str, int]
) -> None:
    async with app.state.sessionmaker() as db:  # no tenant settings at all
        assert await count(db) == 0


async def test_a_viewer_sees_only_their_teams_even_without_a_where_clause(
    app: FastAPI, teams: dict[str, int]
) -> None:
    async with caller(app, read_team_ids=ids(teams["payments"])) as db:
        visible = set((await db.scalars(select(Alert.team_id))).all())
    assert visible == {teams["payments"]}


async def test_the_app_asking_for_every_team_by_mistake_still_leaks_nothing(
    app: FastAPI, teams: dict[str, int]
) -> None:
    # newest_alerts(db, None) is the org admin's read: every team. Run for a
    # viewer by mistake - the bug row-level security exists to contain -
    # Postgres still answers with the viewer's team only.
    async with caller(app, read_team_ids=ids(teams["payments"])) as db:
        rows = await newest_alerts(db, None, limit=50)
    assert {r.team for r in rows} == {"payments"}


async def test_writing_to_a_team_without_the_right_role_is_refused(
    app: FastAPI, teams: dict[str, int]
) -> None:
    viewer_of_both = ids(teams["payments"], teams["platform"])
    async with caller(
        app, read_team_ids=viewer_of_both, write_team_ids=ids(teams["payments"])
    ) as db:
        db.add(Alert(team_id=teams["platform"], source="x", severity="info", message="forged"))
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.flush()


async def test_a_responder_deletes_nothing_even_if_asked_to(
    app: FastAPI, teams: dict[str, int]
) -> None:
    payments = ids(teams["payments"])
    async with caller(app, read_team_ids=payments, write_team_ids=payments) as db:
        deleted = await db.execute(delete(Alert).returning(Alert.id))
        assert deleted.all() == []
        assert await count(db) == 2


async def test_nothing_updates_an_alert_not_even_an_org_admin(
    app: FastAPI, teams: dict[str, int]
) -> None:
    async with caller(app, org_admin="on") as db:
        changed = await db.execute(update(Alert).values(message="rewritten").returning(Alert.id))
        assert changed.all() == []


async def test_an_ended_transactions_empty_setting_means_no_access(
    app: FastAPI, teams: dict[str, int]
) -> None:
    # After a transaction that set it ends, a setting reads '' in that
    # connection, not NULL: the policies must treat it as "no teams".
    async with caller(app, read_team_ids="") as db:
        assert await count(db) == 0


async def test_org_admins_and_the_alertmanager_service_see_every_team(
    app: FastAPI, teams: dict[str, int]
) -> None:
    for settings in ({"org_admin": "on"}, {"service": "alertmanager"}):
        async with caller(app, **settings) as db:
            assert await count(db) == 4, settings


async def test_every_alert_must_name_its_team(app: FastAPI, teams: dict[str, int]) -> None:
    # The expand release gave team_id a default for the release before it;
    # the contract migration removed it.
    async with caller(app, org_admin="on") as db:
        db.add(Alert(source="x", severity="info", message="no team"))
        with pytest.raises(DBAPIError, match="team_id"):
            await db.flush()


@pytest.fixture
async def runbook_teams(app: FastAPI, sign_in_as: SignIn) -> dict[str, int]:
    """One runbook in each of payments and platform, saved through the api."""
    admin = await sign_in_as("team:payments:admin", "team:platform:admin", email="rb@example.com")
    for team in ("payments", "platform"):
        body = {"team": team, "title": "Restart", "body": f"## Restart\nRestart the {team} app."}
        assert (await admin.post("/runbooks", json=body)).status_code == 200
    async with app.state.engine.connect() as conn:
        return dict((await conn.execute(text("SELECT slug, id FROM teams"))).tuples().all())


async def test_runbooks_and_their_sections_are_isolated_like_alerts(
    app: FastAPI, runbook_teams: dict[str, int]
) -> None:
    payments = runbook_teams["payments"]
    async with caller(app, read_team_ids=ids(payments)) as db:
        assert set((await db.scalars(select(Runbook.team_id))).all()) == {payments}
        assert set((await db.scalars(select(RunbookChunk.team_id))).all()) == {payments}
    async with app.state.sessionmaker() as db:  # nobody said who is asking
        assert (await db.scalars(select(RunbookChunk.id))).all() == []


async def test_only_team_admins_write_runbooks_at_the_database_too(
    app: FastAPI, runbook_teams: dict[str, int]
) -> None:
    payments = ids(runbook_teams["payments"])
    # A responder may write the team's alerts, not its runbooks.
    async with caller(app, read_team_ids=payments, write_team_ids=payments) as db:
        db.add(
            Runbook(
                team_id=runbook_teams["payments"],
                title="forged",
                body="x",
                body_sha256="x",
                embedding_model="m",
            )
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.flush()
    # A payments admin cannot delete platform's sections, even when asked to.
    async with caller(
        app, read_team_ids=payments, write_team_ids=payments, admin_team_ids=payments
    ) as db:
        platform = RunbookChunk.team_id == runbook_teams["platform"]
        deleted = await db.execute(delete(RunbookChunk).where(platform).returning(RunbookChunk.id))
        assert deleted.all() == []
