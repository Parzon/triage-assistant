# Code map

One line per file: what it is for. ★ marks the level 1 path in
[START_HERE](../START_HERE.md): how one question gets answered, in the
order it happens. Lockfiles, eval case data and images are left out.

## The api (`apps/api/`)

| File | What it is for |
|---|---|
| `Dockerfile` | the api's images: `dev` (hot reload, your UID) and `production` (non-root, no pip, bytecode precompiled) |
| `pyproject.toml` | dependencies, and the ruff, mypy, pytest and coverage settings |
| `gunicorn.conf.py` | the production server: one worker per CPU the container may use, graceful drain, metrics per worker |
| `alembic.ini` | Alembic's settings; the migrations themselves are in `migrations/` |

### `app/`: the service

| File | What it is for |
|---|---|
| `asgi.py` | the entrypoint gunicorn and uvicorn load; process-wide setup (logging, tracing) |
| `main.py` | the application factory: creates the engine, clients and settings in the lifespan, mounts the routes |
| `config.py` | every setting, read from the environment and checked at startup; production refuses unsafe ones (ADR-0023) |
| `routes/chat.py` ★ | `POST /chat/stream`: checks the off switch and the rate limit, gathers the context, streams the answer |
| `queries.py` ★ | the reads shared by routes: the alert list and the chat's context use the same visibility rule |
| `runbooks.py` ★ | runbooks split into sections, embedded, and searched (full-text and vector, the asker's teams only) |
| `redact.py` ★ | removes credentials from alerts, runbooks and questions before any model sees them |
| `triage.py` ★ | the prompt (`SYSTEM_PROMPT`), what the model is given, and its stream turned into client events |
| `llm.py` ★ | the seam to any OpenAI-compatible model: streaming, timeouts, typed errors, token counts |
| `sse.py` ★ | the Server-Sent Events wire format: one JSON line per event, heartbeats |
| `agent.py` | `CHAT_MODE=agent`: the model chooses what to read, through the tools, in a bounded loop (ADR-0020) |
| `tools.py` | the two read-only tools (`list_alerts`, `search_runbooks`), acting as the asker, audited |
| `mcp_server.py` | the same tools over MCP, for another assistant to call |
| `switch.py` | the assistant's off switch: one row in Postgres, read before every model call (ADR-0024) |
| `access.py` | who may do what: roles per team, org admins, no I/O (ADR-0013) |
| `oidc.py` | signing in with the organisation's identity provider: code flow with PKCE, ID token checks |
| `sessions.py` | the session cookie → the signed-in user and their teams, in one query |
| `db.py` | the database engine and per-request sessions, through PgBouncer; tells Postgres who is asking (row-level security) |
| `models.py` | the tables, as SQLAlchemy models; every change needs a migration |
| `schemas.py` | request and response bodies: the public API contract |
| `vector.py` | pgvector's column type for SQLAlchemy, without the pgvector package |
| `audit.py` | the append-only audit trail: who wrote what the assistant reads, what each question was given (ADR-0019) |
| `privacy.py` | retention, and a person's export and erasure (ADR-0025) |
| `ratelimit.py` | per-user limits shared by every worker, in Valkey; fails open, or closed for the chat in production |
| `errors.py` | one error shape for every failure, with the request id; database outages as 503 |
| `middleware.py` | per request: the request id, the trace span, the access log line, response headers |
| `logs.py` | JSON log lines on stdout |
| `metrics.py` | the Prometheus metrics, and `/metrics` across gunicorn workers |
| `tracing.py` | OpenTelemetry: a trace per request, the GenAI attributes, the sampler |
| `cli.py` | operator commands behind make: sessions, revoke, reembed, audit, the off switch, retention, export, erasure |
| `routes/alerts.py` | alerts: create, list (paged, by severity), read one, and the Alertmanager webhook |
| `routes/runbooks.py` | runbooks: team admins write them, members read and search them |
| `routes/auth.py` | sign-in, the provider's callback, sign-out, `/me` |
| `routes/assistant.py` | read the off switch (everyone), switch it (org admins) |
| `routes/audit.py` | the audit trail, for org admins |
| `routes/health.py` | `/health` (the process is alive) and `/ready` (its dependencies answer) |

### `evals/`: the model's answers, measured

| File | What it is for |
|---|---|
| `__main__.py` | `python -m evals`: runs the cases, prints a report, fails the gate |
| `cases.py` | loads the cases (TOML in `cases/`): a question, its alerts, what a good answer contains |
| `checks.py` | deterministic checks on an answer, then the optional judge |
| `targets.py` | where a question goes: the model directly, or the running service |
| `run.py` | runs the cases; the report; the comparison with a baseline (Fisher's exact test) |
| `calibration.py` | checks the judge against answers a person labelled |
| `retrieval.py` | the retrieval benchmark: recall@k and MRR per search mode |
| `redaction.py` | how much the redactor catches, on a tuning set and a held-out set |
| `cases/`, `runbooks/`, `baselines/`, `*.toml` | the data: cases, the benchmark's runbooks and questions, stored baselines, labelled answers |

### `migrations/`: the schema's history

| File | What it is for |
|---|---|
| `env.py` | how migrations connect: as the schema owner, straight to Postgres, with a lock timeout |
| `versions/*_create_alerts.py` | the alerts table |
| `versions/*_add_alert_external_id.py` | a column and an index added without blocking writes: the pattern to copy |
| `versions/*_index_alerts_by_created_at_id.py` | the index that newest-first reads needed under load |
| `versions/*_teams_users_and_sessions.py` | teams, users, memberships and sessions: the expand half |
| `versions/*_row_level_security_on_alerts.py` | Postgres enforces team isolation: the contract half |
| `versions/*_runbooks.py` | runbooks, their sections and vectors (pgvector) |
| `versions/*_audit_events.py` | the append-only audit table |
| `versions/*_audit_via_mcp.py` | audit events from an MCP client |
| `versions/*_assistant_switch.py` | the off switch's one row, and who may change it |

### `tests/`

| File | What it is for |
|---|---|
| `conftest.py` | one in-memory trace store for both suites |
| `unit/test_access.py` | the authorization model and the sign-in redirect guard |
| `unit/test_agent.py` | the agent's loop and each kind of tool call |
| `unit/test_config.py` | settings validation, and one test per production refusal |
| `unit/test_cursor.py` | the alert list's paging cursor: round trip, and garbage is a 400 |
| `unit/test_errors.py` | which database errors are 503 and which are bugs (500); the validation error shape |
| `unit/test_evals.py` | the eval harness itself: what counts as a pass |
| `unit/test_gunicorn_conf.py` | the worker count follows the container's CPU limit |
| `unit/test_logs.py` | one JSON object per line, with the request id |
| `unit/test_middleware.py` | the request id, the access log and headers, against a tiny app |
| `unit/test_oidc.py` | every ID token check, failed one at a time, against a fake provider |
| `unit/test_probe.py` | the readiness probe answers within its deadline |
| `unit/test_ratelimit.py` | the limiter: limits, windows, failing open, failing closed |
| `unit/test_redact.py` | credentials never reach the model; hostile input stays linear |
| `unit/test_redaction_corpus.py` | the redactor may catch more, never less, and never touch ordinary text |
| `unit/test_runbooks.py` | splitting runbooks, numbering sections, reading citations, the vector format |
| `unit/test_tracing.py` | the sampler, the prompt versions' hashes, trace ids in logs |
| `unit/test_triage.py` | the answer stream: events, heartbeats, errors mid-answer, a cut-off answer |
| `integration/conftest.py` | the real stack: Postgres through PgBouncer, Valkey, signed-in clients (`sign_in_as`) |
| `integration/test_access.py` | who sees and does what, through the API, for every role |
| `integration/test_agent.py` | the agent end to end, with the mock calling both tools |
| `integration/test_alertmanager_webhook.py` | Alertmanager's webhook: stored once, routed by team, token required |
| `integration/test_alerts.py` | create, validate, read, list and page alerts |
| `integration/test_assistant_switch.py` | the off switch: who switches it, what askers see, no model call while off |
| `integration/test_audit.py` | every change to what the assistant reads is recorded, and cannot be rewritten |
| `integration/test_auth_flow.py` | signing in through the test stack's real Keycloak |
| `integration/test_chat.py` | the chat stream against the mock over real HTTP, each failure mode included |
| `integration/test_evals.py` | the eval harness in plumbing mode, as CI runs it |
| `integration/test_health.py` | liveness and readiness with each dependency down |
| `integration/test_mcp.py` | the MCP server through a real MCP client |
| `integration/test_metrics.py` | `/metrics` reflects real traffic |
| `integration/test_privacy.py` | retention, export and erasure against the real database |
| `integration/test_ratelimit.py` | the limiter against real Valkey; the chat failing closed |
| `integration/test_row_level_security.py` | Postgres refuses other teams' rows, around the app |
| `integration/test_runbooks.py` | runbooks through the API: access, saves, search, keyword fallback |
| `integration/test_sessions.py` | sessions: expiry, revocation, the checks on every request |
| `integration/test_tracing.py` | the spans of a real question, and no content on them |

## The web app (`apps/web/`)

| File | What it is for |
|---|---|
| `Dockerfile` | the dev server image, and the production image: the built files in unprivileged nginx |
| `nginx/default.conf` | production nginx: the built app, `/api` proxied without buffering, JSON errors |
| `nginx/snippets/proxy.conf` | shared proxy settings: upstream keep-alive, the request id, timeouts |
| `nginx/snippets/security-headers.conf` | the security headers, included in every location |
| `vite.config.ts` | the dev server (proxies `/api` and `/auth`) and the production build |
| `package.json`, `tsconfig*.json`, `index.html` | dependencies and scripts, TypeScript settings, the page shell |
| `src/main.tsx` | the entry: TanStack Query, and a 401 anywhere sends you back to sign-in |
| `src/App.tsx` | who is signed in decides what renders; loads the off switch's state |
| `src/components/Chat.tsx` ★ | the chat: sends the question, reads the stream, shows citations, Stop |
| `src/lib/sse.ts` ★ | parses the event stream (`EventSource` can't POST) |
| `src/lib/api.ts` | every api call, with one error shape |
| `src/components/RecentAlerts.tsx` | the alert list, polled |
| `src/components/NewAlert.tsx` | create an alert (responders and up) |
| `src/components/AssistantSwitch.tsx` | org admins switch the assistant off, with a reason |
| `src/components/Account.tsx` | who you are, your teams, sign-out |
| `src/components/SignIn.tsx` | the sign-in page, and why a sign-in failed |
| `src/index.css` | the styles: one plain stylesheet |
| `src/**/*.test.ts(x)`, `src/test/setup.ts` | component and unit tests (Vitest, jsdom) |

## Tools, scripts, tests across the stack

| File | What it is for |
|---|---|
| `tools/mock-llm/mock_llm.py` | a fake OpenAI-compatible model with speed and failure knobs (`make mock`) |
| `tools/mock-llm/Dockerfile`, `pyproject.toml` | its image and dependencies |
| `tools/edge/Caddyfile`, `Dockerfile` | the TLS edge: HTTPS, certificates, security headers, holds requests while nginx restarts |
| `scripts/deploy.sh` | `make deploy`: a rolling deploy on one host, and code-only rollbacks |
| `scripts/backup.sh`, `restore.sh` | `make backup` and `make restore` |
| `scripts/failure-drills.sh`, `drills/steady_reads.py` | `make drills`: one fault at a time, what users see |
| `scripts/smoke-release.sh` | the published images, checked on every path (the release runs it) |
| `scripts/new-project.sh` | a fresh copy of this template renamed as your project |
| `scripts/check_settings.py` | every setting reaches the container through compose (`make lint`) |
| `scripts/check_trivyignore.py` | every accepted vulnerability has a reason and an expiry (`make scan`) |
| `scripts/lib/session.sh` | signed-in requests for scripts; the sign-in flow without a browser |
| `scripts/git-hooks/pre-push` | lint, types and unit tests before a push (`make hooks`) |
| `scripts/sql/*.sql`, `seed-alerts.sql` | `make db-activity`, `db-locks`, `db-top-queries`, `bench-rag-filter`, `seed` |
| `tests/e2e/` | browser tests through the production stack: sign-in once (`auth.setup.ts`), access, the chat |
| `tests/load/k6/*.js` | `make load` scenarios: reads, streams, health, open vs paced arrivals |
| `tests/load/stream_client_bench.py` | CPU per streamed chunk: the SDK against a raw client |

## Running it

| File | What it is for |
|---|---|
| `Makefile` | every command (`make`, `make help-all`) |
| `compose.yaml` | every service, shared by all environments |
| `compose.override.yaml` | dev: hot reload, ports on 127.0.0.1 (merged automatically) |
| `compose.prod.yaml` | production shape: release images, limits, read-only, only the edge published |
| `compose.test.yaml` | the throwaway test stack `make test` runs |
| `compose.debug.yaml` | the api under debugpy (`make debug-up`) |
| `.env.example` | every setting, documented; `make setup` copies it with secrets generated |
| `infra/postgres/initdb/10-app-role.sh` | the app's and the monitoring's database roles, with their limits, on a new volume |
| `infra/keycloak/triage-realm.json` | the demo identity provider: users, groups, the client |
| `infra/observability/prometheus/` | scrape config, alert rules, and the rules' tests |
| `infra/observability/alertmanager/alertmanager.yml` | alert routing (to the app itself, as a demo) |
| `infra/observability/grafana/` | the dashboard's generator (`build_dashboard.py`), its output, provisioning |
| `infra/observability/jaeger/config.yaml` | the trace store: OTLP in, 20,000 traces in memory |
| `infra/vm/cloud-init.yaml` | a fresh Ubuntu VM made into a host: Docker, firewall, patches, the checkout, secrets |

## The repository

| File | What it is for |
|---|---|
| `.github/workflows/ci.yml` | every push: lint, tests, build, scans, e2e |
| `.github/workflows/release.yml` | a `vX.Y.Z` tag: scan, build for amd64 and arm64, publish, smoke-test |
| `.github/workflows/scan.yml` | weekly: the compose images and this repo's own, for new vulnerabilities |
| `.github/dependabot.yml` | weekly update PRs: packages, Actions, base images, compose images |
| `.github/CODEOWNERS`, `pull_request_template.md`, `ISSUE_TEMPLATE/` | reviews, and the shape of PRs and issues |
| `.trivyignore.yaml` | accepted vulnerabilities, each with a reason and an expiry |
| `.gitleaks.toml`, `.gitleaksignore` | the secret scan's settings, and fake credentials shown to be fake |
| `.vscode/launch.json`, `extensions.json` | attach VS Code's debugger to the api container; the recommended extensions |
| `.editorconfig`, `.gitattributes`, `.gitignore` | whitespace, line endings (LF), what git ignores |
