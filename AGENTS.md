# AGENTS.md — triage-assistant

## Project overview
AI Ops / Incident Triage Assistant. FastAPI backend (`apps/api`), Postgres
+ PgBouncer for data, deployed via Docker Compose locally. See
`docs/ARCHITECTURE.md` (once it exists) for the full diagram.

## Development environment
`docker compose up` brings up `api` + `pgbouncer` + `db`, with `.env`
(copy from `.env.example`) providing local credentials. Nothing needs
installing on the host except Docker — `apps/api` runs inside its own
container via `uv`.

## Build & test commands
- Run the stack: `docker compose up --build`
- Backend tests: `docker compose exec api uv run pytest` (once tests exist)
- Lint: `docker compose exec api uv run ruff check .`
- Rebuild after a dependency change: `docker compose up --build`

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
