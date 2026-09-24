# AI observability lab: what was observed

Measured on 2026-09-24:
- gpt-oss:20b at reasoning effort low, and nomic-embed-text with its
  prefixes, through Ollama on one RTX 6000 Ada;
- prompt v6;
- the question "db-1's disk is filling up. What do I do?", asked as a
  viewer of team "lab".

The model samples, so your answers will differ. The telemetry's shape
should not.

## 1. Where does the time go?

| | First ask, after idle | The same question, seconds later |
|---|---|---|
| whole request | 6,136 ms | 1,620 ms |
| retrieval | 1,339 ms: the embedding call took 1,332, the SQL 4 | 28 ms: embedding 24, SQL 2 |
| model: time to first chunk | 3.02 s | 0.04 s |
| model: first answer token | 3.27 s (41 reasoning chunks before it) | 0.50 s (76 reasoning chunks) |
| tokens in / out | 702 / 324 | 702 / 284 |

**Diagnosis:** the first ask paid for loading both models. Ollama unloads a
model after five idle minutes (`OLLAMA_KEEP_ALIVE`): 1.3 s for the
embedding model, about 3 s for gpt-oss.

The model span separates two waits:
- **the time to first chunk:** the provider starting. 3 s when loading,
  40 ms when warm;
- **first chunk to first token:** the model thinking. gpt-oss streams its
  reasoning first, in its own field (`app.llm.reasoning_chunks`).

When warm, almost all of the time to the first answer token is thinking.
A single "time to first token" number cannot tell these apart.

## 2. From a log line to its trace, and back

`make trace id=<request id>` printed:
- the api's log lines for the request: the access line (route, status,
  duration, user id), and any warning;
- then `trace: http://localhost:16686/trace/<id>`.

When the embedding settings had changed (another embedding model than the
one the lab's runbooks were embedded with), the lines included "no section
has a current vector: run `make reembed`": the fix, spelled out.

- **The logs add** the words: warnings, errors, tracebacks.
- **The trace adds** the shape: which step took the time, which step
  failed, what each was given, and the whole thing as one tree.

The request id joins them: the trace's `app.request_id`, the logs'
`request_id`.

## 3. The answers got worse

The good answer's retrieval returned four sections of "Disk full on a
database host", by section id:
- 81, Expand the volume;
- 78, the introduction;
- 79, Check what is using space;
- **80, Free space**: delete WAL archives only after a successful backup;
  never delete in the data directory.

Its answer gave those steps, with the conditions.

| Incident | The answer | What the trace showed | Metrics and logs |
|---|---|---|---|
| `EMBEDDING_TIMEOUT_S=0.001` | Looked fine, but step 3 was "free up space by removing or archiving old data or logs": the conditions were gone | embedding span in error, `error.type = llm_timeout`, after 12 ms. Retrieval `keyword_only`, `embedding_error = llm_timeout`. Documents 78, 81, 83, 79, keyword ranks only: **80 ("Free space") was fifth by keyword, and cut**, while 83, "Name resolution failures > Check the resolver", came in from another runbook | `embedding_requests_total{outcome="llm_timeout"}` and `retrieval_duration_seconds_count{mode="keyword_only"}` rise; `RetrievalDegraded` after 15 minutes; a warning per question: "question not embedded" |
| `RAG_CONTEXT_CHUNKS=1` | "Delete obsolete logs, **backups**, or other unnecessary data": invented, and dangerous | `gen_ai.retrieval.top_k = 1`, one hit (81, Expand the volume), `app.prompt.sections = 1` | **nothing**: no metric records the context size |
| `LLM_MAX_OUTPUT_TOKENS=60` | Stopped after "1. Identify the filesystem that is full" | `max_tokens = 60`, output 60 tokens, `finish_reasons = ["length"]`: 19 reasoning chunks, 32 answer chunks. The request: `app.chat.outcome = truncated` | `llm_requests_total{outcome="truncated"}`; `LLMAnswersTruncated` |
| `EMBEDDING_DOCUMENT_PREFIX="document: "` | Skipped how to free space altogether | retrieval `keyword_only`, `embedding_error = no_current_vectors`. `app.retrieval.embedding_key` ends `093387a7`, not `22a337d2`: **the key changed**, so a setting in it changed | `retrieval_duration_seconds_count{mode="keyword_only"}`; the warning "run `make reembed`" |

What the four have in common:
- **Three of the four answers looked competent.** Only reading them
  against the runbook showed the missing safety conditions, or the
  invented step. A user under pressure would not.
- **Nothing errored.** Every request answered 200, and every answer ended
  `done`. The one error, the embedding timeout, was handled by design.
- **One incident showed in no metric at all:** a context of one section.
  Only the trace (or the chat's `meta` event) records how many sections
  the model got.
- **Compare against a good trace.** Section 80's absence, and the embedding
  key's new hash, only stand out next to a known-good trace.

## 4. The answers got slow

`LLM_REASONING_EFFORT=high`:
- The model span took 4.56 s, against 1.59 s.
- **The time to first chunk was unchanged** (0.14 s): the provider started
  as fast as ever.
- The first answer token came at 4.10 s, after **707 reasoning chunks**.
  Then 84 answer chunks.
- Output: **800 of 800 tokens**, `finish_reasons = ["length"]`, request
  outcome `truncated`.

**Diagnosis:** slower *and* worse. Thinking took four seconds, and most of
the output budget: the answer was cut mid-step.
`gen_ai.request.reasoning.level = high` on the span names the cause.

## 5. From a failing eval to its trace

Under `RAG_CONTEXT_CHUNKS=1`, `rag-disk-steps` failed 3 runs in 3. One
failing answer's trace had 43 spans, from two services:
- `eval rag-disk-steps`, with three `gen_ai.evaluation.result` events:
  - "contains any of ['wal', 'logrotate', 'backup']": fail, "none found";
  - "cites a section matching '> Free space'": fail, "cited ['Disk full on
    a database host > Expand the volume']";
  - "cites only sections it was given": pass;
- under it, the service's spans:
  - the case's setup: two `POST /runbooks`, each with its embedding call,
    and a `POST /alerts`;
  - then `POST /chat/stream`, whose retrieval had `top_k = 1` and one hit.

**Diagnosis in one trace:** the check says the "Free space" section was
never cited; the retrieval span says it was never given.

## 6. Who can read a trace?

With `TRACE_CONTENT=true`, a finance-lab user and a lab user each asked a
question.

Both traces were readable by anyone who can open Jaeger:
- **the finance user's question, in full:** "Payroll export to the bank
  failed for 1,200 employees; the SFTP password is [redacted]. What do I
  do?". **Redaction removed the password, and left the business fact.**
- **the lab user's question and answer, and the whole prompt:** 2,498
  characters, with the team's runbook sections.

Jaeger has no notion of teams. The service enforces team isolation twice
(the queries, and row-level security); a trace store with content undoes
both, for whoever can read it.

The conventions' three patterns:
1. **No content (the default here):** ids, counts, hashes.
2. **Content on spans:** pre-production only, or a trace store that meets
   the data's own rules.
3. **Content in a separate store under its own access control,** with
   references on the spans.

The retrieved sections already follow the third: their ids are on the
span, their text stays in Postgres under row-level security. Questions
and answers are not stored at all today. Keeping them is a product and
privacy decision (the PRD's open question on answer feedback), not a
tracing setting.

## 7. What does sampling keep?

- **Ten questions a minute:** the chat allows one user ten a minute. Of 20
  questions asked in a row, 10 answered, and 10 got a 429.
- **At 10%:**
  - of 3 questions, `lab ask` printed a trace id for 1, and `None` for 2;
  - of 500 alert-list requests, Jaeger kept 53 traces (10.6%).
- **Unsampled requests print no id.** A dropped trace still has a valid
  id; the first version handed it out, and at 10% nine ids in ten led
  nowhere. The chat's `meta` and the log lines now carry an id only when
  the trace is kept.

If one question in twenty fails:
- 1,000 questions hold 50 failures;
- **head sampling at 10% keeps about 5 of them,** because it decides
  before anyone knows the trace will fail.

To keep every failure, decide at the end: **tail sampling**, in an
OpenTelemetry Collector (keep errors and slow traces, plus a share of the
rest). Not built here 📘.

The cost of keeping everything, measured: see the AI observability chapter.
