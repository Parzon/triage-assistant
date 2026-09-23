"""Reads shared by several routes: the alert list and the chat's context
must apply exactly the same visibility rule, so they share this code."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import BigInteger, Select, func, literal, select, true, tuple_
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Alert, Team
from app.schemas import AlertOut, Severity


async def team_by_slug(db: AsyncSession, slug: str) -> Team | None:
    return (await db.scalars(select(Team).where(Team.slug == slug))).one_or_none()


async def newest_alerts(
    db: AsyncSession,
    team_ids: Sequence[int] | None,
    *,
    limit: int,
    severity: Severity | None = None,
    before: tuple[datetime, int] | None = None,
) -> list[AlertOut]:
    """Newest first among `team_ids` (None: every team), optionally only one
    severity, optionally only older than a keyset cursor.

    Several teams are read one team at a time and merged - a LATERAL join,
    each team through the (team_id, created_at, id) index - so the cost is
    bounded by teams x limit index entries whatever the data looks like.
    The obvious `WHERE team_id = ANY(...)` lets the planner walk the global
    time index and filter instead, which depends on the data: measured on
    2.5M rows, 0.015ms to 13ms (103k rows filtered out when one of the
    user's teams is large but quiet), against 0.05-0.1ms here in every case.
    """
    if team_ids is not None and not team_ids:
        return []
    filters = []
    if severity is not None:
        filters.append(Alert.severity == severity)
    if before is not None:
        filters.append(tuple_(Alert.created_at, Alert.id) < before)
    newest = (Alert.created_at.desc(), Alert.id.desc())

    stmt: Select[tuple[Alert, str]]
    if team_ids is None or len(team_ids) == 1:
        stmt = select(Alert, Team.slug).join(Team, Team.id == Alert.team_id).where(*filters)
        if team_ids is not None:
            stmt = stmt.where(Alert.team_id == team_ids[0])
        stmt = stmt.order_by(*newest).limit(limit)
    else:
        teams = (
            func.unnest(literal(list(team_ids), ARRAY(BigInteger)))
            .table_valued("id")
            .render_derived(name="t")
        )
        per_team = (
            select(Alert)
            .where(Alert.team_id == teams.c.id, *filters)
            .order_by(*newest)
            .limit(limit)
            .lateral("a")
        )
        alert = aliased(Alert, per_team)
        stmt = (
            select(alert, Team.slug)
            .select_from(teams)
            .join(per_team, true())
            .join(Team, Team.id == alert.team_id)
            .order_by(alert.created_at.desc(), alert.id.desc())
            .limit(limit)
        )
    return [to_out(a, slug) for a, slug in (await db.execute(stmt)).tuples()]


def to_out(alert: Alert, team: str) -> AlertOut:
    return AlertOut(
        id=alert.id,
        team=team,
        source=alert.source,
        severity=alert.severity,
        message=alert.message,
        created_at=alert.created_at,
    )
