"""Alert intake and listing."""

import base64
import binascii
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Alert
from app.ratelimit import rate_limit
from app.schemas import AlertIn, AlertOut, AlertPage, Severity

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
