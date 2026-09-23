"""Request and response bodies: the public API contract."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.access import RoleName
from app.models import TEAM_SLUG

Severity = Literal["info", "warning", "high", "critical"]


class AlertIn(BaseModel):
    # The owning team's slug; the caller needs the responder role in it.
    team: str = Field(pattern=TEAM_SLUG)
    source: str = Field(min_length=1, max_length=200)
    severity: Severity
    message: str = Field(min_length=1, max_length=4000)


class AlertOut(BaseModel):
    id: int
    team: str
    source: str
    severity: Severity
    message: str
    created_at: datetime


class AlertPage(BaseModel):
    items: list[AlertOut]
    # Opaque; pass back as ?cursor= to get the next (older) page.
    next_cursor: str | None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class AlertmanagerAlert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime
    fingerprint: str


class AlertmanagerWebhook(BaseModel):
    """Alertmanager webhook payload (version 4); unknown fields are ignored."""

    version: str
    status: str
    alerts: list[AlertmanagerAlert]


class TeamOut(BaseModel):
    slug: str
    name: str
    role: RoleName


class MeOut(BaseModel):
    id: int
    email: str | None
    name: str | None
    org_admin: bool
    # Every team the user can see, with their role in it (an org admin: all).
    teams: list[TeamOut]


class MemberOut(BaseModel):
    email: str | None
    name: str | None
    role: RoleName


class LogoutOut(BaseModel):
    # Where the browser goes next: the identity provider's sign-out page
    # (which returns to this site), or "/" when it has none.
    logout_url: str
