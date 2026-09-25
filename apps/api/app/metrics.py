"""Prometheus metrics: what we count, and how /metrics serves it.

Under gunicorn every worker is a separate process with its own memory, so
a normal in-process registry would answer each scrape from whichever
worker got it - counters would jump between unrelated values. In
multiprocess mode (PROMETHEUS_MULTIPROC_DIR set, as in the production
image) each worker writes its samples to files in that directory and
/metrics sums them. The variable is read when prometheus_client is first
imported, so it must be in the environment before the process starts.
Known costs of that mode: no per-process CPU/memory/GC metrics (take those
from cAdvisor), and gauges need an explicit multiprocess_mode.

Label values must stay bounded: the route *template* ("/alerts/{alert_id}"),
never the raw path, and never user ids or free text - every distinct
label combination is a separate time series that Prometheus keeps in RAM.
"""

import asyncio
import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from starlette.responses import Response

# gunicorn's on_starting hook creates (and empties) this directory for the
# server. Any other process importing this module - a one-off script, a
# `docker exec` debugging session - would otherwise crash here: metric
# objects open their files as soon as they are defined.
if _multiproc_dir := os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
    os.makedirs(_multiproc_dir, exist_ok=True)

# Seconds. Buckets decide which quantiles can be answered accurately:
# they bracket the latencies we care about, from 5ms to 10s.
HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
# Model latencies live in a different range: first token 0.1-30s,
# whole answers up to the 120s stream cap.
LLM_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120)

http_requests = Counter(
    "http_requests_total", "HTTP requests handled.", ["method", "route", "status"]
)
http_duration = Histogram(
    "http_request_duration_seconds",
    "Time until the response was fully sent (for a stream: the whole stream).",
    ["method", "route"],
    buckets=HTTP_BUCKETS,
)
http_in_progress = Gauge(
    "http_requests_in_progress", "Requests being handled right now.", multiprocess_mode="livesum"
)

llm_requests = Counter(
    "llm_requests_total",
    "Model calls by outcome: ok, truncated (cut off by the output limit), cancelled "
    "(the client left) or an llm_* error code.",
    ["model", "outcome"],
)
llm_ttft = Histogram(
    "llm_time_to_first_token_seconds",
    "From the start of a chat request to the first token.",
    ["model"],
    buckets=LLM_BUCKETS,
)
llm_duration = Histogram(
    "llm_stream_duration_seconds",
    "From the start of a chat request to the end of the answer.",
    ["model", "outcome"],
    buckets=LLM_BUCKETS,
)
llm_tokens = Counter("llm_tokens_total", "Tokens reported by the provider.", ["model", "kind"])
llm_active_streams = Gauge(
    "llm_active_streams", "Answers being streamed right now.", multiprocess_mode="livesum"
)

app_info = Gauge(
    "app_info",
    "1 for what this process runs: the release, the prompt (name:version), the model and the "
    "embedding model. When answers change, line their change up with this one first.",
    ["version", "prompt", "model", "embedding_model"],
    multiprocess_mode="max",
)

embedding_requests = Counter(
    "embedding_requests_total",
    "Embedding calls, by what was embedded (documents: a runbook written; query: a "
    "question asked) and outcome: ok or an llm_* error code.",
    ["kind", "outcome"],
)
prompt_redactions = Counter(
    "prompt_redactions_total",
    "Credentials removed from alert and runbook text before it reached the model. "
    "Each one is a secret a system printed, or someone planted: fix it at the source.",
)
chat_citations = Counter(
    "chat_citations_total",
    "Runbook sections cited by answers: valid (in the answer's context) or invalid (a "
    "number the model invented). Invalid ones are a hallucination signal.",
    ["validity"],
)
agent_steps = Histogram(
    "agent_steps",
    "Rounds of tool calls in one agent answer (CHAT_MODE=agent): 0 = answered without "
    "a tool; AGENT_MAX_STEPS = stopped by the limit and made to answer.",
    buckets=(0, 1, 2, 3, 4, 5, 6, 8, 10),
)
tool_calls = Counter(
    "tool_calls_total",
    "Tool calls (app/tools.py) by tool (unknown: a name a model invented), outcome "
    "(ok, or error: unknown tool, invalid arguments, a timeout) and via: api (the "
    "agent) or mcp (an MCP client).",
    ["tool", "outcome", "via"],
)
retrieval_duration = Histogram(
    "retrieval_duration_seconds",
    "Runbook retrieval for one question, the query's embedding included, by mode: hybrid, "
    "or keyword_only when the embedding failed or timed out.",
    ["mode"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

event_loop_lag = Histogram(
    "event_loop_lag_seconds",
    "How late the event loop runs a timer: the wait any callback has before it can start.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)

db_pool_in_use = Gauge(
    "db_pool_connections_in_use",
    "Connections of the app's database pools in use right now (sampled each "
    "second). Every legitimate use happens inside a request, so more in use "
    "than requests in progress means connections leaked.",
    multiprocess_mode="livesum",
)
db_pool_max = Gauge(
    "db_pool_connections_max",
    "Size of the app's database pools (pool_size + max_overflow, summed over workers).",
    multiprocess_mode="livesum",
)

auth_logins = Counter(
    "auth_logins_total",
    "Sign-in attempts that reached the callback, by outcome: ok, or why not "
    "(access_denied, invalid_state, expired, login_failed, invalid_token, idp_unavailable).",
    ["outcome"],
)
auth_rejections = Counter(
    "auth_rejections_total",
    "Requests refused before reaching a route: no_session, expired, cross_origin.",
    ["reason"],
)

identity_provider_up = Gauge(
    "identity_provider_up",
    "1 if the identity provider answered the last check with trustworthy metadata "
    "(sampled every 30s per worker); the lowest across workers.",
    multiprocess_mode="livemin",
)

chat_refusals = Counter(
    "chat_refusals_total",
    "Questions refused on purpose, before any model call: assistant_disabled (the "
    "off switch, ADR-0024). Not errors: HighErrorRate leaves them out.",
    ["reason"],
)

ratelimit_decisions = Counter(
    "ratelimit_decisions_total",
    "Rate limiter outcomes: allowed or rejected; with Redis unreachable, fail_open "
    "(let through) or fail_closed (refused: the chat, in production).",
    ["scope", "decision"],
)


async def watch_event_loop_lag(interval_s: float = 0.25) -> None:
    """The saturation signal for an async worker. Ask for a timer every
    interval_s and record how late it fires. Near zero on a healthy worker;
    it grows when CPU-bound work, a blocking call or simply too many
    requests keep the loop busy - and every timeout measured in wall-clock
    time (DB pool, Redis budget, LLM read) starts firing spuriously."""
    loop = asyncio.get_running_loop()
    while True:
        start = loop.time()
        await asyncio.sleep(interval_s)
        event_loop_lag.observe(max(0.0, loop.time() - start - interval_s))


def metrics_response() -> Response:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    else:
        registry = REGISTRY
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
