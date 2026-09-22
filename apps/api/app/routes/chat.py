"""Streaming chat over SSE (POST, so the question travels in a JSON body -
the browser's EventSource can only GET)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.config import Settings
from app.logs import request_id_var
from app.models import Alert
from app.ratelimit import rate_limit
from app.schemas import ChatRequest
from app.sse import SSEResponse
from app.triage import answer_events, build_messages

router = APIRouter(tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # Tells any nginx in the path (ours, or an ingress infra adds later)
    # not to buffer this response, even if its config would.
    "X-Accel-Buffering": "no",
}


@router.post("/chat/stream", dependencies=[Depends(rate_limit("chat", "chat_rate_limit"))])
async def chat_stream(payload: ChatRequest, request: Request) -> SSEResponse:
    state = request.app.state
    settings: Settings = state.settings
    # Context is loaded before the response starts: a database failure is
    # still a clean JSON 503, and no connection is held while the model
    # streams for tens of seconds.
    async with state.sessionmaker() as session:
        recent = select(Alert).order_by(Alert.created_at.desc(), Alert.id.desc())
        alerts = list(await session.scalars(recent.limit(settings.chat_context_alerts)))
    events = answer_events(
        state.llm,
        build_messages(payload.message, alerts),
        request_id=request_id_var.get(),
        alerts_in_context=len(alerts),
        stream_timeout_s=settings.llm_stream_timeout_s,
        heartbeat_s=settings.sse_heartbeat_s,
    )
    return SSEResponse(events, headers=SSE_HEADERS)
