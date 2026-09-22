"""Streaming chat. The generator is a placeholder until the LLM seam
lands (issue #11); the transport - SSE over a POST - is final."""

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.ratelimit import rate_limit
from app.schemas import ChatRequest

router = APIRouter(tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # Tells any nginx in the path (ours, or an ingress infra adds later)
    # not to buffer this response, even if its config would.
    "X-Accel-Buffering": "no",
}


async def token_stream(message: str) -> AsyncIterator[str]:
    for word in f"Echo: {message}".split():
        yield f"data: {word}\n\n"
        await asyncio.sleep(0.15)
    yield "data: [DONE]\n\n"


@router.post("/chat/stream", dependencies=[Depends(rate_limit("chat", "chat_rate_limit"))])
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        token_stream(payload.message), media_type="text/event-stream", headers=SSE_HEADERS
    )
