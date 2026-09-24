# AI observability lab

An answer got worse, or slower, and nobody changed the code. Metrics say
something moved; a trace says what one answer was given and where its time
went. This lab has you diagnose AI failures from telemetry alone: traces
first, then metrics and logs.

**Do each exercise before reading [answers.md](answers.md):** it holds what
was observed here, and the diagnosis.

## Setup, once

1. The RAG lab's setup: a local model and embeddings, and its runbooks
   seeded into team "lab" ([labs/rag-debugging](../rag-debugging/README.md),
   "Setup, once", steps 1 to 3). Exercises 1, 2, 6 and 7 also work with the
   mock model; the others need the real one to show a worse answer.
2. The observability stack, which turns tracing on:

   ```
   make obs-up
   ```

   Jaeger is at http://localhost:16686. On a host that already runs the
   production stack's monitoring, give the dev one other ports:
   `GRAFANA_PORT=3001 PROMETHEUS_PORT=9091 ALERTMANAGER_PORT=9094 make obs-up`.

The helper, `labs/ai-observability/lab`:

| Command | Does |
|---|---|
| `lab ask "<question>" [--as team:lab:viewer]` | asks as a member of that team; prints the answer, what retrieval did, and the trace id |
| `lab trace <trace id> [--all]` | the trace as a tree: each span, when it started, how long it took, the attributes that matter |
| `lab incident [slow]` | changes one setting of the api, without saying which |
| `lab reveal` | says which |
| `lab fix` | puts the api back to your `.env` |
| `lab set VAR=value...` | runs the api with these settings on top of `.env`, tracing kept on; `lab set` alone undoes it. A plain `docker compose up -d api` would turn tracing off: `make obs-up` set its endpoint |

## The method: four questions to ask a bad answer's trace

1. **What was the model given?** The request span's `app.prompt.*`
   (alerts, sections, redactions), and retrieval's documents: which
   sections, by id, and how each retriever ranked them.
2. **How was it asked?** The model span: `gen_ai.request.model`,
   `gen_ai.prompt.version`, `max_tokens`, reasoning level.
3. **What came back?** Tokens, `finish_reasons`, reasoning chunks against
   answer chunks, the request's `app.chat.outcome`.
4. **Where did the time go?** Each span's duration; on the model span, the
   time to first chunk, and the "first token" event.

Then compare with a good answer's trace: Jaeger's **Compare** view puts two
side by side.

## The exercises

**1. Where does the time go?** Ask the same question twice, a few seconds
apart, after your models have been idle for five minutes:

```
lab ask "db-1's disk is filling up. What do I do?"
lab trace <the trace id it prints>
```

How long did retrieval take, and how much of it was the embedding call?
The model's time to first chunk, and its first token: what happened in
between? Why is the first ask so much slower than the second?

**2. From a log line to its trace, and back.** Take the request id from a
trace's request span (`app.request_id`), and run `make trace id=<it>`.
What do the log lines add that the trace does not? What does the trace add
that the log lines do not?

**3. The answers got worse.** Ask your question and keep its trace id.
Then:

```
lab incident
```

Ask the same question again. The answer may still look fine: read it
against the runbook (`apps/api/evals/runbooks/platform/disk-full.md`).
Find what changed, from telemetry only:
- the trace, against your first one (Jaeger → Compare);
- the metrics: `curl -s localhost:8010/metrics | grep -E "retrieval|embedding|llm_requests"`;
- the logs: `docker compose logs --since 5m api | grep -v app.access`.

Write down your diagnosis, then `lab reveal` and `lab fix`. Repeat until
you have seen all four incidents.

**4. The answers got slow.** `lab incident slow`, ask again. Which span
grew? Where inside it? What does that do to the answer?

**5. From a failing eval to its trace.** With an incident running (say, the
one exercise 3 found the most dangerous), run one case through the service:

```
make evals a="--target api --case rag-disk-steps --repeat 3"
```

Take a failing answer's `trace_id` from the report
(`apps/api/evals/reports/`), and `lab trace` it. What do the
`gen_ai.evaluation.result` events say, and does the rest of the trace
explain it?

**6. Who can read a trace?** Recreate the api with content capture on:

```
lab set TRACE_CONTENT=true
lab ask "Payroll export to the bank failed; the SFTP password is Pay2026-Secret. What now?" --as team:finance-lab:viewer
lab ask "db-1's disk is filling up. What do I do?"
```

Open both traces. Whose question, answer and prompt can you read? What did
redaction remove, and what did it not? What would it take to keep content
in production (the conventions name three patterns)? Then `lab set` to
turn capture off.

**7. What does sampling keep?** Keep 10% of traces:

```
lab set OTEL_TRACES_SAMPLER=parentbased_traceidratio OTEL_TRACES_SAMPLER_ARG=0.1
```

Ask ten questions (the chat allows one user ten a minute), then count the
chat requests in Jaeger. What do `lab ask`'s trace ids say for the others?
If one question in twenty fails, how many failing traces do you keep per
thousand questions? What would keep all of them? Then `lab set`.

When you are done: `lab fix`, and `make obs-down` to stop tracing.
