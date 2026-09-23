# AGENTS.md — triage-assistant

## Project overview
AI Ops / Incident Triage Assistant. FastAPI backend (`apps/api`),
Postgres + PgBouncer + Valkey (Redis protocol), a streaming chat backed by
any OpenAI-compatible model, React + Vite frontend (`apps/web`), all run
with Docker Compose. The AI-specific logic is `apps/api/app/triage.py`
(prompt + streaming); the provider seam is `apps/api/app/llm.py`.
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
`mock` and `idp` profiles); `.env` (created from `.env.example`)
provides local settings. Compose is split in three: `compose.yaml` (base),
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
  edge on `EDGE_HTTPS_PORT`; nginx on loopback `HTTP_PORT`). `make
  acme-test` rehearses automatic certificates against Pebble.
- Tests: `make test` (full suite + coverage gate in a throwaway stack, the
  same command CI runs), `make test-fast` (unit only, seconds)
- Types: `make typecheck` (mypy strict + tsc). Before pushing: `make check`
- Browser tests: `make prod-up && make e2e` (Playwright, over HTTPS through the TLS edge;
  signs in through Keycloak's page once, `tests/e2e/specs/auth.setup.ts`)
- Sessions for scripts: `make session [groups="team:default:admin org:admin"]` prints a
  cookie; `make revoke email=...` ends a user's sessions (add `ENV=prod`)
- Load tests (production stack, rate limits raised):
  `ALERTS_RATE_LIMIT=1000000 CHAT_RATE_LIMIT=1000000 make prod-up`, then
  `make seed n=1000000 ENV=prod`, `make load s=alerts-read|chat|health`,
  `make load-compare` (same scenario through six tools), `make load-tool TOOL=locust`.
  Profile a live worker: `make py-spy-dump` / `py-spy-top` / `py-spy-record`.
- Monitoring: `make obs-up` (Prometheus :9090, Grafana :3000, Alertmanager
  :9093 on localhost); `make obs-check` validates configs, unit-tests the
  alert rules (`infra/observability/prometheus/alerts.test.yml`) and checks
  the dashboard JSON matches its generator. Dashboard changes go in
  `infra/observability/grafana/build_dashboard.py`, then `make dashboard`.
  New metric labels must be bounded (route templates, never raw paths or
  user input).
- Debugging: `make debug-up` (breakpoints from VS Code, `.vscode/launch.json`),
  `make trace id=<request id>` (one request across nginx and the api),
  `make db-activity` / `db-locks` / `db-top-queries`, `make netshoot`,
  `make tcpdump`, `make strace` (add `ENV=prod` for the production stack).
- Releases and hosts: a `vX.Y.Z` tag on main publishes the api, web and edge images (amd64 + arm64) to GHCR
  (`.github/workflows/release.yml`); a host runs `make deploy tag=X.Y.Z`
  (rolling: the new api is healthy before the old one drains). `make
  backup` / `make restore file=...` (add `ENV=prod`); `make fresh-host-test`
  proves the committed tree comes up on a clean Docker host. Runbook:
  `docs/runbooks/demo-vm.md`. Never hardcode a container name: after a
  rolling deploy the api is `api-2`, `api-3`... - use `docker compose ps -q api`.
- Failure drills: `make drills` injects each fault into the production stack
  (rate limits raised, as for load tests) and records what users see;
  `make drills d="db-freeze deploy"` runs a selection. Run the database
  drills after upgrading asyncpg, SQLAlchemy or PgBouncer (ADR-0010).
- Mock LLM behaviour: `make mock` shows its config and counters;
  `make mock c='{"fail_mode": "http_429"}'` / `c='{"tokens_per_s": 5}'`
  changes it; `make mock c=reset` restores defaults
- Schema change: edit `app/models.py`, then `make migration m="..."`,
  review the generated file (autogenerate misses renames and some
  constraint changes), `make migrate`

## Docs — when to write which

See `docs/README.md` for the full picture. Short version: **PRD**
(`docs/prd/`) for what/why before building; **RFC** (`docs/rfc/`) for
should-we/how, before a non-trivial change; **Design Doc**
(`docs/design-docs/`) for the detailed technical plan once an RFC is
accepted; **ADR** (`docs/adr/`) for any decision that would confuse
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
  never give the assistant data the asker could not read.
- Never log tokens, cookies, authorization codes or alert text; log the
  user's id, not their email.
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
