"""The audit trail (app/audit.py, ADR-0019): org admins only. Who wrote a
runbook section, and what the assistant was given for each question."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.audit import events, to_out
from app.db import DbSession
from app.queries import team_by_slug
from app.schemas import AuditPage
from app.sessions import CurrentUser

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=AuditPage)
async def list_events(
    principal: CurrentUser,
    db: DbSession,
    action: Annotated[str | None, Query(pattern=r"^[a-z_]+\.[a-z_]+$")] = None,
    actor: int | None = None,
    team: str | None = None,
    target: int | None = None,
    since: datetime | None = None,
    before: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    """Newest first. ?target= is a runbook's or an alert's id: with
    ?action=runbook.saved, every version of one runbook and who wrote it."""
    # 403, not 404: the audit trail's existence is no secret, only its rows.
    if not principal.org_admin:
        raise HTTPException(status_code=403, detail="needs org admin")
    team_id = None
    if team is not None:
        found = await team_by_slug(db, team)
        if found is None:
            raise HTTPException(status_code=404, detail="team not found")
        team_id = found.id
    stmt = events(
        action=action,
        actor_user_id=actor,
        team_id=team_id,
        target_id=target,
        since=since,
        before_id=before,
        limit=limit + 1,
    )
    rows = (await db.execute(stmt)).tuples().all()
    page, more = rows[:limit], len(rows) > limit
    return AuditPage(
        items=[to_out(*row) for row in page],
        next_before=page[-1][0].id if more else None,
    )
