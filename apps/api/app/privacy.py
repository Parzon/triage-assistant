"""Personal data: retention, and a person's export and erasure
(docs/privacy.md, ADR-0025). Operator commands run them (app/cli.py): as
the app's role, claiming org admin for row-level security.

Each runs its statements in one transaction and commits only when asked
to: a dry run executes the very same deletes and rolls them back, so what
it reports is exactly what the real run would do.

The audit trail is left alone. The api's role cannot delete from it, and
it must not: it is the record of who did what. It names people by user
id only, so once a person's user row is gone its events are pseudonymous.
The schema owner prunes it by age (`make audit-prune`).
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import CLI, record
from app.config import Settings
from app.models import (
    Alert,
    AssistantSwitch,
    AuditEvent,
    LoginRequest,
    Membership,
    Team,
    User,
    UserSession,
)

# What an export cannot contain, because this service does not keep it or
# does not keep it here: said in every export, so nobody mistakes it for
# the whole picture.
ELSEWHERE = [
    "Questions asked and answers given: not stored. The audit trail records "
    "that you asked, with the ids of what the model was given, never the text.",
    "Access logs: the api's lines carry your user id, nginx's your IP address; "
    "kept by the container log rotation (3 files of 10 MB per container).",
    "Traces: your user id on a sample of requests, in memory, at most 20,000 traces.",
    "Backups: copies of all of the above database rows, for as long as backups are kept.",
    "The identity provider: your account and your groups. It is the source of "
    "truth, and signs you in again unless it removes you.",
    "The model provider: the questions you asked, with the alerts and runbook "
    "sections they were sent with, under its own retention terms.",
]


@dataclass(frozen=True)
class Retained:
    """Rows a retention run deleted (or, in a dry run, would delete)."""

    expired_sessions: int
    expired_sign_ins: int
    inactive_users: int
    old_alerts: int


def _count(result: Result[Any]) -> int:
    # A DELETE's result is a CursorResult: it knows how many rows it hit.
    return cast("CursorResult[Any]", result).rowcount


async def retention(db: AsyncSession, settings: Settings, *, apply: bool) -> Retained:
    """Delete what is past its retention: expired sessions and sign-ins in
    progress, users who have not signed in for USER_RETENTION_DAYS (their
    memberships and sessions go with them), alerts older than
    ALERT_RETENTION_DAYS. A retention of None keeps that kind for ever."""
    now = func.now()
    users = alerts = 0
    sessions = _count(await db.execute(delete(UserSession).where(UserSession.expires_at < now)))
    sign_ins = _count(await db.execute(delete(LoginRequest).where(LoginRequest.expires_at < now)))
    if settings.user_retention_days is not None:
        idle = now - timedelta(days=settings.user_retention_days)
        users = _count(await db.execute(delete(User).where(User.last_login_at < idle)))
    if settings.alert_retention_days is not None:
        old = now - timedelta(days=settings.alert_retention_days)
        alerts = _count(await db.execute(delete(Alert).where(Alert.created_at < old)))
    retained = Retained(sessions, sign_ins, users, alerts)
    if apply:
        await record(
            db,
            CLI,
            "retention.applied",
            **asdict(retained),
            user_retention_days=settings.user_retention_days,
            alert_retention_days=settings.alert_retention_days,
        )
        await db.commit()
    else:
        await db.rollback()
    return retained


async def export(db: AsyncSession, email: str) -> dict[str, Any]:
    """Everything this service's database holds about the person with this
    email (a subject access request): one entry per account, since an email
    can have several (one per identity provider). Never a session's token
    hash or ID token: those are credentials, not information about anyone."""
    accounts = []
    for user in await db.scalars(select(User).where(User.email == email).order_by(User.id)):
        memberships = await db.execute(
            select(Team.slug, Membership.role)
            .join(Team, Team.id == Membership.team_id)
            .where(Membership.user_id == user.id)
            .order_by(Team.slug)
        )
        sessions = await db.execute(
            select(UserSession.created_at, UserSession.last_seen_at, UserSession.expires_at)
            .where(UserSession.user_id == user.id)
            .order_by(UserSession.created_at)
        )
        events = await db.execute(
            select(AuditEvent, Team.slug)
            .outerjoin(Team, Team.id == AuditEvent.team_id)
            .where(AuditEvent.actor_user_id == user.id)
            .order_by(AuditEvent.id)
        )
        switched = await db.scalar(
            select(AssistantSwitch).where(AssistantSwitch.changed_by == user.id)
        )
        accounts.append(
            {
                "user_id": user.id,
                "issuer": user.issuer,
                "subject": user.subject,
                "email": user.email,
                "name": user.name,
                "org_admin": user.is_org_admin,
                "created_at": user.created_at.isoformat(),
                "last_login_at": user.last_login_at.isoformat(),
                "teams": [{"team": slug, "role": role} for slug, role in memberships],
                "sessions": [
                    {
                        "created_at": created.isoformat(),
                        "last_seen_at": seen.isoformat(),
                        "expires_at": expires.isoformat(),
                    }
                    for created, seen, expires in sessions
                ],
                "audit_events": [
                    {
                        "id": event.id,
                        "created_at": event.created_at.isoformat(),
                        "action": event.action,
                        "via": event.via,
                        "team": team,
                        "target_id": event.target_id,
                        "detail": event.detail,
                    }
                    for event, team in events
                ],
                "switched_the_assistant": switched is not None,
            }
        )
        await record(db, CLI, "user.exported", target_id=user.id)
    await db.commit()
    return {
        "email": email,
        "exported_at": datetime.now(UTC).isoformat(),
        "accounts": accounts,
        "held_elsewhere": ELSEWHERE,
    }


@dataclass(frozen=True)
class Forgotten:
    user_id: int
    issuer: str
    # Kept, and pseudonymous from now on: they name a user id that no
    # longer resolves to anyone.
    audit_events_kept: int


async def forget(db: AsyncSession, email: str, *, apply: bool) -> list[Forgotten]:
    """Erase every account with this email: the user row, and with it their
    memberships and sessions. The audit trail keeps its events, which from
    now on name a user id that resolves to nobody. The identity provider
    must remove the person too, or their next sign-in creates them again."""
    forgotten = []
    for user in (await db.scalars(select(User).where(User.email == email))).all():
        kept = await db.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.actor_user_id == user.id)
        )
        await db.execute(delete(User).where(User.id == user.id))
        await record(db, CLI, "user.forgotten", target_id=user.id, audit_events_kept=kept)
        forgotten.append(Forgotten(user.id, user.issuer, kept or 0))
    if apply:
        await db.commit()
    else:
        await db.rollback()
    return forgotten
