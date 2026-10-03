"""Server-Sent Events: wire format and the response type that carries them.

Every event's data is one line of JSON. That is what lets a token contain
"\\n\\n": JSON escapes newlines, so the text can never end an event early -
the bug a plain `data: <token>` format has as soon as a real model answers
in paragraphs or markdown.

Events on /chat/stream, in order (app/triage.py, answer_events):
    meta   {request_id, trace_id, model, mode, alerts_in_context,
            runbooks_in_context, retrieval}          sent immediately
    tool   {name, ok, summary}                       per tool call (CHAT_MODE=agent)
    token  {delta}                                   zero or more
    done   {usage, ttft_ms, duration_ms, finish_reason, citations,
            invalid_citations, model_calls, tool_calls, alerts_in_context,
            runbooks_in_context}                     success
    error  {code, message, request_id}               failure after the stream began
plus ": keep-alive" comments while waiting (ignored by SSE parsers).
"""

import json
from contextlib import aclosing
from typing import Any

from starlette.responses import StreamingResponse
from starlette.types import Send

HEARTBEAT = ": keep-alive\n\n"


def sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


class SSEResponse(StreamingResponse):
    """StreamingResponse that always closes its generator.

    Starlette iterates the body with a plain `async for`, which never calls
    aclose(): if the client hangs up while a chunk is being sent, the
    generator is left suspended and its `finally` - which stops the LLM
    call - runs only whenever garbage collection gets to it.
    """

    media_type = "text/event-stream"

    async def stream_response(self, send: Send) -> None:
        async with aclosing(self.body_iterator):  # type: ignore[type-var]
            await super().stream_response(send)
