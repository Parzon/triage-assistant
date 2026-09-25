# triage-assistant

An AI assistant for people on call. Alerts from monitoring land in one
place, each owned by a team. When something breaks, people ask in plain
words what is failing and what to do. A model answers from their own
teams' alerts and runbooks, and cites the runbook sections it used.

It is also the team's reference template for taking an AI idea to a
production-shaped service.

This page says what the app does and how to use it. The way into the
code is [START_HERE.md](START_HERE.md): what runs, five commands, then the
code in four levels. After that:
- every file, one line each: [the code map](docs/code-map.md);
- every technology, when a new project needs it, and how sign-in works:
  [TECH_STACK.md](TECH_STACK.md);
- the rules and every trap met building it:
  [the guide](gold_standard_development_guide.md);
- the project on one page, for deciding about it:
  [the overview](docs/overview.md);
- a new service from it: [using this template](docs/handbook/using-this-template.md).

## What it does

1. **Alerts come in** from Prometheus's Alertmanager (a webhook), or
   someone types one in. Each belongs to a team.
2. **People sign in** with the organisation's account (OIDC). Its groups
   decide their teams and roles, and they see their teams' alerts only.
3. **They ask** in plain words: "what is failing in payments?",
   "checkout returns 502 since the deploy, what do I do?".
4. **The assistant answers**, word by word as the model writes, from:
   - the newest alerts of the asker's teams;
   - the sections of those teams' runbooks that match the question,
     found by meaning and by keyword.

   It cites the runbook sections it used, and says when they don't cover
   the question.
5. **It advises; people act.** It reads, and changes nothing.

What it never does:
- give the model, or the person asking, another team's alerts or
  runbooks;
- send the model a password or token written in an alert or a runbook:
  those are removed first;
- answer while an org admin has switched it off.

Every question is recorded: who asked, and which alerts and runbook
versions the model was given (ids and hashes, never the text).

Not built yet: a page for runbooks (they are saved through the API, see
[Runbooks](#runbooks)), notifications, and any action on a system.

## Try it

There is no shared instance yet: each person runs it on their own
machine. You need Docker (Engine on Linux, Docker Desktop or an
alternative on macOS/Windows) and GNU make, nothing else.

```
make setup     # once: .env from .env.example, every secret generated; builds the images
make up        # the stack, with hot reload and a local sign-in provider (Keycloak)
make ps        # every service should be (healthy)
```

Open http://localhost:5173 and **Sign in**. Keycloak's page takes one of
four demo users. The password is `DEMO_USER_PASSWORD` in `.env`.

| User | Teams and role | Can |
|---|---|---|
| `alice` | payments: responder; platform: viewer | read and ask about both teams' alerts; create payments alerts |
| `bob` | platform: admin; default: viewer | read and ask about both teams' alerts; create and delete platform alerts; write platform runbooks |
| `carol` | org admin | everything, in every team; switch the assistant off |
| `dave` | none | nothing yet: the page says to ask for a team |

In general: a **viewer** reads and asks, a **responder** also creates
alerts, an **admin** also deletes alerts, writes runbooks and sees the
team's members. In production the organisation's identity provider
replaces these users, and its groups (`team:payments:responder`,
`org:admin`) grant the roles.

### The page

One page, top to bottom:
- **Ask about the alerts:** type a question, then **Ask**. The answer
  appears as the model writes it, and **Stop** ends it.
  - The line under the box says what the model was given (how many
    alerts and runbook sections) and how fast it started.
  - The runbook sections an answer cites are listed under it.
  - A note above the box says the answers are AI-generated and can be
    wrong.
- **The assistant** (org admins only): **Switch off**, with a reason
  everyone sees, and **Switch on**. Off takes effect on every server at
  once; alerts keep working.
- **New alert** (responders and admins): a team, a severity and a
  message.
- **Recent alerts:** your teams' newest 20, refreshed every 5 seconds;
  pick one team if you have several.
- Top right: your name, your role in each team, and **Sign out**.

### Five minutes

1. As **alice**, create an alert: team payments, severity high,
   "checkout returns 502 since the 14:05 deploy".
2. Ask "what is failing in payments?". The line under the box shows
   `1 alerts in context`.

   Out of the box a **mock model** answers: fixed text that counts the
   alerts it was given, and ends with "(mock-llm reply ...)". It proves
   the path, not the advice. For real answers, see [a real
   model](#a-real-model-and-evals).
3. Sign out, and sign in as **bob**: alice's alert isn't there. Payments'
   alerts are not bob's to see, and not the model's either when bob asks.
4. As **carol**, switch the assistant off, with a reason. As alice, ask
   again: the question is refused, and carol's reason is shown. Switch it
   back on.

### Runbooks

A runbook is a team's written procedure, in Markdown. The assistant
searches the runbooks for every question. There is no page for them yet:
a team admin saves one through the API. From a terminal, with the stack
up:

```
cookie=$(make -s session groups="team:payments:admin")
curl -s http://localhost:5173/api/runbooks -H "Cookie: $cookie" \
  -H "Origin: http://localhost:5173" -H "Content-Type: application/json" \
  -d '{"team": "payments", "title": "Checkout errors", "body": "# Checkout errors\n\n## 502s after a deploy\n\nRoll back to the previous release: make deploy tag=<previous>. Then open an incident."}'
```

- `make session` prints a session cookie for scripts, here a payments
  admin's.
- The `Origin` header is the api's check against cross-site requests.
- Saving the same title again replaces the runbook.

Ask alice's question again: the line under the box now counts one runbook
section. With a real model, the answer cites it, and the section is
listed under the answer. More runbooks to try: `apps/api/evals/runbooks/`.

Every endpoint, with a form to try it as the user you signed in as:
http://localhost:5173/api/docs (development only).

### Where it runs

| Where | Address | How |
|---|---|---|
| your machine, for development | http://localhost:5173 | `make up` |
| your machine, in the production shape: HTTPS, production images | https://localhost | `make prod-up`. Expect a certificate warning: the certificate comes from the edge's own local authority, which your browser doesn't know |
| a server | `https://<your domain>` | [the VM runbook](docs/runbooks/demo-vm.md): what to ask IT for, then `make deploy tag=X.Y.Z` |

`make` lists the commands you need first; `make help-all` lists every
one.

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

One line per file: [the code map](docs/code-map.md). How it is built,
tested, shipped and operated, with every measurement and trap:
[`gold_standard_development_guide.md`](gold_standard_development_guide.md)
and the chapters in [`docs/handbook/`](docs/handbook/).

## Contributing

See `CONTRIBUTING.md`. Instructions for AI coding tools are in `AGENTS.md`.
