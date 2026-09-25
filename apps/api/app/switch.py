"""The assistant's off switch (ADR-0024).

One row in Postgres, not a setting: it flips in seconds, on every worker
and replica at once, without a deploy - during an incident, when a deploy
is the last thing anyone wants. Off, every question is refused before any
model call (embedding included), with the reason shown to the asker.
Alerts, runbooks and the MCP tools keep working: they call no chat model.

Who may flip it: org admins (PUT /assistant), and operators with the
production host (`make assistant-off`), which works while the identity
provider is down. Every flip is an audit event: who, when, and the hash of
the reason (the audit trail holds no text).

Read on every question: one primary-key lookup, in the request's own
transaction. Not cached, so an "off" is never stale on some worker.
"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import Actor, record, sha256
from app.models import AssistantSwitch

ROW = 1


async def current(db: AsyncSession) -> AssistantSwitch:
    return (await db.execute(select(AssistantSwitch).where(AssistantSwitch.id == ROW))).scalar_one()


async def turn(
    db: AsyncSession, actor: Actor, *, enabled: bool, reason: str | None
) -> AssistantSwitch:
    """Turn the assistant on or off, recording who did it, in the caller's
    transaction: the caller commits. The reason is kept while it is off,
    and cleared when it comes back on."""
    row = (
        await db.execute(
            update(AssistantSwitch)
            .where(AssistantSwitch.id == ROW)
            .values(
                enabled=enabled,
                reason=None if enabled else reason,
                changed_by=actor.user_id,
                changed_at=func.now(),
            )
            .returning(AssistantSwitch)
        )
    ).scalar_one()
    await record(
        db,
        actor,
        "assistant.enabled" if enabled else "assistant.disabled",
        reason_sha256=sha256(reason) if reason else None,
    )
    return row
