# AI observability: tracing a question through retrieval and the model

Metrics say how the service behaves on average. When one answer is wrong
or slow, you need that answer's own record:
- what it was given: which sections, how many alerts, which prompt version;
- which model answered, with what settings;
- where its time went: retrieval, the embedding call, the model's start,
  its thinking, its answer;
- what came back: tokens, finish reason, citations.

A trace is that record. This chapter covers:
- what is traced, and what is not (privacy);
- how to read a trace;
- what tracing costs, measured;
- the gotchas met building it.

The decision record is ADR-0018.

✅ = built and measured here (gpt-oss:20b, nomic-embed-text, Jaeger 2.21).
📘 = not built yet.

## Turning it on

```
make obs-up
```

It starts Jaeger (and Prometheus, Grafana, Alertmanager), and recreates
the api with `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`. Jaeger is at
http://localhost:16686; Grafana has it as a data source too.
`make obs-down` stops Jaeger, and recreates the api with tracing off.

- **Tracing is off unless that endpoint is set.** Off, its spans are
  no-ops.
- **In production:** point `OTEL_EXPORTER_OTLP_ENDPOINT` at the platform's
  OpenTelemetry Collector, or at a Jaeger on the host (`compose.prod.yaml`
  has its limits).

## One question, one trace ✅

```
POST /chat/stream                          the request (server span)
├─ SELECT ...                              session, row-level security settings
├─ retrieval runbooks                      GenAI retrieval
│  ├─ embeddings nomic-embed-text          GenAI embeddings: the question
│  └─ WITH keyword AS (...)                the hybrid search's SQL
├─ SELECT alerts ...                       the asker's alerts
└─ chat gpt-oss:20b                        GenAI inference, the whole stream
     └─ event "first token"
```

Every log line of the request carries the same `trace_id`. The chat's
`meta` event carries it too, and `make trace id=<request id>` prints the
Jaeger link.

| Span | Attributes that answer "why" |
|---|---|
| `POST /chat/stream` | `app.prompt.alerts`, `app.prompt.sections`, `app.prompt.redactions`, `app.chat.outcome` (ok, truncated, cancelled, an error code), `app.chat.retrieval`, `app.chat.citations`, `app.chat.invalid_citations`, `user.id`, `app.request_id` |
| `retrieval runbooks` | `app.retrieval.mode` (hybrid, keyword_only), `app.retrieval.embedding_error`, `app.retrieval.embedding_key`, `gen_ai.retrieval.top_k`, `gen_ai.retrieval.documents`: each section's id, fused score, keyword rank, semantic rank, distance |
| `embeddings {model}` | `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `error.type` |
| `chat {model}` | `gen_ai.prompt.name` and `.version`, `app.prompt.sha256`, `gen_ai.request.max_tokens` and `.reasoning.level`, `gen_ai.response.model`, `gen_ai.response.time_to_first_chunk`, the "first token" event, `gen_ai.usage.input_tokens` and `.output_tokens`, `gen_ai.response.finish_reasons`, `app.llm.reasoning_chunks` and `.content_chunks`, `app.llm.outcome`, `error.type` |

The names follow the OpenTelemetry GenAI semantic conventions (their own
repository, `open-telemetry/semantic-conventions-genai`), still marked
"Development": names may change. `app.*` attributes are this service's
own.

### Reading it

1. **What was the model given?** The request's `app.prompt.*`, and
   retrieval's documents. A section missing from a bad answer's documents,
   against a good answer's, is the usual cause.
2. **How was it asked?** Model, prompt version, output limit, reasoning
   level.
3. **What came back?** Tokens, finish reason, the request's outcome.
4. **Where did the time go?** Span durations. On the model span:
   - **time to first chunk:** the provider starting;
   - **first chunk to "first token":** the model thinking.

   Measured with gpt-oss:20b: 3 s to the first chunk right after idle
   (loading the model), 0.04 s warm; then about 0.5 s of reasoning at low
   effort, 4 s at high.

### The eval harness is traced too ✅

With the endpoint set, `make evals`:
- makes each case's run a trace, `eval {case}`;
- records each check as a `gen_ai.evaluation.result` event (the
  convention's name), with the judge's reason as its explanation;
- sends its `traceparent` to the api (the api target), so the service's
  spans sit under the case.

The report records each answer's `trace_id` and the prompt version. From a
failing answer to what it was given: one click.

## What never goes on a span ✅

By default: no question, no prompt, no answer, no alert or runbook text.
Only ids, counts, durations and hashes. The integration test
`test_no_question_prompt_answer_or_runbook_text_reaches_a_span` plants
markers in all of them, and fails if one reaches a span.

A trace is for finding where one request's time went, for a sample of
requests. The record of who did what, for every request, is the audit
trail ([AI security](ai-security.md)).

**Why:**
- **A trace store ignores teams.** The service enforces team isolation
  twice (the queries, row-level security); Jaeger shows every trace to
  whoever can open it. Content in traces undoes both.
- **It keeps what it gets for its own retention,** under other access
  rules, often copied to a vendor.
- **Measured:** with content on, one reader saw a
  finance user's question ("payroll export failed for 1,200 employees")
  and another team's runbooks. Redaction had removed the password in it,
  and nothing else.

**`TRACE_CONTENT=true`** adds the content, for development: the
conventions' `gen_ai.input.messages`, `gen_ai.output.messages`,
`gen_ai.system_instructions`, `gen_ai.retrieval.query.text`, and the
section headings. Each value is redacted (`app/redact.py`) and cut at
4,000 characters.

The conventions name three patterns:
1. **No content** (the default).
2. **Content on spans:** pre-production.
3. **Content in a separate store under its own access control,** and
   references on the spans.

Retrieved sections already follow the third: their ids are on the span,
their text stays in Postgres under row-level security. Keeping questions
and answers is a product decision (answer feedback, PRD), not a tracing
setting.

**Trace headers from outside are dropped:** nginx removes `traceparent`,
`tracestate` and `baggage` from every incoming request, so the internet
cannot pick trace ids, join its requests to another trace, or force
sampling (the sampler follows a caller's "sampled" flag).

## Prompt and model versions ✅

- **`PROMPT_VERSION`** (`app/triage.py`) goes on every model span, in
  `app_info`, and in eval reports, with the template's SHA-256.
- A unit test pins each version's hash: editing `SYSTEM_PROMPT` without
  bumping the version fails the build.
- **`app_info`** (a Prometheus gauge, 1) carries the release, the prompt
  (`triage:v6`), the model and the embedding model. When answers change,
  line their change up with this one first.
- **The model that answered** is on the span (`gen_ai.response.model`). A
  provider can serve a dated version behind an alias: the requested and
  the answering model can differ.

## What it costs, measured ✅

The dev api (one process), 200 signed-in alert-list requests a second for
40 s: 8,000 requests per run, two runs per row except 10%.

| Tracing | p50 | p95 | p99 | api CPU |
|---|---|---|---|---|
| off | 2.45 ms | 2.83–2.87 ms | 6.6–12.5 ms | 23.1–23.6% |
| on, every trace | 2.58–2.60 ms | 5.0–5.9 ms | 39–44 ms | 26.6–27.5% |
| on, 10% of traces | 2.51 ms | 2.99 ms | 20.7 ms | 24.5% |

- **The median barely moves; the tail does.** CPU rose 15–19%. p99 grew three to seven
  times at full sampling.
- **The cost scales with the traces kept.** A likely cause, not measured:
  the export thread serializing batches while holding Python's GIL,
  stalling the event loop.
- **For production:** sample. At 10%, p95 was within 0.2 ms of off.
  Watch `event_loop_lag_seconds` when you raise it.

**When Jaeger is down** (measured):
- Requests do not notice: p50 2.3 ms, p95 2.5 ms over 200 requests. Spans
  are queued and dropped.
- The exporter logs a warning per retry, and an error per dropped batch.
- A worker's shutdown waited out the exporter's 10 s default timeout:
  10.25 s against 0.42 s. With `TRACE_EXPORT_TIMEOUT_S=2`: 1.27 s.

**Sampling, measured:**
- At 10%, Jaeger kept 53 traces of 500 requests.
- A dropped trace hands out no id: the first version put one in the
  `meta` event and the logs, and nine in ten led nowhere.
- **Head sampling decides at the start.** If one question in twenty fails,
  10% keeps about 5 failing traces per 1,000 questions. **Tail sampling**
  (in an OpenTelemetry Collector: keep every error and slow trace, and a
  share of the rest) keeps all of them 📘.

## Settings

| Setting | Default | Notes |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty (off) | OTLP/HTTP; `/v1/traces` is appended. `make obs-up` sets `http://jaeger:4318` |
| `OTEL_TRACES_SAMPLER` / `OTEL_TRACES_SAMPLER_ARG` | `parentbased_always_on` / 1.0 | `parentbased_traceidratio` with 0.1 keeps 10%; a caller's decision wins |
| `OTEL_SERVICE_NAME` | `triage-assistant-api` | the eval harness reports as `triage-assistant-evals` |
| `TRACE_CONTENT` | false | content on spans, redacted: development only |
| `TRACE_EXPORT_TIMEOUT_S` | 2 | one export's budget, retries included; bounds shutdown when the backend is down |
| `APP_VERSION` | `dev` | compose passes `IMAGE_TAG`: `service.version`, `app_info` |

Jaeger keeps the newest 20,000 traces in memory
(`infra/observability/jaeger/config.yaml`); a restart empties it. It is for
what just happened. Keeping weeks of traces needs a real backend (Badger
on disk, Elasticsearch, Tempo on object storage) 📘.

## Gotchas

In the guide's list, with every other gotcha met here: [Tracing](../../gold_standard_development_guide.md#tracing-opentelemetry).

## Not built yet 📘

- **An OpenTelemetry Collector:** tail sampling, a second place to strip
  attributes, fan-out to more than one backend.
- **Metrics from traces** (span metrics, exemplars): not possible in
  multiprocess mode.
- **Durable trace storage,** and access control on it.
- **Content in a separate store** (pattern 3), for production debugging.
- **Tracing the browser,** so a trace starts at the user's click.
- **Tool calls** (`execute_tool` spans): the assistant has no tools yet
  (the agents section).
