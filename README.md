# triage-assistant

AI Ops / incident triage assistant: an API that ingests alerts into
Postgres, owned by teams, and a streaming chat UI for asking an LLM about
them. People sign in with the organisation's identity provider (OIDC) and
see only their teams' alerts; so does the assistant answering them. It is also
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
grounded in the alerts, refusing what they do not say, resisting prompt
injection, never crossing teams ([AI engineering](docs/handbook/ai-engineering.md)).

## Structure

```
apps/api/            FastAPI service (Python 3.13, uv); apps/api/evals: the model's evals
apps/web/            React + Vite UI (Node 24); nginx config for production
tools/               mock LLM provider, profiler and load-tool images
tests/               end-to-end (Playwright) and load tests
infra/               monitoring as code, Postgres roles, the demo identity realm (Keycloak), VM bootstrap (cloud-init)
scripts/             deploy, backup/restore, failure drills, fresh-host test
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
