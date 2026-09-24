"""Request and response bodies: the public API contract."""

from datetime import datetime
from typing import Any, Literal

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


class RunbookIn(BaseModel):
    # The owning team's slug; the caller needs the admin role in it.
    team: str = Field(pattern=TEAM_SLUG)
    # Unique per team: saving the same title again replaces the text.
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=200_000)
    source_url: str | None = Field(None, max_length=2000, pattern=r"^https?://")


class RunbookSaved(BaseModel):
    id: int
    team: str
    title: str
    sections: int
    # False: this text was already stored, embedded by the same model.
    changed: bool


class RunbookSummary(BaseModel):
    id: int
    team: str
    title: str
    source_url: str | None
    sections: int
    updated_at: datetime


class RunbookOut(RunbookSummary):
    body: str


class RunbookList(BaseModel):
    items: list[RunbookSummary]


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    # One team's runbooks; default: every team the caller can see.
    team: str | None = Field(None, pattern=TEAM_SLUG)
    k: int = Field(5, ge=1, le=20)
    # One retriever alone (keyword, semantic), or both fused (hybrid).
    mode: Literal["hybrid", "keyword", "semantic"] = "hybrid"


class SearchHit(BaseModel):
    runbook_id: int
    team: str
    title: str
    heading: str
    content: str
    updated_at: datetime
    score: float
    keyword_rank: int | None
    semantic_rank: int | None
    distance: float | None


class SearchOut(BaseModel):
    # As asked, or keyword_only: a hybrid search whose question could not be
    # embedded.
    mode: Literal["hybrid", "keyword", "semantic", "keyword_only"]
    embedding_error: str | None
    hits: list[SearchHit]


class AuditEventOut(BaseModel):
    """One audit event (app/audit.py): who did what, and when."""

    id: int
    created_at: datetime
    # "runbook.saved", "chat.asked"...
    action: str
    # None: not a signed-in user - via says what it was.
    actor_user_id: int | None
    actor_email: str | None
    via: str
    team: str | None
    # The runbook's or the alert's id, by the action.
    target_id: int | None
    request_id: str | None
    trace_id: str | None
    # Ids, counts and hashes, by action; never question, answer or document text.
    detail: dict[str, Any]


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    # Pass as ?before= for the next (older) page; None: no more.
    next_before: int | None
