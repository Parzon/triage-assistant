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
drop_mid_stream | empty_answer), fail_rate (0..1, share of requests that
fail). empty_answer streams no text and stops with finish_reason "length":
what a reasoning model does when its thinking uses the whole output limit.
A max_tokens below the reply's length truncates it, with "length" too.
POST /v1/embeddings answers with a hashed bag of words: deterministic, and
texts sharing words point in similar directions, so retrieval tests mean
something without a model. embed_fail_mode (none | http_429 | http_500 |
hang) breaks embeddings alone, leaving chat working.
The API key "invalid-key" (or none) gets a 401, like a real provider.
GET /_admin/stats counts requests and streams - including streams the
client abandoned, which is how cancellation is proven to reach the provider.
"""

import asyncio
import hashlib
import json
import math
import os
import random
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from pydantic import BaseModel, ConfigDict

FAIL_MODES = {"none", "http_429", "http_500", "hang", "drop_mid_stream", "empty_answer"}
EMBED_FAIL_MODES = {"none", "http_429", "http_500", "hang"}
EMBEDDING_DIM = 768


@dataclass
class Behaviour:
    ttft_ms: int = int(os.environ.get("MOCK_TTFT_MS", "300"))
    tokens_per_s: float = float(os.environ.get("MOCK_TOKENS_PER_S", "50"))
    fail_mode: str = os.environ.get("MOCK_FAIL_MODE", "none")
    fail_rate: float = float(os.environ.get("MOCK_FAIL_RATE", "1.0"))
    embed_fail_mode: str = os.environ.get("MOCK_EMBED_FAIL_MODE", "none")


@dataclass
class Stats:
    requests: int = 0
    streams_started: int = 0
    streams_completed: int = 0
    streams_cancelled: int = 0
    active_streams: int = 0
    embedding_requests: int = 0
    embedded_texts: int = 0


behaviour = Behaviour()
stats = Stats()
app = FastAPI(title="mock-llm")

# The provider's side of the story, for dashboards: what a real provider's
# console would show you (requests, streams abandoned by the caller).
requests_total = Counter("mock_llm_requests_total", "Requests received.", ["result"])
streams_total = Counter("mock_llm_streams_total", "Streams by how they ended.", ["end"])
active = Gauge("mock_llm_active_streams", "Streams in progress.")


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
    # An agent's context arrives as tool results rather than in the system prompt.
    results = "\n".join(str(m.get("content") or "") for m in messages if m["role"] == "tool")
    alerts = sum(1 for line in f"{system}\n{results}".splitlines() if line.startswith("- ["))
    cite = " Follow the runbook [R1]." if "\n[R1] " in f"\n{results}" else ""
    return (
        f"Triage summary for: {question}\n\n"
        f"I can see {alerts} recent alert(s) in context.\n"
        "- Start with the newest critical alert — it is usually closest to the cause.\n"
        f"- Check what was deployed in the last hour.{cite}\n\n"
        "(mock-llm reply: café ☕ ünïcödé check)"
    )


def tool_calls_for(body: ChatCompletionRequest) -> list[dict[str, Any]]:
    """Offered tools and none called yet: call every one (search_runbooks with
    the question), like a model that looks before it answers. Then answer."""
    tools = (body.model_extra or {}).get("tools") or []
    if not tools or any(m["role"] == "tool" for m in body.messages):
        return []
    question = next((m["content"] for m in reversed(body.messages) if m["role"] == "user"), "")
    arguments = {"search_runbooks": {"query": str(question)[:300]}}
    return [
        {
            "index": i,
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": tool["function"]["name"],
                "arguments": json.dumps(arguments.get(tool["function"]["name"], {})),
            },
        }
        for i, tool in enumerate(tools)
    ]


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
        requests_total.labels("401").inc()
        return openai_error(401, "invalid API key", "invalid_api_key")
    failing = behaviour.fail_mode != "none" and random.random() < behaviour.fail_rate  # noqa: S311
    if failing and behaviour.fail_mode == "http_429":
        requests_total.labels("429").inc()
        return openai_error(429, "rate limit reached for requests", "rate_limit_exceeded")
    if failing and behaviour.fail_mode == "http_500":
        requests_total.labels("500").inc()
        return openai_error(500, "the server had an error", "server_error")
    requests_total.labels("200").inc()

    calls = tool_calls_for(body)
    reply = [] if calls else tokenize(reply_for(body.messages))
    tokens = reply[: body.max_tokens]
    finish_reason = "tool_calls" if calls else "length" if len(tokens) < len(reply) else "stop"
    if failing and behaviour.fail_mode == "empty_answer":
        tokens, finish_reason = [], "length"
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
        choice = {"index": 0, "message": message, "finish_reason": finish_reason}
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
        stream(
            completion_id, body.model, tokens, usage, include_usage, failing, finish_reason, calls
        ),
        media_type="text/event-stream",
    )


async def stream(
    completion_id: str,
    model: str,
    tokens: list[str],
    usage: dict[str, int],
    include_usage: bool,
    failing: bool,
    finish_reason: str = "stop",
    calls: list[dict[str, Any]] | None = None,
) -> AsyncIterator[str]:
    stats.streams_started += 1
    stats.active_streams += 1
    active.inc()
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
        if calls:  # whole, in one chunk, as Ollama sends them
            choice = {"index": 0, "delta": {"tool_calls": calls}, "finish_reason": None}
            yield chunk(completion_id, model, choices=[choice])
        last = {"index": 0, "delta": {}, "finish_reason": finish_reason}
        yield chunk(completion_id, model, choices=[last])
        if include_usage:
            yield chunk(completion_id, model, choices=[], usage=usage)
        yield "data: [DONE]\n\n"
        stats.streams_completed += 1
        streams_total.labels("completed").inc()
    except asyncio.CancelledError:
        # The caller hung up. A real provider would stop generating (and
        # billing) here; the counter lets tests prove we hung up.
        stats.streams_cancelled += 1
        streams_total.labels("cancelled_by_caller").inc()
        raise
    except ConnectionAbortedError:
        streams_total.labels("dropped").inc()
        raise
    finally:
        stats.active_streams -= 1
        active.dec()


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    input: str | list[str]
    encoding_format: str | None = None
    dimensions: int | None = None


_WORD = re.compile(r"[a-z0-9]+")


def embed_text(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """The hashing trick: each word adds +1 or -1 at a position its hash
    picks. Unit length, so cosine similarity measures shared words."""
    vector = [0.0] * dim
    for word in _WORD.findall(text.lower()):
        digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
        vector[int.from_bytes(digest[:4], "big") % dim] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


@app.post("/v1/embeddings", response_model=None)
async def embeddings(
    body: EmbeddingRequest, authorization: str | None = Header(default=None)
) -> JSONResponse | dict[str, Any]:
    stats.embedding_requests += 1
    key = (authorization or "").removeprefix("Bearer ").strip()
    if not key or key == "invalid-key":
        return openai_error(401, "invalid API key", "invalid_api_key")
    if behaviour.embed_fail_mode == "http_429":
        return openai_error(429, "rate limit reached for requests", "rate_limit_exceeded")
    if behaviour.embed_fail_mode == "http_500":
        return openai_error(500, "the server had an error", "server_error")
    if behaviour.embed_fail_mode == "hang":
        await asyncio.sleep(3600)
    texts = [body.input] if isinstance(body.input, str) else body.input
    stats.embedded_texts += len(texts)
    dim = body.dimensions or EMBEDDING_DIM
    data = [
        {"object": "embedding", "index": i, "embedding": embed_text(text, dim)}
        for i, text in enumerate(texts)
    ]
    tokens = sum(len(text.split()) for text in texts)
    return {
        "object": "list",
        "data": data,
        "model": body.model,
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": "mock-1", "object": "model", "owned_by": "mock"}]}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
    if "embed_fail_mode" in update and update["embed_fail_mode"] not in EMBED_FAIL_MODES:
        allowed = sorted(EMBED_FAIL_MODES)
        return JSONResponse({"error": f"embed_fail_mode must be one of {allowed}"}, 400)
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
