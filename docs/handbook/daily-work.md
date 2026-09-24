# Daily work: how to change things

The recipes for the changes you will make every week, in the order to do
them, then the Git workflow and the full list of settings. `make` lists
every command; each target is a thin wrapper, so read the recipe in the
Makefile to see the real `docker compose` command.

## Everyday commands

| I want to… | Run |
|---|---|
| start / stop the dev stack | `make up` / `make down` (data kept) |
| see what is running and healthy | `make ps` (`ENV=prod` for the production stack) |
| follow logs | `make logs S=api` |
| a shell in a container | `make sh S=api` |
| query the database | `make psql` |
| lint, format, types | `make lint`, `make fmt`, `make typecheck` |
| all tests as CI runs them | `make test`; unit only: `make test-fast` |
| everything CI checks, before a push | `make check` |
| the production-shaped stack | `make prod-up` (HTTPS on `EDGE_HTTPS_PORT`; nginx on loopback `HTTP_PORT`) |
| start from an empty database | `make nuke && make up` (deletes the dev volume) |
| sample data | `make seed n=10000` |
| sign in (dev) | http://localhost:5173 → Sign in → `alice`, `bob`, `carol` or `dave`, password `DEMO_USER_PASSWORD` from `.env` |
| call the api with curl, signed in | `c=$(make -s session groups="team:default:responder")`, then `curl -b "$c" -H "Origin: http://localhost:5173" ...` (`ENV=prod`: `Origin` is `PUBLIC_URL`) |
| end someone's sessions now | `make revoke email=alice@example.com` (`ENV=prod`) |
| the identity provider's admin console (dev) | http://localhost:5173/auth/admin/, `admin` / `KEYCLOAK_ADMIN_PASSWORD` |

## Add a Python dependency

```
make deps-api p=httpx            # runtime dependency
make deps-api p="--dev pytest"   # dev-only (tests, tools): never in the production image
```

What happens:
1. `uv add` runs inside the api container, as your UID, so the edited
   `pyproject.toml` and `uv.lock` stay owned by you.
2. The image is rebuilt with `--renew-anon-volumes`.

That last flag matters. The dev container keeps `.venv` in an anonymous
volume, which survives rebuilds. `docker compose up --build` would give
you a new image and still mount the old `.venv`: the import fails in the
container while it works in the image. `make rebuild` does the same
refresh after any dependency change you pulled from someone else.

Then:
- **Say why in the PR description** (one line per new dependency). Prefer
  what the standard library or an existing dependency already does.
- **Import only what you declared.** The openai SDK depends on `httpx2`,
  not `httpx`. Code that imported `httpx` worked in dev and tests only
  because `httpx` was a dev dependency, and crashed the production image.
  `make image-check` (also in CI) now imports every module in the
  production image.
- Dependencies are resolved into `uv.lock` (exact versions, hashes). The
  image installs with `uv sync --frozen`: the lockfile or nothing.

## Add an npm dependency

```
make deps-web p=zod              # runtime
make deps-web p="-D vitest"      # dev-only
```

It updates `package.json` and `package-lock.json` in a throwaway Node
container (the version from `apps/web/.nvmrc`), then rebuilds the web
image and its `node_modules` volume. It doesn't use the compose service:
npm also rewrites a hidden lockfile inside `node_modules`, and the
service's `node_modules` volume is root-owned.

## Add an endpoint

1. **The route** in `apps/api/app/routes/<area>.py`, with request and
   response models in `app/schemas.py`. Use `async def` and the
   request's database session (`db: DbSession`). A blocking call inside
   an async route stalls every request on that worker. That's measured:
   a synchronous HTTP client made `/health` take 4.8 s. Put CPU-heavy
   work in `run_in_threadpool`.
2. **Who may call it.** Take `principal: CurrentUser`: a request without
   a session gets 401 before your code runs, and a state-changing one
   from another site gets 403.
   - Filter reads by `principal.team_ids()`, or reuse
     `queries.newest_alerts`.
   - Check writes with `require_role(principal, team_id, Role.RESPONDER,
     "team")`: 404 when the caller cannot see the team, 403 when their
     role is too low.
   - Never trust a team id from the request body without that check.
   - A new right is a new rank check, not a new role
     (`app/access.py`).
3. **Errors** go through the shared shape `{"error": {code, message,
   request_id}}`: raise `HTTPException`, or let the handlers in
   `app/errors.py` map database failures to 503. Never return a stack
   trace.
4. **Rate limiting:** `dependencies=[Depends(rate_limit("<scope>",
   "<setting>"))]`, as in `routes/chat.py`, plus a `<SCOPE>_RATE_LIMIT`
   setting (see "Add a setting"). It counts per signed-in user;
   `rate_limit_by_ip` is for routes used before sign-in.
5. **Metrics** come for free: the middleware records every request by
   its route *template*, and the access log records the user's id.
6. **Tests:** success, validation, not found, access (401 / 404 / 403 /
   `csrf_failed`), dependency down, rate limit. The checklist is in the
   testing chapter.
7. **Through nginx:** everything under `/api/` is proxied. A streaming
   endpoint needs its own `location` with `proxy_buffering off`
   (`location = /api/chat/stream` in `apps/web/nginx/default.conf`), or
   nginx delivers the whole stream at once. An internal-only endpoint
   gets `return 404` there, as `/api/metrics` does.
8. **The UI** calls it through `apps/web/src/lib/api.ts`, which
   turns every non-2xx into an `ApiError` carrying the request id. A 401
   from any query shows the sign-in page (`main.tsx`). Hide what a role
   cannot do (`atLeast(team.role, 'responder')`), knowing that hiding is
   a courtesy: the api is the control.

## Add a setting

Four places (`app/config.py` says so too):
1. `Settings` in `apps/api/app/config.py`: a typed field with a default
   and bounds (`Field(5.0, gt=0)`). It is validated once at startup, so a
   bad value stops the process with a readable error, not on the first
   request.
2. `.env.example`, if a developer or operator should set it.
3. The `environment:` of the service in `compose.yaml` (or
   `compose.prod.yaml`, if production-only). Compose passes only what is
   listed there.
4. The reference table at the end of this chapter.

Secrets are `SecretStr`: they print as `**********` and must be read with
`.get_secret_value()`. An empty value is not "unset". gunicorn crashed
at startup on `WEB_CONCURRENCY=""`, so never pass `${VAR:-}` for
something that is parsed as a number.

## Change the database schema

```
# 1. edit apps/api/app/models.py
make migration m="add alert source"     # autogenerate, formatted by ruff
# 2. READ the generated file in apps/api/migrations/versions/
make migrate                            # apply to the dev database
make test-api                           # includes `alembic check`: models and migrations must agree
```

What autogenerate gets wrong, and you must fix by hand:
- **A rename** comes out as a drop plus an add, and the data is lost.
  Use `op.alter_column(..., new_column_name=...)`.
- It misses some constraint changes and server defaults.
- **Data migrations** are never generated.

Rules for migrations that deploy without downtime. The old api keeps
serving while they run (ADR-0011):
- **Expand, then contract.** Add a column (nullable, or with a default)
  in one release. Start using it in the same or the next release. Remove
  the old column only in a later release, once nothing reads it.
- **Locks.** Every migration sets `lock_timeout = 5s`. An `ALTER TABLE`
  that waits behind a long query would otherwise make every later query
  on the table wait behind it (measured: users got 503s at 10 s).
  If it times out, retry off-peak.
- **Indexes on big tables** use `CREATE INDEX CONCURRENTLY` inside
  `op.get_context().autocommit_block()`, as the `(created_at, id)` index
  migration does. It can't run in a transaction, and a plain `CREATE
  INDEX` blocks writes for the whole build.
- **Migrations connect as the owner role, directly to Postgres** (not
  through PgBouncer). The app's role can read and write rows, but cannot
  change the schema.
- **Foreign keys on big tables** are added `NOT VALID` (instant: new rows
  only), then `VALIDATE CONSTRAINT` in its own transaction, which scans
  without blocking writes. The teams migration (`3713e56869fa`) has all
  three patterns: a column with a default, a foreign key validated
  separately, a concurrent index. It ran on 2 M rows in 1.5 s under load
  with 0 failed requests.
- **Name every constraint.** Alembic cannot generate a downgrade that
  drops an unnamed one.

**A new table that holds team data** (anything a team owns):
1. A `team_id` column, NOT NULL, with a foreign key to `teams`.
2. In the same migration, row-level security, with the policies of
   `ce83ff21ab01` as the model: `ENABLE ROW LEVEL SECURITY`, then one
   policy per command that reads `app.read_team_ids` /
   `app.write_team_ids` / `app.admin_team_ids` through
   `tenant_team_ids()`. Commands the app never runs get no policy.
3. Routes: `principal: CurrentUser`, reads filtered by
   `principal.team_ids()`, writes checked with `require_role`. The app
   answers first; the policies are the backstop.
4. Tests at both levels. Through the api: 401, 404 for another team, 403
   for a role too low. At the database, as the app role: no context sees
   nothing, a viewer sees their team, a write without the role is refused
   (`test_row_level_security.py`).

## Add a metric, a dashboard panel, an alert

1. **The metric** in `apps/api/app/metrics.py`. Label values must come
   from a small fixed set: route templates, outcomes, never ids or user
   input. Each distinct label combination is a series Prometheus holds in
   memory. Gauges need a `multiprocess_mode` (`livesum`, `max`...),
   because each gunicorn worker writes its own file.
2. **The panel** in `infra/observability/grafana/build_dashboard.py`,
   then `make dashboard`. Commit both files: `make obs-check` fails when
   the JSON and its generator differ. An edit made in the Grafana UI is
   lost at the next reload unless it is brought back into the generator.
3. **The alert** in `infra/observability/prometheus/alerts.yml`, with a
   test in `alerts.test.yml`: when it must fire, and when it must not.
   Then run `make obs-check`. A rule on a counter that may not exist yet
   needs `or vector(0)` or `absent()`: see the observability chapter.

## Data

```
make seed n=1000000 [ENV=prod]   # synthetic alerts, ~5 s for 2 M rows; ANALYZE included
make psql                        # dev database as the owner
make backup [ENV=prod]           # dump to backups/ ; make restore file=... to load one
make nuke                        # delete the dev stack's volumes: an empty database next time
```

## Git workflow

Trunk-based: `main` is always deployable, and every change reaches it
through a pull request.

1. **An issue first**: what and why. For anything non-trivial, the
   approach too. The issue number names the branch and closes with the
   PR.
2. **A branch** `type/<issue>-<short-description>`:
   `feat/14-release-and-demo-vm`, `fix/31-pool-leak`. Branch *before*
   the first edit.
3. **Commits** in Conventional Commits: `feat:`, `fix:`, `docs:`,
   `perf:`, `chore:`, `test:`, `refactor:`. The PR is squash-merged, so
   the PR title becomes the commit on `main`: make it a good one.
4. **`make check`** before pushing. It runs what CI runs.
5. **The pull request**, with:
   - `Closes #N`
   - what changed and why
   - how you verified it (commands, numbers)
   - one line per new dependency
   - anything the reviewer should look at first
6. **CI must be green.** `lint`, `test-api`, `build`, `web-build` and
   `e2e` are required checks: branch protection enforces them.
7. **Review:** read the diff as the next person to debug it at 3 am.
   Check tests for the failure paths, error shapes and timeouts on
   anything that waits, and that labels are bounded.
8. **Squash-merge, delete the branch.** Merged ADRs are never edited: a
   new ADR supersedes an old one.

Never commit `.env`, dumps (`backups/`) or captures (`.captures/`):
they're gitignored. If a secret reaches a commit, rotate it. Removing it
from history is not enough on a public repo; it's already copied.

## Which document to write

| You… | Write |
|---|---|
| are proposing what to build and why | a PRD (`docs/prd/`) |
| want agreement on an approach before building | an RFC (`docs/rfc/`) |
| made a decision someone will later ask "why?" about: a dependency, a schema shape, a timeout, a deployment pattern | an ADR (`docs/adr/`) |
| found a procedure someone will need under pressure | a runbook (`docs/runbooks/`) |
| learned how something works here that the next person will trip over | the matching handbook chapter, and a line in the gotcha list of `gold_standard_development_guide.md` |

## Settings reference

Every variable the stack reads. Where a variable is set: `.env` (from
`.env.example`), or the service's `environment:` in the compose files.

**The api** (`apps/api/app/config.py`: typed, validated at startup):

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `dev` | `dev`, `test` or `prod` |
| `LOG_LEVEL` | `INFO` | |
| `ROOT_PATH` | empty | the path prefix the proxy strips (`/api`); used in generated URLs |
| `DOCS_ENABLED` | `true` | the OpenAPI UI at `/api/docs` |
| `DATABASE_URL` | required | `postgresql://app-role@pgbouncer/...`; the async driver is chosen by the app |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 20 / 0 | per worker; overflow connections are discarded on return, which churned under bursty load |
| `DB_POOL_TIMEOUT_S` | 5 | wait for a pooled connection, then 503 |
| `DB_CONNECT_TIMEOUT_S` | 5 | new connection to PgBouncer. There is deliberately no query timeout on the client (ADR-0010) |
| `REDIS_URL` | required | the rate-limit store (Valkey) |
| `RATELIMIT_TIMEOUT_S` | 0.2 | budget per limiter call; past it the request is allowed (fail-open, ADR-0004) |
| `REDIS_MAX_CONNECTIONS` | 256 | per worker; at least the concurrent requests per worker |
| `RATELIMIT_WINDOW_S` | 60 | |
| `ALERTS_RATE_LIMIT` / `CHAT_RATE_LIMIT` | 60 / 10 | per signed-in user per window. Load tests raise both |
| `AUTH_RATE_LIMIT` | 30 | sign-in redirects and callbacks, per client IP per window |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | required | any OpenAI-compatible endpoint (ADR-0006) |
| `LLM_CONNECT_TIMEOUT_S` | 5 | |
| `LLM_READ_TIMEOUT_S` | 60 | max silence from the provider, first token included |
| `LLM_STREAM_TIMEOUT_S` | 120 | whole answer |
| `LLM_MAX_RETRIES` | 1 | the SDK's default is 2, with a 600 s read timeout |
| `LLM_MAX_OUTPUT_TOKENS` | 800 | cost and latency cap. A reasoning model's hidden thinking counts against it |
| `LLM_REASONING_EFFORT` | empty (not sent) | reasoning models only: `low`, `medium` or `high`. Other models reject the parameter. gpt-oss:20b: `low` (ai-engineering chapter) |
| `EMBEDDING_MODEL` | empty (runbooks off) | runbook search: an embedding model on the same endpoint (RAG chapter). `.env.example` sets the mock's, `mock-embed` |
| `EMBEDDING_QUERY_PREFIX` / `EMBEDDING_DOCUMENT_PREFIX` | empty | the model's task prefixes, from its card (nomic-embed-text: `"search_query: "`, `"search_document: "`); quoted in `.env` |
| `EMBEDDING_DIMENSIONS` | empty (the model's own) | the database stores 768: set it for a model with another native size that can shorten its vectors |
| `EMBEDDING_TIMEOUT_S` | 5 | past it, runbook search uses keywords alone |
| `RAG_CONTEXT_CHUNKS` | 4 | runbook sections per prompt; 0 = alerts only |
| `RUNBOOKS_RATE_LIMIT` | 60 | per user per window: runbook writes and searches (each an embedding call) |
| `CHAT_CONTEXT_ALERTS` | 20 | recent alerts put in the prompt |
| `SSE_HEARTBEAT_S` | 15 | keep-alive comments while the model is silent; below every proxy's idle timeout |
| `ALERTMANAGER_WEBHOOK_TOKEN` | empty (webhook off) | shared with Alertmanager |
| `PUBLIC_URL` | required (compose: `https://localhost`) | the site's address as browsers see it, no path; the redirect URI (`<PUBLIC_URL>/api/auth/callback`) and the `Origin` every state-changing request must carry derive from it. Dev: `http://localhost:5173` |
| `OIDC_ISSUER` | required (compose: `<PUBLIC_URL>/auth/realms/triage`) | exactly the `iss` of the provider's ID tokens |
| `OIDC_DISCOVERY_URL` | compose: the bundled Keycloak's, direct | where the api reads the provider's metadata. Empty for a real provider (`<issuer>/.well-known/openid-configuration`) |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | `triage-web` / required | the app's registration at the provider; the secret must not be empty |
| `OIDC_SCOPES` | `openid profile email` | |
| `OIDC_GROUPS_CLAIM` | `groups` | the claim carrying `team:<slug>:<role>` and `org:admin` (`roles` for Entra ID app roles) |
| `OIDC_TIMEOUT_S` | 5 | each call to the provider |
| `SESSION_MAX_AGE_S` / `SESSION_IDLE_TIMEOUT_S` | 43200 / 7200 | a session ends 12 h after sign-in or 2 h after its last request; role changes apply at the next sign-in |
| `SESSION_COOKIE_SECURE` | `true` | `false` only for plain-HTTP dev (the dev overlay sets it): the cookie is then `triage_session`, not `__Host-triage_session` |
| `JUDGE_API_KEY` | empty (`LLM_API_KEY`) | development only, for `make evals`: the key of a judge at another provider than the model under test |

**gunicorn** (`apps/api/gunicorn.conf.py`, production image only):

| Variable | Default | Notes |
|---|---|---|
| `WEB_CONCURRENCY` | = usable CPUs | worker count. It follows the container's CPU limit (cgroup `cpu.max`). Never set it to an empty string |
| `GUNICORN_TIMEOUT` | 30 | a worker silent this long is killed (`WORKER TIMEOUT`) |
| `GUNICORN_GRACEFUL_TIMEOUT` | 120 | time to finish in-flight requests on stop; `stop_grace_period` must be above it |
| `GUNICORN_KEEPALIVE` | 75 | above nginx's upstream keep-alive (60 s): the proxy must close idle connections first |
| `GUNICORN_MAX_REQUESTS` (`_JITTER`) | 0 | worker recycling: off, it caused 502 bursts (ADR-0009) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` | whose `X-Forwarded-For` to trust; prod sets `*` because only nginx can reach the api |
| `GUNICORN_CONTROL_SOCKET` | `/tmp/gunicorn.ctl` | for `make gunicorn` |
| `PROMETHEUS_MULTIPROC_DIR` | `/tmp/prometheus` (image) | must exist before start; never set to `""` |

**The stack** (`.env`):

| Variable | Used by |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | the schema owner: Postgres and migrations |
| `APP_DB_USER`, `APP_DB_PASSWORD` | the app's role (reads and writes rows; no DDL), through PgBouncer |
| `MONITOR_DB_USER`, `MONITOR_DB_PASSWORD` | postgres-exporter (`pg_monitor`: statistics only) |
| `COMPOSE_PROFILES` | `mock` runs the mock LLM, `edge` the TLS edge, `idp` the bundled Keycloak; add `observability` for the monitoring stack |
| `KEYCLOAK_ADMIN_PASSWORD`, `DEMO_USER_PASSWORD` | the bundled Keycloak's administrator and its demo users (it refuses to start without both) |
| `GRAFANA_ADMIN_PASSWORD`, `GRAFANA_PORT`, `PROMETHEUS_PORT`, `ALERTMANAGER_PORT` | monitoring (bound to 127.0.0.1) |
| `BIND_ADDR`, `API_PORT`, `WEB_PORT` | dev ports (127.0.0.1 by default) |
| `HTTP_BIND`, `HTTP_PORT` | nginx over plain HTTP: loopback `8088` behind the TLS edge; `0.0.0.0`/`80` behind a cloud load balancer (edge profile off) |
| `SITE_ADDRESS` | the TLS edge's name: a domain gets a Let's Encrypt certificate automatically; `localhost` uses Caddy's local CA |
| `EDGE_BIND`, `EDGE_HTTP_PORT`, `EDGE_HTTPS_PORT` | where the edge publishes HTTP (redirect) and HTTPS; 80/443 on a VM |
| `HSTS_MAX_AGE` | seconds browsers must use HTTPS only: 0 for localhost, 31536000 for a real domain |
| `ACME_CA` | the ACME directory (default Let's Encrypt; its staging directory for a first setup; Pebble in `make acme-test`) |
| `API_CPUS` | the api's CPU limit (and so its worker count) |
| `IMAGE_PREFIX`, `IMAGE_TAG` | which images the production stack runs; `make deploy` records `IMAGE_TAG` |
