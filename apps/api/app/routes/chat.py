"""Streaming chat over SSE (POST, so the question travels in a JSON body -
the browser's EventSource can only GET)."""

from fastapi import APIRouter, Depends, Request

from app.config import Settings
from app.db import DbSession
from app.logs import request_id_var
from app.queries import newest_alerts
from app.ratelimit import rate_limit
from app.runbooks import Retrieval, search_runbooks
from app.schemas import ChatRequest
from app.sessions import CurrentUser
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
async def chat_stream(
    payload: ChatRequest, request: Request, principal: CurrentUser, db: DbSession
) -> SSEResponse:
    state = request.app.state
    settings: Settings = state.settings
    # The model sees exactly what the asker may see: their teams' alerts,
    # read with the same query as the alert list, and their teams' runbook
    # sections, searched under the same rule. Loaded before the response
    # starts, so a database failure is still a clean JSON 503; the session
    # is closed when this function returns (DbSession's scope), so no
    # connection is held while the model streams for tens of seconds.
    retrieval: Retrieval | None = None
    if settings.rag_context_chunks and state.llm.embedding_model is not None:
        # First: it embeds the question with no transaction open.
        retrieval = await search_runbooks(
            db,
            state.llm,
            settings,
            payload.message,
            principal.team_ids(),
            k=settings.rag_context_chunks,
        )
    alerts = await newest_alerts(db, principal.team_ids(), limit=settings.chat_context_alerts)
    sections = retrieval.hits if retrieval else []
    events = answer_events(
        state.llm,
        build_messages(payload.message, alerts, sections),
        request_id=request_id_var.get(),
        alerts_in_context=len(alerts),
        stream_timeout_s=settings.llm_stream_timeout_s,
        heartbeat_s=settings.sse_heartbeat_s,
        sections=sections,
        retrieval=retrieval.mode if retrieval else None,
    )
    return SSEResponse(events, headers=SSE_HEADERS)
