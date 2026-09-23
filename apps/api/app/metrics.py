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

ratelimit_decisions = Counter(
    "ratelimit_decisions_total",
    "Rate limiter outcomes: allowed, rejected, or fail_open (Redis unreachable).",
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
