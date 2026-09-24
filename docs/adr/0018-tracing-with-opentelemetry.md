# ADR-0018: Traces with OpenTelemetry and the GenAI conventions; no content by default

**Status:** accepted
**Date:** 2026-09-24

## Context

ADR-0008 chose metrics and logs, and left traces for when "a request
crosses several services, or when you need a timing breakdown inside one
request in production". Runbook retrieval (ADR-0017) brought the second.
One answer's time now splits across:
- an embedding call;
- a SQL search;
- the model's wait for its first token, then its thinking, then its answer.

Its quality depends on which sections were retrieved, which prompt
version and model were used, and whether the output limit cut it.
Metrics average all of that away. When one answer is worse, we need that
answer's own record.

## Decision

1. **OpenTelemetry**, the SDK in the api, exported over OTLP/HTTP. It is
   off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set, and the standard
   `OTEL_*` variables apply (sampler, service name, resource attributes).
2. **Spans named and shaped by the OpenTelemetry GenAI semantic
   conventions:** `retrieval runbooks`, `embeddings {model}`,
   `chat {model}`, with `gen_ai.*` attributes, plus `app.*` for this
   service's own facts (retrieval mode, citations, outcome). They are marked
   "Development" and may change; one module
   (`app/tracing.py`) and the span sites follow them.
3. **Manual GenAI spans,** not an instrumentation package for the openai
   SDK. The service needs its own attributes (prompt version, retrieval,
   citations, redactions), control over content, and no dependency on the
   SDK's internals. SQL spans come from the SQLAlchemy instrumentation.
   The server span is the service's own middleware's: the ASGI
   instrumentation package adds a span per streamed chunk by default.
4. **No content on spans by default:** no question, prompt, answer, alert
   or runbook text. Ids, counts, durations, hashes.
   - Why: a trace store ignores team isolation, keeps what it gets on its
     own terms, and is read by everyone who debugs.
   - `TRACE_CONTENT=true` adds redacted content, for development.
   - The retrieved sections are recorded by id: their text stays in
     Postgres, under row-level security.
5. **The prompt is versioned:** `PROMPT_VERSION` and the template's hash,
   on every model span, in `app_info` and in eval reports. A unit test
   fails when the prompt changes without a new version.
6. **Jaeger 2** in the `observability` profile: one container, traces in
   memory, its own UI (trace comparison), and a Grafana data source.
7. **The eval harness joins the traces:** a trace per case run, its checks
   as `gen_ai.evaluation.result` events, and its trace context sent to the
   api.
8. **Trace context from outside is dropped at nginx:** `traceparent`,
   `tracestate`, `baggage`.

## Alternatives considered

- **Grafana Tempo** (with TraceQL, in the Grafana already running). Its
  current major version (3.0) needed configuration work to learn and
  verify, and it is AGPL. Jaeger ran with a short config file, and has
  trace comparison. The api exports OTLP, so switching later changes an
  address, not the code.
- **An LLM-specific platform** (Langfuse, Arize Phoenix, LangSmith).
  - They add: prompt management, datasets, scoring UIs.
  - They are built to store prompts and answers, which decision 4
    forbids by default.
  - The hosted ones send the data out.
  - They accept OpenTelemetry, so the same spans can feed one later.
- **Logs only**, with the request id (the state before). A log line per
  step gives the facts, not the shape: no tree, no timing breakdown, no
  comparison of two requests.
- **Content on by default, redacted.** Measured, redaction removed a
  password, and left a payroll failure affecting 1,200 employees readable
  across teams. Redaction removes known secret formats, not sensitive
  facts.

## Consequences

- **Cost, measured** at 200 req/s on one process:
  - every trace: p95 2.8 → 5–6 ms, p99 7–13 → 39–44 ms, CPU 15–19% more;
  - 10% of traces: p95 within 0.2 ms of off.

  Production samples.
- **A trace backend that is down** loses spans, never requests. A worker's
  shutdown waits up to `TRACE_EXPORT_TIMEOUT_S` (2 s) to flush.
- **Metrics cannot link to traces** (no exemplars in prometheus_client's
  multiprocess mode). Log lines carry the trace id instead.
- **Every setting now reaches the api from `.env`.** Building the lab
  found 22 that did not, and `make lint` checks it.
- **The conventions may rename attributes.** The span sites follow the
  spec's repository, not a pinned package.
