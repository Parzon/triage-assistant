"""Streaming chat over SSE (POST, so the question travels in a JSON body -
the browser's EventSource can only GET)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import State

from app import agent, switch, tools
from app.access import Principal
from app.audit import Actor, record
from app.config import Settings
from app.db import DbSession
from app.errors import ApiError
from app.logs import request_id_var
from app.metrics import chat_refusals
from app.queries import newest_alerts
from app.ratelimit import rate_limit
from app.runbooks import Retrieval, search_runbooks
from app.schemas import ChatRequest
from app.sessions import CurrentUser
from app.sse import SSEResponse
from app.triage import PROMPT, answer_events, build_messages, pipeline

router = APIRouter(tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # Tells any nginx in the path (ours, or an ingress infra adds later)
    # not to buffer this response, even if its config would.
    "X-Accel-Buffering": "no",
}


@router.post(
    "/chat/stream",
    dependencies=[
        Depends(rate_limit("chat", "chat_rate_limit", "chat_rate_limit_fail_closed")),
    ],
)
async def chat_stream(
    payload: ChatRequest, request: Request, principal: CurrentUser, db: DbSession
) -> SSEResponse:
    state = request.app.state
    settings: Settings = state.settings
    # The off switch (app/switch.py, ADR-0024), before anything that calls
    # a model: the question's embedding included, in either mode.
    turned = await switch.current(db)
    if not turned.enabled:
        chat_refusals.labels("assistant_disabled").inc()
        raise ApiError(
            503,
            "assistant_disabled",
            "the assistant is switched off",
            reason=turned.reason,
            since=turned.changed_at.isoformat(),
        )
    if settings.chat_mode == "agent":
        return await _agent_answer(payload, principal, db, state)
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
    messages = build_messages(payload.message, alerts, sections)
    # Recorded before the answer starts, for every question: what the model
    # is given, for whom (app/audit.py). Ids and versions, never the text;
    # a failed write fails the request, and no answer goes unrecorded.
    await record(
        db,
        Actor.of(principal),
        "chat.asked",
        teams=principal.team_ids(),
        prompt={"name": PROMPT.name, "version": PROMPT.version, "sha256": PROMPT.sha256},
        model=state.llm.model,
        alerts=[alert.id for alert in alerts],
        sections=[
            {
                "chunk_id": hit.chunk_id,
                "runbook_id": hit.runbook_id,
                "runbook_sha256": hit.runbook_sha256,
            }
            for hit in sections
        ],
        retrieval=retrieval.mode if retrieval else None,
        mode="pipeline",
    )
    await db.commit()
    events = answer_events(
        state.llm,
        pipeline(state.llm, messages),
        request_id=request_id_var.get(),
        alerts_in_context=len(alerts),
        stream_timeout_s=settings.llm_stream_timeout_s,
        heartbeat_s=settings.sse_heartbeat_s,
        sections=sections,
        retrieval=retrieval.mode if retrieval else None,
    )
    return SSEResponse(events, headers=SSE_HEADERS)


async def _agent_answer(
    payload: ChatRequest, principal: Principal, db: AsyncSession, state: State
) -> SSEResponse:
    """CHAT_MODE=agent (ADR-0020): the model reads through tools, as the
    asker, as it answers (app/agent.py). What it is given is recorded per
    tool call (tool.called), after this event."""
    settings: Settings = state.settings
    ctx = tools.ToolContext(principal, state.sessionmaker, state.llm, settings)
    prompt = agent.AGENT_PROMPT_REF
    await record(
        db,
        Actor.of(principal),
        "chat.asked",
        teams=principal.team_ids(),
        prompt={"name": prompt.name, "version": prompt.version, "sha256": prompt.sha256},
        model=state.llm.model,
        mode="agent",
    )
    await db.commit()
    events = answer_events(
        state.llm,
        agent.answer(state.llm, payload.message, ctx),
        request_id=request_id_var.get(),
        alerts_in_context=None,
        stream_timeout_s=settings.llm_stream_timeout_s,
        heartbeat_s=settings.sse_heartbeat_s,
        sections=ctx.sections,
        mode="agent",
        alerts_read=ctx.alert_ids,
    )
    return SSEResponse(events, headers=SSE_HEADERS)
