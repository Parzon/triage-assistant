import asyncio
import os
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

app = FastAPI()

# Redis-backed: the rate limit is shared correctly across every worker/
# replica, unlike an in-memory store (see unifiedlearning's /login bug).
limiter = Limiter(key_func=get_remote_address, storage_uri=os.environ["REDIS_URL"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.get("/health")
def health_check():
    return {"status": "ok"}


class Alert(BaseModel):
    source: str
    severity: str
    message: str


@app.post("/alerts")
@limiter.limit("5/minute")
def create_alert(request: Request, alert: Alert):
    return {"received": alert}


class ChatRequest(BaseModel):
    message: str


async def token_stream(message: str) -> AsyncIterator[str]:
    # Placeholder generator — swap this loop for a real streaming model
    # call later. Everything else (the SSE framing, the client-side
    # parsing) stays exactly as-is; that's the point of ADR-0001.
    for word in f"Echo: {message}".split():
        yield f"data: {word}\n\n"
        await asyncio.sleep(0.15)
    yield "data: [DONE]\n\n"


@app.post("/chat/stream")
async def chat_stream(payload: ChatRequest):
    return StreamingResponse(token_stream(payload.message), media_type="text/event-stream")
