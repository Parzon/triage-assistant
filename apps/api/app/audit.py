"""Who did what, kept where the api cannot rewrite it (ADR-0019).

An audit event is a row in audit_events, added to the same transaction as
the change it records: no change without its record, and no record of a
change that rolled back. The app's database role may insert and read audit
rows, never update or delete them (the migration revokes both), so a buggy
or compromised api can add to the history but not rewrite it. Old rows are
pruned by the schema owner (`make audit-prune`).

What is recorded, and the question each answers:
- runbook.saved, runbook.deleted, alert.created (by a user or the
  Alertmanager webhook), alert.deleted: who wrote the text the model reads.
  A planted instruction in a runbook is traced to the save that wrote it.
- chat.asked: what the model was given, for whom. The prompt's version and
  hash, the model, the alerts and runbook sections in its context, and each
  runbook's version (the hash of its text). A chat's sections lead to the
  save that wrote them.
- assistant.disabled, assistant.enabled: who switched the assistant off or
  on (app/switch.py), and the hash of the reason they gave.
- retention.applied, user.exported, user.forgotten: personal data deleted
  by age, exported for a person, or erased (app/privacy.py).

Never the text of a question, an answer, an alert or a runbook: like
traces (ADR-0018), the audit trail holds ids, counts and hashes. The text
lives in its own tables, under its own access rules, for as long as it
exists; a hash still proves which version a chat was given.

Traces answer "where did this request's time go?" for a sample of requests,
kept for days. The audit trail answers "who did this, and what did the
assistant see?" for every one, kept for as long as the organisation must.

Reading it: org admins through GET /audit; operators, `make audit`.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Select, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import Principal
from app.logs import request_id_var
from app.models import AuditEvent, Team, User
from app.schemas import AuditEventOut
from app.tracing import trace_id

Action = Literal[
    "alert.created",
    "alert.deleted",
    "assistant.disabled",
    "assistant.enabled",
    "chat.asked",
    "retention.applied",
    "runbook.deleted",
    "runbook.saved",
    "tool.called",
    "user.exported",
    "user.forgotten",
]


@dataclass(frozen=True)
class Actor:
    """Who an event is recorded for: a signed-in user (through the api, or
    an MCP client: app/mcp_server.py), or no user - the Alertmanager webhook,
    an operator's command."""

    user_id: int | None
    via: Literal["api", "mcp", "alertmanager", "cli"]

    @classmethod
    def of(cls, principal: Principal) -> "Actor":
        return cls(principal.user_id, "api")


ALERTMANAGER = Actor(None, "alertmanager")
CLI = Actor(None, "cli")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def record(
    db: AsyncSession,
    actor: Actor,
    action: Action,
    *,
    team_id: int | None = None,
    target_id: int | None = None,
    **detail: Any,
) -> None:
    """Insert an event in the session's open transaction: it commits, or
    rolls back, with the change it records. The request id and the trace id
    come from the request being served, when there is one.

    An INSERT that returns nothing (inline): Postgres checks a returned row
    against the SELECT policy too, and only org admins may read audit rows.
    The ORM's add() - or a plain insert(), which asks for the new id back -
    is refused by row-level security."""
    request_id = request_id_var.get()
    await db.execute(
        insert(AuditEvent)
        .inline()
        .values(
            action=action,
            actor_user_id=actor.user_id,
            via=actor.via,
            team_id=team_id,
            target_id=target_id,
            request_id=None if request_id == "-" else request_id,
            trace_id=trace_id(),
            detail=detail,
        )
    )


def events(
    *,
    action: str | None = None,
    actor_user_id: int | None = None,
    team_id: int | None = None,
    target_id: int | None = None,
    since: datetime | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> Select[tuple[AuditEvent, str | None, str]]:
    """Newest first, filtered, with the actor's email and the team's slug.
    Row-level security shows rows to org admins only: the caller's
    transaction must say it is one."""
    stmt = (
        select(AuditEvent, User.email, Team.slug)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .outerjoin(Team, Team.id == AuditEvent.team_id)
        .order_by(AuditEvent.id.desc())
        .limit(limit)
    )
    if action is not None:
        stmt = stmt.where(AuditEvent.action == action)
    if actor_user_id is not None:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_user_id)
    if team_id is not None:
        stmt = stmt.where(AuditEvent.team_id == team_id)
    if target_id is not None:
        stmt = stmt.where(AuditEvent.target_id == target_id)
    if since is not None:
        stmt = stmt.where(AuditEvent.created_at >= since)
    if before_id is not None:
        stmt = stmt.where(AuditEvent.id < before_id)
    return stmt


def to_out(event: AuditEvent, email: str | None, team: str | None) -> AuditEventOut:
    return AuditEventOut(
        id=event.id,
        created_at=event.created_at,
        action=event.action,
        actor_user_id=event.actor_user_id,
        actor_email=email,
        via=event.via,
        team=team,
        target_id=event.target_id,
        request_id=event.request_id,
        trace_id=event.trace_id,
        detail=event.detail,
    )
