# triage-assistant

AI Ops / incident triage assistant: an API that ingests alerts into
Postgres, owned by teams, and a streaming chat UI for asking an LLM about
them. People sign in with the organisation's identity provider (OIDC) and
see only their teams' alerts; so does the assistant answering them, which
gives the steps from the team's own runbooks and cites each section. It is also
the team's reference template for taking an AI idea to a production-shaped
service — see [`gold_standard_development_guide.md`](gold_standard_development_guide.md).
The project on one page: [`docs/overview.md`](docs/overview.md). Starting
a new service from it: [using this template](docs/handbook/using-this-template.md).

## Quickstart

Requires Docker (Engine on Linux, Docker Desktop or an alternative on
macOS/Windows) and GNU make.

```
make setup     # creates .env from .env.example, builds images
make up        # dev stack with hot reload, a local identity provider (Keycloak)
make ps        # every service should be (healthy)
curl localhost:8010/health
open http://localhost:5173   # Sign in: alice, bob, carol or dave, password DEMO_USER_PASSWORD in .env
```

`make` lists every other command.

On a server: `docs/runbooks/demo-vm.md` (what to ask IT for, bootstrap
with `infra/vm/cloud-init.yaml`, deploy, backups, HTTPS).

## A real model, and evals

Out of the box the assistant answers with a mock model. For a real one,
point `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in `.env` at any
OpenAI-compatible provider, or run a model on your own GPU (the `ollama`
profile, see `.env.example`). `make evals` then measures the answers:
grounded in the alerts and runbooks, refusing what they do not say,
resisting prompt injection, never crossing teams
([AI engineering](docs/handbook/ai-engineering.md)).

Runbook search needs an embedding model (`EMBEDDING_MODEL`; the mock
has one). How it works, and what was measured: [RAG](docs/handbook/rag.md).

`make obs-up` starts the monitoring stack and turns tracing on: each
answer is a trace in Jaeger (http://localhost:16686), from retrieval to
the model's tokens, with no question or answer text on it
([AI observability](docs/handbook/ai-observability.md)).

Every change to what the assistant reads (runbooks, alerts) and every
question it is asked is recorded in an audit trail the api cannot
rewrite: who wrote which version, and which versions each answer was
given (`make audit`; [AI security](docs/handbook/ai-security.md)).

`CHAT_MODE=agent` lets the model choose what to read instead, through two
read-only tools called with the asker's rights, each call audited. An MCP
server offers the same tools to a local client such as Claude Code
([Agents](docs/handbook/agents.md)).

## Structure

```
apps/api/            FastAPI service (Python 3.13, uv); apps/api/evals: the model's evals, the retrieval benchmark
apps/web/            React + Vite UI (Node 24); nginx config for production
tools/               mock LLM provider, the TLS edge image
tests/               end-to-end (Playwright) and load tests
infra/               monitoring as code, Postgres roles, the demo identity realm (Keycloak), VM bootstrap (cloud-init)
scripts/             deploy, backup/restore, failure drills
compose.yaml         services shared by every environment
compose.override.yaml  dev: hot reload, ports on 127.0.0.1 (auto-merged)
compose.prod.yaml    production shape: prod images, only the TLS edge published
Makefile             the single entry point for commands
docs/                handbook, runbooks, ADRs, PRD/RFC/design-doc templates
```

How it is built, tested, shipped and operated, with every measurement
and trap: [`gold_standard_development_guide.md`](gold_standard_development_guide.md)
and the chapters in [`docs/handbook/`](docs/handbook/).

## Contributing

See `CONTRIBUTING.md`. Instructions for AI coding tools are in `AGENTS.md`.
