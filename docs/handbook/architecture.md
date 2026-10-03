# Architecture: the monolith, and when to split it

What the system is made of, why it's one deployable api rather than
services, what would be split off first, and the signal that says when.

## What there is

```
apps/web  (React, built to static files, served by nginx)
    │  /api/*  (nginx strips /api)
    ▼
apps/api  (one FastAPI app)
    routes/auth.py      GET /auth/login, GET /auth/callback, POST /auth/logout, GET /me,
                        GET /teams/{slug}/members
    routes/alerts.py    POST /alerts, GET /alerts (keyset pages), GET /alerts/{id},
                        DELETE /alerts/{id}, POST /alerts/alertmanager (webhook, token)
    routes/chat.py      POST /chat/stream (SSE): the pipeline, or the agent (CHAT_MODE)
    routes/runbooks.py  POST/GET/DELETE /runbooks, POST /runbooks/search
    routes/audit.py     GET /audit (org admins)
    routes/assistant.py GET/PUT /assistant: the off switch (ADR-0024)
    routes/health.py    /health, /ready, /metrics
    triage.py           the prompt and the streaming loop (heartbeats, caps, cancellation)
    llm.py              the provider seam: any OpenAI-compatible API (ADR-0006)
    runbooks.py         sections, embeddings, hybrid search in Postgres (ADR-0017)
    redact.py           credentials out of everything a model is sent
    agent.py, tools.py  the agent's bounded loop and its read-only tools (ADR-0020)
    mcp_server.py       the same tools over MCP, for another assistant
    audit.py            the append-only record of what the assistant read (ADR-0019)
    switch.py           the off switch, one row in Postgres (ADR-0024)
    privacy.py          retention, export and erasure (ADR-0025)
    tracing.py          OpenTelemetry spans, no content by default (ADR-0018)
    oidc.py             the identity-provider seam: OIDC code flow, ID-token checks (ADR-0013)
    sessions.py         sessions in Postgres, the request's principal, the CSRF check
    access.py           roles and who may do what: no I/O, unit-tested alone
    queries.py          reads shared by routes: the visibility rule lives once
    ratelimit.py        per-user fixed windows in Valkey, fail-open (ADR-0004)
    db.py, models.py    SQLAlchemy async, PgBouncer in front (ADR-0005); the tenant
                        context in every transaction
    cli.py              operator commands: sessions for scripts, revoke, the off
                        switch, retention, export, erasure, re-embedding
    middleware.py, errors.py, logs.py, metrics.py, sse.py   the cross-cutting parts
```

The data:
```
teams ─┬─< memberships >── users ──< sessions         login_requests (a sign-in in progress)
       ├─< alerts
       └─< runbooks ──< runbook_chunks (sections, with their vectors)
audit_events (append-only)    assistant_switch (one row)
```
- **teams:** created the first time a sign-in names them.
- **memberships:** the user's role per team, replaced at every sign-in.
- **users:** identified by `(issuer, subject)`.
- **sessions:** the SHA-256 of each cookie's token.
- **alerts, runbooks, runbook_chunks:** each owned by one team. Postgres
  shows the app role only the rows of the teams the transaction names
  (row-level security, ADR-0014); runbook search filters by team
  explicitly as well ([RAG](rag.md)).
- **audit_events:** insert-only for the app role; org admins read them.

It's a **modular monolith**:
- One process type, one deploy, one database.
- The internal boundaries are visible:
  - the AI-specific logic sits behind one seam (`llm.py`): the prompt and
    stream in `triage.py`, retrieval in `runbooks.py`, the agent in
    `agent.py` and `tools.py`;
  - identity is three: `oidc.py` speaks the protocol, `sessions.py`
    turns a cookie into a principal, `access.py` decides;
  - everything else is the standard scaffold every service here should
    share (ADR-0001).

The frontend is a separate artifact because it has a different
toolchain and a different runtime (static files), not because it is a
"microservice".

## Why not microservices now

Splitting has costs, and every one of them was measured in this repo on
a *single* network hop:
- **Every call across a network can fail in ways a function call
  can't.** It can hang, time out, or succeed after the caller gave up.
  The failure drills needed an ADR's worth of work to make one
  dependency (Postgres through PgBouncer) fail cleanly. Each new service
  boundary is another such dependency.
- **Latency adds up.** Every hop needs a connection pool, a timeout and
  its own error mapping: the timeouts chain in ADR-0010 is the sum along
  the path.
- **Operations multiply.** Each service needs its own image, pipeline,
  deploy, dashboard, alerts and runbook.
- **Data ownership gets hard.** A query that joins alerts to anything
  else becomes an API call, or two copies of the data that drift.

The benefits of splitting are independent scaling, independent deploys
and team autonomy. They pay off when there are several teams, or one
component with a very different load profile. Neither is true yet.
**Complexity should follow measured pain.**

## What would be split first, and the signal for each

| Candidate | Split when | How |
|---|---|---|
| **Streaming chat (the LLM path)** | chat load dominates the api's CPU (the SDK costs 126 µs per chunk, so 500 streams saturate 2 CPUs), *or* chat needs a different scaling policy than reads | the same image, a second deployment serving only `/chat/stream` (a route split at the load balancer, no code split). Scale it on active streams |
| **An LLM gateway** | several services call models: shared keys, quotas, cost tracking, provider fallback | a gateway (e.g. LiteLLM) in front of the providers. `llm.py` already speaks the OpenAI API, so only `LLM_BASE_URL` changes |
| **Alert ingestion** | webhook volume or bursts slow down user requests, or ingestion must survive the api being down | a queue (SQS, or a Postgres table used as a queue) plus a worker process from the same codebase |
| **The frontend to a CDN** | global users, or static traffic competing with the api | the build output to object storage + CDN; nginx keeps only the proxy role |

Notice the pattern: the first steps split **deployments of the same
codebase**, not the codebase. That buys independent scaling without
network calls between our own components.

## The scaling path

In order: each step is cheaper than the next and buys a known amount
([operations](operations.md#performance-and-load)).

1. **Fix the query.** The first bottleneck was a missing index (p95 7 s
   → 4 ms). No amount of scaling fixes that.
2. **More CPU for the api.** Workers follow the CPU limit (~530 simple
   signed-in reads/s per core).
3. **More api replicas** behind a load balancer. They are stateless:
   rate limits live in Valkey, sessions in Postgres, so any replica
   serves any request, with no sticky sessions. The connection
   budget sets the limit: 2 workers × 20 per replica against PgBouncer's
   `MAX_CLIENT_CONN` 500, i.e. ~12 replicas before that changes.
4. **Separate chat and read deployments** (above).
5. **Read replicas** for Postgres, once the database, not the api, is the
   measured bottleneck. At 1,710 req/s it used 0.93 of a core, so that
   point is far away.
6. **Queues for bursty writes** (ingestion).

## Decisions that shaped it

The ADRs in `docs/adr/`, one line each:
- **0001:** one standard project shape for every AI service here.
- **0002:** Valkey instead of Redis (licensing).
- **0003:** compose files split by environment, with `make` as the entry
  point.
- **0004:** the rate limiter fails open.
- **0005:** database access: roles, PgBouncer, migrations as a separate
  step.
- **0006:** an OpenAI-compatible seam for the model.
- **0007:** the SSE wire format and cancellation.
- **0008:** the observability stack.
- **0009:** performance defaults from load tests.
- **0010:** database timeouts live on the server side.
- **0011:** releases and rolling deploys.
- **0012:** HTTPS terminates at a Caddy edge.
- **0013:** sign-in with the organisation's identity provider (OIDC,
  server-side sessions); teams own alerts, with ranked roles.
- **0014:** Postgres enforces team isolation (row-level security).
- **0015:** rollbacks roll back the code, never the schema.
- **0016:** evals gate prompt and model changes.
- **0017:** runbook retrieval in Postgres, hybrid, under row-level
  security.
- **0018:** traces with OpenTelemetry; no content by default.
- **0019:** an append-only audit trail of what the assistant reads.
- **0020:** an agent mode beside the pipeline, and its tools over MCP.
- **0021:** trim the template to what a project uses.
- **0022:** scan images and commits; pin base images and scanners by
  digest.
- **0023:** production is safe by default and refuses unsafe settings.
- **0024:** an off switch for the assistant.
- **0025:** personal data: retention, export and erasure.
- **0026:** report third-party image findings; bump base images weekly.
