"""Request and response bodies: the public API contract."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["info", "warning", "high", "critical"]


class AlertIn(BaseModel):
    source: str = Field(min_length=1, max_length=200)
    severity: Severity
    message: str = Field(min_length=1, max_length=4000)


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
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
