"""Distributed traces (OpenTelemetry): one trace per request, one span per
step that takes time - retrieval, the embedding call, each SQL statement,
the model call.

Metrics say how the service behaves on average; a trace says where one
request's time went and what it was given: which sections, which prompt
version, which model, how many tokens, when the first token came.

Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set (`make obs-up` sets it, and
starts Jaeger). The standard OTEL_* variables apply: OTEL_TRACES_SAMPLER
and OTEL_TRACES_SAMPLER_ARG for sampling, OTEL_SERVICE_NAME,
OTEL_RESOURCE_ATTRIBUTES. Spans follow the GenAI semantic conventions
(gen_ai.*), which are still marked "Development": names may change.

What spans never hold by default: questions, prompts, answers, alert or
runbook text. A trace store is read by everyone who debugs, across every
team, and keeps what it gets for its own retention: content there would
undo the team isolation the service enforces. Spans carry ids, counts,
durations and hashes; TRACE_CONTENT=true adds redacted content, for
development only (ADR-0018).
"""

import json
import logging
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import Settings
from app.redact import redact

log = logging.getLogger(__name__)

# Content attributes are cut to this many characters: a whole prompt can be
# tens of kilobytes, past what trace backends accept for one attribute.
MAX_CONTENT_CHARS = 4000

_provider: TracerProvider | None = None


def configure_tracing(settings: Settings, processor: SpanProcessor | None = None) -> bool:
    """Install the process's tracer provider, once. Called in each worker's
    startup (the lifespan), after gunicorn forks: the batch processor's
    export thread must live in the process that records the spans.

    Returns True when this call installed it: the caller then owns it, and
    shuts it down. OpenTelemetry allows one provider per process, so a
    second app in the same process (tests) shares the first one's.

    `processor` is for tests (a synchronous one, over an in-memory
    exporter); otherwise spans are batched and sent over OTLP/HTTP to
    OTEL_EXPORTER_OTLP_ENDPOINT."""
    global _provider
    if _provider is not None:
        return False
    if processor is None:
        if not settings.otel_exporter_otlp_endpoint:
            return False
        # Imported here: it pulls in requests and protobuf, which a process
        # with tracing off never needs.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # Batched, on a background thread: a request never waits for the
        # exporter, and a trace backend that is down loses spans, not
        # requests. The exporter reads OTEL_EXPORTER_OTLP_ENDPOINT itself,
        # and appends /v1/traces.
        processor = BatchSpanProcessor(OTLPSpanExporter())
    # Resource.create adds OTEL_RESOURCE_ATTRIBUTES; the service name comes
    # from Settings, which reads OTEL_SERVICE_NAME.
    resource = Resource.create(
        {SERVICE_NAME: settings.otel_service_name, SERVICE_VERSION: settings.app_version}
    )
    # The sampler comes from OTEL_TRACES_SAMPLER (default: every trace,
    # following the caller's decision when a trace context arrives).
    _provider = TracerProvider(resource=resource)
    _provider.add_span_processor(processor)
    trace.set_tracer_provider(_provider)
    log.info("tracing on", extra={"endpoint": settings.otel_exporter_otlp_endpoint or "test"})
    return True


def tracing_on() -> bool:
    return _provider is not None


def shutdown_tracing() -> None:
    """Export what is buffered, then stop (a worker's shutdown)."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


def trace_id() -> str | None:
    """The current trace's id as Jaeger shows it, or None outside a trace."""
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


# gen_ai.provider.name: the well-known value when the endpoint is one of
# these providers' own; otherwise the endpoint's host ("ollama",
# "mock-llm"), which the conventions allow as a custom value.
_PROVIDERS = {
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "api.groq.com": "groq",
    "api.mistral.ai": "mistral_ai",
    "api.deepseek.com": "deepseek",
    "api.x.ai": "x_ai",
    "generativelanguage.googleapis.com": "gcp.gemini",
}


def provider_attributes(base_url: str) -> dict[str, Any]:
    """gen_ai.provider.name, server.address and server.port for a model
    endpoint."""
    url = urlsplit(base_url)
    host = url.hostname or "unknown"
    if host.endswith(".openai.azure.com"):
        provider = "azure.ai.openai"
    elif host.startswith("bedrock-runtime."):
        provider = "aws.bedrock"
    else:
        provider = _PROVIDERS.get(host, host)
    port = url.port or (443 if url.scheme == "https" else 80)
    return {"gen_ai.provider.name": provider, "server.address": host, "server.port": port}


def text_parts(text: str) -> str:
    """Content as the conventions shape it ([{"type": "text", ...}]), JSON
    encoded, redacted and cut to MAX_CONTENT_CHARS."""
    return json.dumps([{"type": "text", "content": _clip(text)}])


def messages_json(messages: Sequence[dict[str, str]], finish_reason: str | None = None) -> str:
    """gen_ai.input.messages / gen_ai.output.messages: role and text parts."""
    shaped: list[dict[str, Any]] = []
    for message in messages:
        entry: dict[str, Any] = {
            "role": message["role"],
            "parts": [{"type": "text", "content": _clip(message["content"])}],
        }
        if finish_reason is not None:
            entry["finish_reason"] = finish_reason
        shaped.append(entry)
    return json.dumps(shaped)


def _clip(text: str) -> str:
    text, _ = redact(text)
    if len(text) <= MAX_CONTENT_CHARS:
        return text
    return text[:MAX_CONTENT_CHARS] + f"... [{len(text) - MAX_CONTENT_CHARS} more characters]"
