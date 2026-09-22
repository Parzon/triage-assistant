"""A stand-in for any OpenAI-compatible LLM endpoint (OpenAI, Azure OpenAI,
LiteLLM, vLLM, Ollama).

It streams chat completions with realistic timing, so timeouts, streaming,
cancellation and load tests can all be exercised without an API key, cost,
or provider rate limits. Replies are deterministic and deliberately contain
newlines and non-ASCII text, the two things naive streaming code breaks on.

Behaviour is tunable at runtime, so tests and failure drills switch modes
without a restart:
    curl -X POST localhost:8020/_admin/config -d '{"fail_mode": "http_429"}'
Knobs: ttft_ms, tokens_per_s, fail_mode (none | http_429 | http_500 | hang |
drop_mid_stream), fail_rate (0..1, share of requests that fail).
The API key "invalid-key" (or none) gets a 401, like a real provider.
GET /_admin/stats counts requests and streams - including streams the
client abandoned, which is how cancellation is proven to reach the provider.
"""

import asyncio
import json
import os
import random
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

FAIL_MODES = {"none", "http_429", "http_500", "hang", "drop_mid_stream"}


@dataclass
class Behaviour:
    ttft_ms: int = int(os.environ.get("MOCK_TTFT_MS", "300"))
    tokens_per_s: float = float(os.environ.get("MOCK_TOKENS_PER_S", "50"))
    fail_mode: str = os.environ.get("MOCK_FAIL_MODE", "none")
    fail_rate: float = float(os.environ.get("MOCK_FAIL_RATE", "1.0"))


@dataclass
class Stats:
    requests: int = 0
    streams_started: int = 0
    streams_completed: int = 0
    streams_cancelled: int = 0
    active_streams: int = 0


behaviour = Behaviour()
stats = Stats()
app = FastAPI(title="mock-llm")


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    max_tokens: int | None = None


def reply_for(messages: list[dict[str, Any]]) -> str:
    question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    alerts = sum(1 for line in system.splitlines() if line.startswith("- ["))
    return (
        f"Triage summary for: {question}\n\n"
        f"I can see {alerts} recent alert(s) in context.\n"
        "- Start with the newest critical alert — it is usually closest to the cause.\n"
        "- Check what was deployed in the last hour.\n\n"
        "(mock-llm reply: café ☕ ünïcödé check)"
    )


def tokenize(text: str) -> list[str]:
    """Roughly like a BPE tokenizer: words carry their leading space and
    newlines are tokens of their own - so the client must not add spaces."""
    return re.findall(r"\n+|[^\S\n]*[^\s]+", text)


def openai_error(status: int, message: str, kind: str) -> JSONResponse:
    headers = {"retry-after": "2"} if status == 429 else None
    body = {"error": {"message": message, "type": kind, "code": kind}}
    return JSONResponse(body, status_code=status, headers=headers)


def chunk(completion_id: str, model: str, **fields: Any) -> str:
    payload: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": fields.pop("choices", []),
        **fields,
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    body: ChatCompletionRequest, authorization: str | None = Header(default=None)
) -> JSONResponse | StreamingResponse:
    stats.requests += 1
    key = (authorization or "").removeprefix("Bearer ").strip()
    if not key or key == "invalid-key":  # "invalid-key": test hook for auth failures
        return openai_error(401, "invalid API key", "invalid_api_key")
    failing = behaviour.fail_mode != "none" and random.random() < behaviour.fail_rate  # noqa: S311
    if failing and behaviour.fail_mode == "http_429":
        return openai_error(429, "rate limit reached for requests", "rate_limit_exceeded")
    if failing and behaviour.fail_mode == "http_500":
        return openai_error(500, "the server had an error", "server_error")

    tokens = tokenize(reply_for(body.messages))[: body.max_tokens]
    prompt_tokens = sum(len(str(m.get("content", "")).split()) for m in body.messages)
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": len(tokens),
        "total_tokens": prompt_tokens + len(tokens),
    }
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    if not body.stream:
        await asyncio.sleep(behaviour.ttft_ms / 1000 + len(tokens) / behaviour.tokens_per_s)
        message = {"role": "assistant", "content": "".join(tokens)}
        choice = {"index": 0, "message": message, "finish_reason": "stop"}
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.model,
                "choices": [choice],
                "usage": usage,
            }
        )
    include_usage = bool((body.stream_options or {}).get("include_usage"))
    return StreamingResponse(
        stream(completion_id, body.model, tokens, usage, include_usage, failing),
        media_type="text/event-stream",
    )


async def stream(
    completion_id: str,
    model: str,
    tokens: list[str],
    usage: dict[str, int],
    include_usage: bool,
    failing: bool,
) -> AsyncIterator[str]:
    stats.streams_started += 1
    stats.active_streams += 1
    try:
        if failing and behaviour.fail_mode == "hang":
            await asyncio.sleep(3600)
        await asyncio.sleep(behaviour.ttft_ms / 1000)
        first = {"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}
        yield chunk(completion_id, model, choices=[first])
        for i, token in enumerate(tokens):
            if failing and behaviour.fail_mode == "drop_mid_stream" and i == len(tokens) // 2:
                raise ConnectionAbortedError("mock-llm: dropping the connection mid-stream")
            choice = {"index": 0, "delta": {"content": token}, "finish_reason": None}
            yield chunk(completion_id, model, choices=[choice])
            await asyncio.sleep(1 / behaviour.tokens_per_s)
        last = {"index": 0, "delta": {}, "finish_reason": "stop"}
        yield chunk(completion_id, model, choices=[last])
        if include_usage:
            yield chunk(completion_id, model, choices=[], usage=usage)
        yield "data: [DONE]\n\n"
        stats.streams_completed += 1
    except asyncio.CancelledError:
        # The caller hung up. A real provider would stop generating (and
        # billing) here; the counter lets tests prove we hung up.
        stats.streams_cancelled += 1
        raise
    finally:
        stats.active_streams -= 1


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": "mock-1", "object": "model", "owned_by": "mock"}]}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/_admin/config")
async def get_config() -> dict[str, Any]:
    return asdict(behaviour)


@app.post("/_admin/config")
async def set_config(update: dict[str, Any]) -> JSONResponse:
    if "fail_mode" in update and update["fail_mode"] not in FAIL_MODES:
        return JSONResponse({"error": f"fail_mode must be one of {sorted(FAIL_MODES)}"}, 400)
    for key, value in update.items():
        if hasattr(behaviour, key):
            setattr(behaviour, key, type(getattr(behaviour, key))(value))
    return JSONResponse(asdict(behaviour))


@app.post("/_admin/reset")
async def reset() -> dict[str, Any]:
    global behaviour, stats
    behaviour, stats = Behaviour(), Stats()
    return {"config": asdict(behaviour), "stats": asdict(stats)}


@app.get("/_admin/stats")
async def get_stats() -> dict[str, int]:
    return asdict(stats)
