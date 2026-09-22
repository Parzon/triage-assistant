"""Alert intake and listing."""

import base64
import binascii
import hmac
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import SEVERITIES, Alert
from app.ratelimit import rate_limit
from app.schemas import AlertIn, AlertmanagerWebhook, AlertOut, AlertPage, Severity

router = APIRouter(prefix="/alerts", tags=["alerts"])

Session = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "",
    status_code=201,
    response_model=AlertOut,
    dependencies=[Depends(rate_limit("alerts", "alerts_rate_limit"))],
)
async def create_alert(payload: AlertIn, session: Session) -> Alert:
    alert = Alert(**payload.model_dump())
    session.add(alert)
    await session.commit()
    return alert


@router.get("", response_model=AlertPage)
async def list_alerts(
    session: Session,
    severity: Severity | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> AlertPage:
    """Newest first. Keyset pagination: the cursor encodes the last row's
    (created_at, id), so page N costs the same as page 1 - unlike OFFSET,
    which reads and discards every earlier row."""
    stmt = select(Alert).order_by(Alert.created_at.desc(), Alert.id.desc()).limit(limit + 1)
    if severity is not None:
        stmt = stmt.where(Alert.severity == severity)
    if cursor is not None:
        created_at, alert_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Alert.created_at, Alert.id) < (created_at, alert_id))
    rows = list(await session.scalars(stmt))
    page, more = rows[:limit], len(rows) > limit
    return AlertPage(
        items=[AlertOut.model_validate(row) for row in page],
        next_cursor=encode_cursor(page[-1]) if more else None,
    )


@router.post("/alertmanager", include_in_schema=False)
async def ingest_alertmanager(
    payload: AlertmanagerWebhook,
    request: Request,
    session: Session,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    """Alertmanager webhook: firing alerts become triage alerts.

    Internal only (nginx refuses this path). Authenticated with a shared
    bearer token, compared in constant time. Idempotent: Alertmanager
    re-sends a firing alert every repeat_interval, and the same
    (fingerprint, startsAt) - one firing episode - is stored once.
    """
    token = request.app.state.settings.alertmanager_webhook_token
    # Also checked here, not only in Settings: pydantic's model_copy() skips
    # validators, and an empty secret must never match "Bearer ".
    if token is None or not token.get_secret_value():
        raise HTTPException(status_code=404, detail="Not Found")
    expected = f"Bearer {token.get_secret_value()}"
    if not hmac.compare_digest((authorization or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid webhook token")

    rows = [
        {
            "source": f"alertmanager/{alert.labels.get('alertname', 'unknown')}",
            "severity": _severity(alert.labels.get("severity")),
            "message": (
                alert.annotations.get("summary")
                or alert.annotations.get("description")
                or alert.labels.get("alertname", "alert")
            )[:4000],
            "external_id": f"{alert.fingerprint}:{alert.startsAt.isoformat()}",
        }
        for alert in payload.alerts
        if alert.status == "firing"
    ]
    created = 0
    if rows:
        stmt = insert(Alert).values(rows).on_conflict_do_nothing(index_elements=["external_id"])
        created = len((await session.execute(stmt.returning(Alert.id))).all())
        await session.commit()
    return {"received": len(payload.alerts), "created": created}


def _severity(label: str | None) -> str:
    return label if label in SEVERITIES else "info"


@router.get("/{alert_id}", response_model=AlertOut)
async def get_alert(alert_id: int, session: Session) -> Alert:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    return alert


def encode_cursor(alert: Alert) -> str:
    raw = f"{alert.created_at.isoformat()}|{alert.id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        created_at, alert_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(created_at), int(alert_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
