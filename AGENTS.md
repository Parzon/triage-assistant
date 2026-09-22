# AGENTS.md — triage-assistant

## Project overview
AI Ops / Incident Triage Assistant. FastAPI backend (`apps/api`),
Postgres + PgBouncer + Redis for data, React+Vite frontend (`apps/web`),
all deployed via Docker Compose locally. See `docs/adr/0001-standard-project-shape.md`
for the reasoning behind this repo's shape — it's the template every
project this team builds should follow.

## Development environment
`docker compose up --build` brings up `api` + `pgbouncer` + `db` +
`redis` + `web`, with `.env` (copy from `.env.example`) providing local
credentials. Nothing needs installing on the host except Docker —
`apps/api` runs `uv` inside its own container, `apps/web` runs Node 20
inside its own container.

## Build & test commands
- Run the whole stack: `docker compose up --build`
- Backend tests: `docker compose exec api uv run pytest` (once tests exist)
- Backend lint: `docker compose exec api uv run ruff check .`
- Frontend build/type-check: `docker compose exec web npm run build`
- Rebuild after a dependency change: `docker compose up --build`

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
Every new endpoint needs at least one test before it's considered done.
Don't report a task complete without a passing local test run.

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
