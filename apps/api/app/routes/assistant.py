"""The assistant's off switch (app/switch.py, ADR-0024): everyone may read
whether the assistant answers, and why not; org admins switch it."""

from fastapi import APIRouter, HTTPException

from app import switch
from app.audit import Actor
from app.db import DbSession
from app.models import AssistantSwitch
from app.schemas import AssistantIn, AssistantOut
from app.sessions import CurrentUser

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.get("", response_model=AssistantOut)
async def status(principal: CurrentUser, db: DbSession) -> AssistantOut:
    """What the chat shows before anyone types a question."""
    return _out(await switch.current(db))


@router.put("", response_model=AssistantOut)
async def turn(payload: AssistantIn, principal: CurrentUser, db: DbSession) -> AssistantOut:
    """Org admins only. 403, not 404: the switch's existence is no secret."""
    if not principal.org_admin:
        raise HTTPException(status_code=403, detail="needs org admin")
    row = await switch.turn(db, Actor.of(principal), enabled=payload.enabled, reason=payload.reason)
    await db.commit()
    return _out(row)


def _out(row: AssistantSwitch) -> AssistantOut:
    return AssistantOut(enabled=row.enabled, reason=row.reason, changed_at=row.changed_at)
