# AGENTS.md — triage-assistant

## Project overview
AI Ops / Incident Triage Assistant. FastAPI backend (`apps/api`),
Postgres + PgBouncer + Redis for data, React+Vite frontend (`apps/web`),
all deployed via Docker Compose locally. See `docs/adr/0001-standard-project-shape.md`
for the reasoning behind this repo's shape — it's the template every
project this team builds should follow.

## Development environment
`make setup && make up` brings up `api` + `pgbouncer` + `db` + `redis`
(Valkey) + `web` with hot reload; `.env` (created from `.env.example`)
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
- Production-shaped stack locally: `make prod-up` (nginx on `HTTP_PORT`)
- Tests: `make test` (full suite + coverage gate in a throwaway stack, the
  same command CI runs), `make test-fast` (unit only, seconds)
- Types: `make typecheck` (mypy strict). Before pushing: `make check`
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

## What agents should NOT do
- Don't modify `infra/` without being explicitly asked.
- Don't add a new third-party service/dependency without flagging it in
  the PR description first.
- Don't edit a merged `docs/adr/*.md` file — write a new ADR that
  supersedes it instead.
