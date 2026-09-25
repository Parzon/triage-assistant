# Start here

**triage-assistant** is an on-call assistant. Alerts live in Postgres,
owned by teams. People sign in with the organisation's identity
provider and see only their teams' alerts. When they ask a question, a
model answers from those alerts and the team's runbooks, and cites the
sections it used. The repository is also the template this team starts
new services from.

This page is the way in: what runs, five commands, then four levels to
read in order. Each level says what to read, what to run, and what you
can explain once you have done both.

## What runs

```
browser
  │ HTTPS
  ▼
edge (Caddy): certificates ───── /auth ────► Keycloak: the demo identity provider
  ▼                                          (the organisation's own in production)
nginx: the built React app; /api → the api, never buffered
  ▼
api (FastAPI, apps/api/app) ─────────────────► the model: any OpenAI-compatible API
  │                                            (a mock locally; Ollama or a provider for real)
  ├─► PgBouncer ─► Postgres + pgvector: alerts, runbooks and their vectors,
  │                users, sessions, the audit trail, the off switch
  └─► Valkey: rate-limit counters shared by every worker
```

In development (`make up`), Vite's dev server stands in for the edge and
nginx. Monitoring (Prometheus, Grafana, Jaeger) is optional:
`make obs-up`.

## Five commands

You need Docker and make, nothing else: every toolchain runs in a
container.

```
make setup        # once: .env from .env.example, every secret generated; the dev images
make up           # the dev stack, hot reload. Open http://localhost:5173, sign in as alice
                  # (the password is DEMO_USER_PASSWORD in .env) and ask about an alert
make logs S=api   # watch your question go through
make check        # lint, types, every test: what CI runs, before you push
make              # the commands you will need next (make help-all: every one)
```

## Level 1: how one question gets answered

The core, and the one to know by heart. It needs the dev stack
(`make up`) and no real model: the mock answers.

**Read,** in the order a question travels (★ in the [code map](docs/code-map.md)):

```
POST /api/chat/stream
  routes/chat.py    is the assistant on? is the asker within their rate limit?
  queries.py        the asker's teams' newest alerts        ┐ only what the asker
  runbooks.py       the runbook sections that match         ┘ may read
  redact.py         credentials removed
  triage.py         the prompt; the model's stream turned into events
  llm.py            the model, called through the OpenAI-compatible seam
  sse.py            one JSON event per line back to the browser: meta, tokens, done
apps/web/src/components/Chat.tsx, lib/sse.ts    the browser reads the stream
```

**Run:** ask a question as alice; then `make mock c='{"fail_mode":
"http_429"}'`, ask again, and `make mock c=reset`.

**You can explain:** what the model is given, and why never more than the
asker could read; where credentials are removed; why the answer streams,
and what Stop does; what the browser shows when the model fails.

## Level 2: the production shape

The plumbing around the core: know what each piece does, and what goes
wrong without it.

**Read:** `compose.yaml` and `compose.prod.yaml`, `apps/api/Dockerfile`,
`apps/web/nginx/default.conf`, `tools/edge/Caddyfile`,
`apps/api/gunicorn.conf.py`, `scripts/deploy.sh`, `.github/workflows/`.
Then [the tech stack](TECH_STACK.md) and
[production](docs/handbook/production.md).

**Run:** `make prod-up && make e2e`; `make scan`.

**You can explain:** what each container is for, and when a new project
needs it; how one image goes from a tag to a host; how a deploy drops no
request, and a rollback crosses a migration; what production refuses to
start with.

## Level 3: operations

What you need on call.

**Read:** [operations](docs/handbook/operations.md): start with its
symptom-to-tool table, then the failure matrix. Then
[the alert runbook](docs/runbooks/alerts.md) and
[turning the assistant off](docs/runbooks/turn-the-assistant-off.md).

**Run:** `make obs-up`, then follow one request with `make trace
id=<request id>`. The drills: `make drills d="redis-stop db-freeze"`, on
the production stack with its rate limits raised (AGENTS.md has the
command).

**You can explain:** liveness against readiness; what fails open, what
fails closed, and why; how to find why one user's request failed; how to
stop the assistant during an incident.

## Level 4: AI depth

The AI engineering itself: evals, retrieval, agents, security, cost.

**Read:** [AI engineering](docs/handbook/ai-engineering.md) (evals, the
judge, the prompt's history), [RAG](docs/handbook/rag.md),
[agents](docs/handbook/agents.md), [AI security](docs/handbook/ai-security.md),
[AI cost](docs/handbook/ai-cost.md),
[AI observability](docs/handbook/ai-observability.md); then
`apps/api/evals/`.

**Run:** `make evals` (with the mock); then a real model (the `ollama`
profile, see `.env.example`) with `make evals a="--judge --judge-model
gemma3:27b --repeat 10 --baseline evals/baselines/gpt-oss-20b.json"`,
and `make evals a="--target retrieval"`.

**You can explain:** why a prompt change needs repeated runs against a
baseline; what the judge's calibration proves; how runbook search stays
within the asker's teams; when an agent is worth its cost; what the
audit trail records, and why it holds no text.

## Where everything else is

| For | Read |
|---|---|
| every file, one line each | [the code map](docs/code-map.md) |
| every technology, and when a project needs it | [TECH_STACK.md](TECH_STACK.md) |
| the rules, and every trap met building this | [the guide](gold_standard_development_guide.md) |
| a change you make every week: a dependency, an endpoint, a setting, a migration | [daily work](docs/handbook/daily-work.md) |
| the decisions, and why | [docs/adr/](docs/adr/) |
| what to do when an alert fires | [docs/runbooks/](docs/runbooks/) |
| the personal data the service holds | [privacy](docs/privacy.md) |
| how to contribute; instructions for AI coding tools | [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md) |
