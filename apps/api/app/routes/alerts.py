"""Alert intake and listing. Every route acts for the signed-in user and
sees only their teams' alerts (app/access.py)."""

import base64
import binascii
import hmac
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.access import Role, require_role
from app.db import DbSession, set_transaction_settings
from app.models import DEFAULT_TEAM, SEVERITIES, Alert, Team
from app.queries import newest_alerts, team_by_slug, to_out
from app.ratelimit import rate_limit
from app.schemas import AlertIn, AlertmanagerWebhook, AlertOut, AlertPage, Severity
from app.sessions import CurrentUser

log = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post(
    "",
    status_code=201,
    response_model=AlertOut,
    dependencies=[Depends(rate_limit("alerts", "alerts_rate_limit"))],
)
async def create_alert(payload: AlertIn, principal: CurrentUser, db: DbSession) -> AlertOut:
    team = await team_by_slug(db, payload.team)
    if team is None:
        raise HTTPException(status_code=404, detail="team not found")
    require_role(principal, team.id, Role.RESPONDER, "team")
    alert = Alert(team_id=team.id, **payload.model_dump(exclude={"team"}))
    db.add(alert)
    await db.commit()
    return to_out(alert, team.slug)


@router.get("", response_model=AlertPage)
async def list_alerts(
    principal: CurrentUser,
    db: DbSession,
    team: str | None = None,
    severity: Severity | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> AlertPage:
    """Newest first, from every team the caller can see, or one (?team=).
    Keyset pagination: the cursor encodes the last row's (created_at, id),
    so page N costs the same as page 1 - unlike OFFSET, which reads and
    discards every earlier row."""
    team_ids = principal.team_ids()
    if team is not None:
        found = await team_by_slug(db, team)
        if found is None:
            raise HTTPException(status_code=404, detail="team not found")
        require_role(principal, found.id, Role.VIEWER, "team")
        team_ids = [found.id]
    before = decode_cursor(cursor) if cursor is not None else None
    rows = await newest_alerts(db, team_ids, limit=limit + 1, severity=severity, before=before)
    page, more = rows[:limit], len(rows) > limit
    return AlertPage(items=page, next_cursor=encode_cursor(page[-1]) if more else None)


@router.post("/alertmanager", include_in_schema=False)
async def ingest_alertmanager(
    payload: AlertmanagerWebhook,
    request: Request,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    """Alertmanager webhook: firing alerts become triage alerts.

    Internal only (nginx refuses this path). Authenticated with a shared
    bearer token, compared in constant time - a service, not a user, so no
    session. Routed by the alert's `team` label to that team, else to the
    default team. Idempotent: Alertmanager re-sends a firing alert every
    repeat_interval, and the same (fingerprint, startsAt) - one firing
    episode - is stored once.
    """
    token = request.app.state.settings.alertmanager_webhook_token
    # Also checked here, not only in Settings: pydantic's model_copy() skips
    # validators, and an empty secret must never match "Bearer ".
    if token is None or not token.get_secret_value():
        raise HTTPException(status_code=404, detail="Not Found")
    expected = f"Bearer {token.get_secret_value()}"
    if not hmac.compare_digest((authorization or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid webhook token")

    await set_transaction_settings(db, {"app.service": "alertmanager"})
    firing = [alert for alert in payload.alerts if alert.status == "firing"]
    wanted = {alert.labels.get("team", DEFAULT_TEAM) for alert in firing} | {DEFAULT_TEAM}
    found = await db.execute(select(Team.slug, Team.id).where(Team.slug.in_(wanted)))
    teams = dict(found.tuples().all())
    if unknown := wanted - teams.keys():
        log.warning(
            "alerts for unknown teams go to the default team", extra={"teams": sorted(unknown)}
        )
    rows = [
        {
            "team_id": teams.get(alert.labels.get("team", DEFAULT_TEAM), teams[DEFAULT_TEAM]),
            "source": f"alertmanager/{alert.labels.get('alertname', 'unknown')}",
            "severity": _severity(alert.labels.get("severity")),
            "message": (
                alert.annotations.get("summary")
                or alert.annotations.get("description")
                or alert.labels.get("alertname", "alert")
            )[:4000],
            "external_id": f"{alert.fingerprint}:{alert.startsAt.isoformat()}",
        }
        for alert in firing
    ]
    created = 0
    if rows:
        stmt = insert(Alert).values(rows).on_conflict_do_nothing(index_elements=["external_id"])
        created = len((await db.execute(stmt.returning(Alert.id))).all())
        await db.commit()
    return {"received": len(payload.alerts), "created": created}


def _severity(label: str | None) -> str:
    return label if label in SEVERITIES else "info"


@router.get("/{alert_id}", response_model=AlertOut)
async def get_alert(alert_id: int, principal: CurrentUser, db: DbSession) -> AlertOut:
    row = (
        await db.execute(
            select(Alert, Team.slug)
            .join(Team, Team.id == Alert.team_id)
            .where(Alert.id == alert_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="alert not found")
    require_role(principal, row[0].team_id, Role.VIEWER, "alert")
    return to_out(*row)


@router.delete(
    "/{alert_id}",
    status_code=204,
    dependencies=[Depends(rate_limit("alerts", "alerts_rate_limit"))],
)
async def delete_alert(alert_id: int, principal: CurrentUser, db: DbSession) -> Response:
    """Team admins only: removes noise and test alerts."""
    alert = await db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    require_role(principal, alert.team_id, Role.ADMIN, "alert")
    await db.delete(alert)
    await db.commit()
    return Response(status_code=204)


def encode_cursor(alert: AlertOut) -> str:
    raw = f"{alert.created_at.isoformat()}|{alert.id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        created_at, alert_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(created_at), int(alert_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
