# AGENTS.md — triage-assistant

## Project overview
AI Ops / Incident Triage Assistant. FastAPI backend (`apps/api`),
Postgres + PgBouncer + Valkey (Redis protocol), a streaming chat backed by
any OpenAI-compatible model, React + Vite frontend (`apps/web`), all run
with Docker Compose. The AI-specific logic is `apps/api/app/triage.py`
(prompt + streaming); the provider seam is `apps/api/app/llm.py`; runbook
retrieval (hybrid search in Postgres with pgvector, ADR-0017) is
`apps/api/app/runbooks.py`; credentials are redacted before any model call
by `apps/api/app/redact.py`; who wrote what the assistant reads, and what
it was given for each question, is recorded by `apps/api/app/audit.py`
(ADR-0019). `CHAT_MODE=agent` answers with a bounded tool-calling loop
instead (`app/agent.py`), over read-only tools (`app/tools.py`) that
`app/mcp_server.py` also serves over MCP (ADR-0020).
Locally the model is `tools/mock-llm` (OpenAI-compatible, tunable
latency and failure modes). Sign-in is OIDC against the organisation's
identity provider (locally the `keycloak` service, demo users alice, bob,
carol, dave): `app/oidc.py` (protocol), `app/sessions.py` (cookie →
principal), `app/access.py` (roles: viewer < responder < admin per team,
org admin). ADR-0013. See `docs/adr/0001-standard-project-shape.md`
for the reasoning behind this repo's shape — it's the template every
project this team builds should follow.

## Development environment
`make setup && make up` brings up `api` + `pgbouncer` + `db` + `redis`
(Valkey) + `web` with hot reload, plus `mock-llm` and `keycloak` (the
`mock` and `idp` profiles); `.env` (made from `.env.example` by `make
setup`, every secret generated) provides local settings. Compose is split in three: `compose.yaml` (base),
`compose.override.yaml` (dev, merged automatically), `compose.prod.yaml`
(production shape, `make prod-up`). Toolchains live in the containers:
Python 3.13 + uv for `apps/api`, Node 24 for `apps/web`.

## Build & test commands
Run `make` to list every target. The ones you need most:
- Start / stop the dev stack: `make up` / `make down`
- Lint + format check: `make lint`; auto-format: `make fmt`
- Add a dependency: `make deps-api p=<pkg>` / `make deps-web p=<pkg>`
- After any dependency change: `make rebuild` — NOT `docker compose up
  --build`, which keeps the old `.venv`/`node_modules` anonymous volume
- Production-shaped stack locally: `make prod-up` (HTTPS through the TLS
  edge on `EDGE_HTTPS_PORT`; nginx on loopback `HTTP_PORT`).
- Tests: `make test` (full suite + coverage gate in a throwaway stack, the
  same command CI runs), `make test-fast` (unit only, seconds)
- Types: `make typecheck` (mypy strict + tsc). Before pushing: `make check`
- Supply chain (ADR-0022, CI's `security` job): `make secrets-scan`
  (gitleaks, every commit), `make scan` (Trivy: the production images and
  the web's runtime dependencies; a fixable HIGH or CRITICAL fails),
  `make scan-compose` (the compose files' images, weekly). Base images
  are pinned by tag and digest: change both, or let Dependabot do it.
- Browser tests: `make prod-up && make e2e` (Playwright, over HTTPS through the TLS edge;
  signs in through Keycloak's page once, `tests/e2e/specs/auth.setup.ts`)
- Sessions for scripts: `make session [groups="team:default:admin org:admin"]` prints a
  cookie; `make revoke email=...` ends a user's sessions (add `ENV=prod`)
- The assistant's off switch (ADR-0024): `make assistant [off="why" | on=1]`
  (add `ENV=prod`); org admins have it in the UI (`PUT /api/assistant`).
  Runbook: docs/runbooks/turn-the-assistant-off.md
- Personal data (docs/privacy.md, ADR-0025): `make retention [apply=1]`,
  `make user-export email=...`, `make user-forget email=... [yes=1]` (add
  `ENV=prod`); dry runs unless told. A new table, log field or span
  attribute holding personal data gets a row in docs/privacy.md, and a
  retention.
- Audit trail: `make audit a="--action runbook.saved --target 17"` (add
  `ENV=prod`); `make audit-prune days=N` deletes older events as the schema
  owner (the api cannot). Redaction's score: `python -m evals.redaction` in
  the api container; `tests/unit/test_redaction_corpus.py` fails if a
  change catches less (docs/handbook/ai-security.md)
- Cost: tokens per answer and where they go, cost per 1,000 questions,
  self-hosted throughput: docs/handbook/ai-cost.md.
  `OLLAMA_NUM_PARALLEL` sets how many answers the local model batches
- Agents and MCP (docs/handbook/agents.md): `CHAT_MODE=agent` switches the
  chat to the agent; compare it with the pipeline by running `make evals
  a="--target api --judge ..."` in each mode. The tools over MCP, for a
  local client: `python -m app.mcp_server`, with `TRIAGE_SESSION` set to a
  `make session` cookie
- Load tests (production stack, rate limits raised):
  `ALERTS_RATE_LIMIT=1000000 CHAT_RATE_LIMIT=1000000 make prod-up`, then
  `make seed n=1000000 ENV=prod`, `make load s=alerts-read|chat|health` (k6).
- Monitoring: `make obs-up` (Prometheus :9090, Grafana :3000, Alertmanager
  :9093, Jaeger :16686 on localhost; it turns tracing on, `make obs-down`
  off); `make obs-check` validates configs, unit-tests the
  alert rules (`infra/observability/prometheus/alerts.test.yml`) and checks
  the dashboard JSON matches its generator. Dashboard changes go in
  `infra/observability/grafana/build_dashboard.py`, then `make dashboard`.
  New metric labels must be bounded (route templates, never raw paths or
  user input).
- Debugging: `make debug-up` (breakpoints from VS Code, `.vscode/launch.json`),
  `make trace id=<request id>` (one request across nginx and the api, then
  its Jaeger link; a worse answer, diagnosed from its trace:
  docs/handbook/ai-observability.md),
  `make db-activity` / `db-locks` / `db-top-queries` (add `ENV=prod` for
  the production stack).
- Releases and hosts: a `vX.Y.Z` tag on main publishes the api, web and edge images (amd64 + arm64) to GHCR
  (`.github/workflows/release.yml`); a host runs `make deploy tag=X.Y.Z`
  (rolling: the new api is healthy before the old one drains). `make
  backup` / `make restore file=...` (add `ENV=prod`). Runbook:
  `docs/runbooks/demo-vm.md`. Never hardcode a container name: after a
  rolling deploy the api is `api-2`, `api-3`... - use `docker compose ps -q api`.
- Failure drills: `make drills` injects each fault into the production stack
  (rate limits raised, as for load tests) and records what users see;
  `make drills d="db-freeze deploy"` runs a selection. Run the database
  drills after upgrading asyncpg, SQLAlchemy or PgBouncer (ADR-0010).
- Evals (the model's answers, not the code; `apps/api/evals`, docs/handbook/ai-engineering.md):
  `make evals` runs them in the dev api against `LLM_*` (quality mode). Before
  merging any change to `SYSTEM_PROMPT` or the model: `make evals a="--judge
  --judge-model gemma3:27b --repeat 10 --baseline evals/baselines/gpt-oss-20b.json"`,
  with the before/after numbers in the PR. `a="--calibrate-judge ..."` checks the
  judge against labelled answers. A real model locally: add `ollama` to
  `COMPOSE_PROFILES`, `make ollama-pull m=gpt-oss:20b`, point `LLM_*` at it
  (`.env.example`). CI runs plumbing mode with the mock.
- Runbook search (RAG, docs/handbook/rag.md): after changing `EMBEDDING_MODEL`,
  `EMBEDDING_DIMENSIONS` or `EMBEDDING_DOCUMENT_PREFIX`, run `make reembed` (add
  `ENV=prod`). Before merging a retrieval or embedding change: `make evals
  a="--target retrieval"` (recall@k, MRR), before/after in the PR. A team
  filter under the vector index, measured: `make bench-rag-filter`.
- Mock LLM behaviour: `make mock` shows its config and counters;
  `make mock c='{"fail_mode": "http_429"}'` / `c='{"tokens_per_s": 5}'`
  changes it; `make mock c=reset` restores defaults
- Schema change: edit `app/models.py`, then `make migration m="..."`,
  review the generated file (autogenerate misses renames and some
  constraint changes), `make migrate`

## Docs — when to write which

See `docs/README.md` for the full picture. Short version: **PRD**
(`docs/prd/`) for what/why before building; **RFC** (`docs/rfc/`) for
should-we/how, with the technical plan, before a non-trivial change;
**ADR** (`docs/adr/`) for any decision that would confuse
someone later if left unexplained — write one whenever you make an
irreversible or non-obvious call (a new dependency, a schema choice, a
deployment pattern), even if nobody asked for it.

## Code style
- Python: ruff defaults, type hints on new functions.
- Commit messages: Conventional Commits (`feat:`, `fix:`, `chore:`...).
- No commented-out code or teaching-scaffold comments in merged code —
  this is a real project repo, not the practice workspace.

## Testing instructions
Every new endpoint needs tests for its success path and its failure
paths (validation, not found, dependency down). Unit tests go in
`apps/api/tests/unit` (no services), anything touching Postgres/Redis in
`apps/api/tests/integration`. Coverage below 85% fails CI. Don't report a
task complete without a green `make check`.

## Contribution conventions
Branch naming: `type/<issue-number>-<short-desc>`. PRs reference an
issue (`Closes #N`). Squash merge only. See `CONTRIBUTING.md`.

## Security considerations
Never commit secrets. `.env` is gitignored; `.env.example` is the
template. Any new third-party dependency needs a one-line justification
in the PR description.

Production is safe by default (ADR-0023): `APP_ENV=prod` changes some
defaults and refuses to start on unsafe settings (`production_problems`
in `app/config.py`). A new setting that is unsafe in production gets a
check there and a test in `tests/unit/test_config.py`, not a checklist
line. `.env.example` waives only the three checks a local production-
shaped stack needs (`PROD_CHECKS_WAIVED`); never add to them.

Access control, for every change that touches data:
- Every data route takes `principal: CurrentUser` and filters by
  `principal.team_ids()`, or reuses `app/queries.py`. Writes check
  `require_role(...)`. A team or id from the request body is never
  trusted without that check.
- What the caller cannot see is a 404, never a 403 that confirms it
  exists.
- Postgres enforces the same rule with row-level security on `alerts`
  (ADR-0014). A new table holding team data gets `ENABLE ROW LEVEL
  SECURITY`, policies reading `app.*_team_ids`, and database-level tests
  (`test_row_level_security.py`), in the same migration.
- The model's context comes from the same visibility query as the list:
  never give the assistant data the asker could not read. Runbook search
  filters by the caller's teams explicitly (a plain `team_id = ANY(...)`):
  under an approximate vector index, a filter left to row-level security
  alone can return nothing (docs/handbook/rag.md).
- Never log tokens, cookies, authorization codes or alert text; log the
  user's id, not their email. The same holds for trace spans (ADR-0018):
  ids, counts and hashes, never questions, prompts, answers or document
  text. Content goes on spans only behind `TRACE_CONTENT`.
- Changing `SYSTEM_PROMPT` means bumping `PROMPT_VERSION` and recording its
  hash (`tests/unit/test_tracing.py`), with the eval runs before and after.
- A write that changes what the assistant reads (runbooks, alerts, a new
  source) records an audit event with `await record(...)` (`app/audit.py`)
  in the same transaction, before its commit. Never `db.add(AuditEvent(...))`:
  row-level security refuses an insert that reads its row back. Audit
  events hold ids and hashes, never text.
- A regular expression run on text others write (alerts, runbooks,
  questions) has every scan bounded, and a test that hostile input stays
  linear (`test_hostile_text_is_redacted_in_linear_time`).
- Every path that calls a chat model checks the off switch first
  (`switch.current`, as `routes/chat.py` does): a new one refuses with
  503 `assistant_disabled` while it is off, and counts `chat_refusals`.
- Tools live in `app/tools.py`, shared by the agent and the MCP server,
  under docs/handbook/ai-security.md's rules: they act with the asker's
  rights, take the team from the session, ask the person before changing
  anything, and are audited. A tool name comes from the model: through
  `tools.label()` before it reaches a metric, a span or a log.
- Tests: `sign_in_as("team:<slug>:<role>")` for a signed-in client;
  cover 401, 404 for another team, 403 for a role too low, and
  `csrf_failed` for writes (`tests/integration/test_access.py`).
- Scripts calling the api need a session: `make session` (and `Origin:
  <PUBLIC_URL>` on POSTs).

## What agents should NOT do
- Don't modify `infra/` without being explicitly asked.
- Don't add a new third-party service/dependency without flagging it in
  the PR description first.
- Don't edit a merged `docs/adr/*.md` file — write a new ADR that
  supersedes it instead.
- Don't make a scan pass by exempting its finding. An entry in
  `.trivyignore.yaml` needs a statement (why, who accepted) and an expiry
  at most 90 days out; a line in `.gitleaks.toml` or `.gitleaksignore`
  needs the value shown to be fake. A real secret is rotated first.
- Don't weaken, delete or re-label an eval case, a calibration answer or a
  retrieval question to make a run pass. A failing case is a claim to investigate: read the
  answer and the judge's reason, and change a check only when the check is
  shown wrong, in its own commit, saying why in the case's `notes`.
