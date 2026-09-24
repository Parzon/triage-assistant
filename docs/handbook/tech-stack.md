# The tech stack

Every technology in this repo:
- what it does here;
- why it was chosen, and what else was on the table;
- the version pinned;
- the trap to know before touching it.

Versions are those of v0.4.0 (2026-09). The lockfiles and image tags
are the source of truth, and Dependabot moves them weekly:
- `apps/api/uv.lock`;
- `apps/web/package-lock.json`;
- `tests/e2e/package-lock.json`;
- the `FROM` lines and the `image:` lines in the compose files.

## On one page

| Layer | Technology | Version | Where |
|---|---|---|---|
| Language (api) | Python | 3.13 | `apps/api/Dockerfile` |
| Packages (api) | uv | 0.12.18 | `apps/api/pyproject.toml`, `uv.lock` |
| Web framework | FastAPI on Starlette | 0.141 / 1.6 | `apps/api/app/` |
| Validation, settings | Pydantic, pydantic-settings | 2.13 / 2.15 | `app/schemas.py`, `app/config.py` |
| Servers | gunicorn managing Uvicorn workers (uvloop, httptools) | 26.2 / 0.53 | `apps/api/gunicorn.conf.py` |
| Database access | SQLAlchemy (async) + asyncpg | 2.0.54 / 0.31 | `app/db.py`, `app/models.py` |
| Migrations | Alembic | 1.20 | `apps/api/migrations/` |
| Database | PostgreSQL, with pgvector | 17 / 0.8.6 | `compose.yaml` (`db`) |
| Connection pooler | PgBouncer | 1.25.2 | `compose.yaml` (`pgbouncer`) |
| Rate-limit store | Valkey, via redis-py | 8.1 / 8.1 | `app/ratelimit.py` |
| The model | the openai SDK over httpx2 | 3.17 / 2.13 | `app/llm.py` |
| Sign-in | OIDC; PyJWT + cryptography for ID tokens | 2.14 / 50 | `app/oidc.py` |
| Identity provider (dev, demo) | Keycloak | 26.7.4 | `infra/keycloak/` |
| Metrics (app) | prometheus-client | 0.26 | `app/metrics.py` |
| Language (web) | TypeScript on Node | 6.0 / 24 | `apps/web/` |
| UI | React, TanStack Query | 19.3 / 5.103 | `apps/web/src/` |
| Build, dev server | Vite | 8.3 | `apps/web/vite.config.ts` |
| Static files, proxy | nginx (unprivileged image) | 1.30 | `apps/web/nginx/` |
| TLS edge | Caddy | 2.11.4 | `tools/edge/` |
| Tests (api) | pytest, pytest-asyncio, coverage | 9.1 / 1.4 / 7.16 | `apps/api/tests/` |
| Tests (web) | Vitest, jsdom, Testing Library | 5.0 / 30 / 16 | `apps/web/src/**/*.test.ts(x)` |
| Tests (browser) | Playwright | 1.63 | `tests/e2e/` |
| Lint, types | ruff, mypy (strict); oxlint, tsc | 0.16 / 2.3; 1.85 / 6.0 | `pyproject.toml`, `package.json` |
| Local models | Ollama: gpt-oss:20b answers, nomic-embed-text embeds, gemma3:27b judges | 0.34.3 | `compose.yaml` (`ollama` profile) |
| Containers | Docker Engine, Compose v2, GNU make | | `compose*.yaml`, `Makefile` |
| CI, releases | GitHub Actions, GHCR, Dependabot | | `.github/` |
| Monitoring | Prometheus, Alertmanager, Grafana, exporters, cAdvisor | 3.14 / 0.34 / 13.2 | `infra/observability/` |
| Load testing | k6, vegeta, oha, Locust, Artillery, JMeter | | `tests/load/` ([load testing](load-testing.md)) |
| Debugging | debugpy, py-spy, netshoot (tcpdump, dig, ss), strace | | `Makefile` ([debugging](debugging.md)) |

## The api (Python)

**Python 3.13**, from the official `python:3.13-slim` image. It is the
current stable line with the newest performance work. The
free-threaded build is not used: every library here assumes the GIL.

**uv** installs the dependencies. It resolves in seconds, and writes a
lockfile with hashes of every package (`uv.lock`). The images install
with `uv sync --frozen`, which fails rather than re-resolving: the
image gets exactly what was reviewed. Alternatives: pip with
pip-tools, Poetry, PDM. uv is the fastest of them, and one tool
replaces pip, pip-tools and virtualenv.
- **Trap:** after a dependency change, `make rebuild`. The `.venv` lives
  in an anonymous volume that plain `up --build` keeps
  ([daily work](daily-work.md)).

**FastAPI** (on **Starlette**) gives:
- routing;
- request validation through Pydantic models;
- dependency injection (`CurrentUser`, `DbSession`);
- the OpenAPI document.

Alternatives: Flask with an async bridge, Django with Ninja, Litestar.
FastAPI is the most widely known async Python framework, and typed
dependencies make access control a function signature
(`principal: CurrentUser`).
- **Trap:** FastAPI closes a `yield` dependency *after the response is
  sent*. A request-scoped database session would have held its
  connection for a whole streamed answer. `Depends(..., scope="function")`
  closes it when the endpoint returns (an integration test checks the
  pool mid-stream).

**Pydantic 2** validates requests, responses and settings.
**pydantic-settings** reads settings from the environment, with types
and bounds (`Field(ge=1)`), and refuses to start on a bad value.
- **Trap:** an empty variable (`LLM_REASONING_EFFORT=`) is the string
  `""`, not "unset". Validators turn `""` into `None` where empty means
  unset (`app/config.py`).

**gunicorn + Uvicorn workers.** Uvicorn runs the ASGI app, on uvloop
and httptools. gunicorn manages the worker processes:
- it restarts a crashed worker;
- it drains workers gracefully on SIGTERM (`graceful_timeout`);
- its hooks set up Prometheus's multiprocess mode.

The `uvicorn-worker` package is the worker class (it moved out of
Uvicorn). Development runs plain Uvicorn with `--reload`.
- **Trap:** one async worker per available CPU (`WEB_CONCURRENCY`
  overrides it), not the classic `2 × CPU + 1` for sync workers. An
  async worker already keeps its CPU busy ([performance](performance.md)).

**SQLAlchemy 2 (async) with asyncpg.** SQLAlchemy is the query builder
and ORM, with typed `Mapped[...]` models. asyncpg is the fastest
Postgres driver for asyncio. Alternatives: psycopg 3 (sync and async,
closer to libpq); raw asyncpg (no ORM); SQLModel (a thin layer over
both).
- **Trap:** async SQLAlchemy runs database calls inside greenlets.
  Coverage then needs `concurrency = ["greenlet", "thread"]` or it
  under-reports.
- **Trap:** no client-side query timeout (`command_timeout`) on
  purpose. When one fires on a dead connection, asyncpg waits forever
  for the cancel to be acknowledged, and the connection leaks
  (ADR-0010, the `db-freeze` drill).

**Alembic** migrations are generated from the models (`make
migration`), then read and fixed by hand. Autogenerate misses renames
and some constraint changes. Migrations are expand/contract, so the
previous release still runs on the new schema (ADR-0015).

**PostgreSQL 17** holds everything that must survive: alerts, runbooks,
teams, memberships, sessions. It also enforces team isolation itself,
with row-level security (ADR-0014).
- **Trap:** a major version upgrade (16 → 17) needs a dump and restore
  or `pg_upgrade`, not a new tag. That's why Dependabot leaves compose
  images alone.

**pgvector** adds a `vector` type, distance operators (`<=>` is cosine
distance) and approximate indexes (HNSW, IVFFlat) to Postgres: runbook
search runs next to the data and under the same row-level security
(ADR-0017). The image, `pgvector/pgvector:0.8.6-pg17-trixie`, is the
same Debian, glibc and PostgreSQL build as `postgres:17`, so the data
directory and collations carry over. Alternatives: a dedicated vector
database (Qdrant, Weaviate, OpenSearch), which adds a second datastore,
a second backup, and a second copy of team membership.
- **Trap:** an approximate index hands back its nearest rows first, and
  filters after. With the caller's team owning 1% of the rows, a
  one-team search found nothing. The service sends an explicit team
  filter; iterative scans (0.8+) cover unfiltered searches
  ([RAG](rag.md)).
- **No new package:** SQLAlchemy has no vector type, and `app/vector.py`
  defines one in under 60 lines (the text wire format). The `pgvector` Python
  package does the same.

**PgBouncer** pools connections in **transaction** mode: thousands of
client connections share a few server connections.
- **Trap:** session state does not survive between transactions. Per-request settings use
  `set_config(..., true)`, which is scoped to the transaction (the
  row-level security context, ADR-0014); `statement_timeout` sits on
  the database role, not in a `SET`.
- **Prepared statements:** asyncpg prepares every query. PgBouncer
  1.21+ tracks protocol-level prepared statements in transaction mode
  (`max_prepared_statements`, 200 by default: read from the running
  PgBouncer's `SHOW CONFIG`). The driver therefore needs no workaround.
  On an older PgBouncer, or RDS Proxy, re-run the database drills.

**Valkey** is the Linux Foundation's BSD-licensed fork of Redis, and
the client is **redis-py**. It holds only rate-limit counters, which
are disposable. When it is down, the limiter fails open (ADR-0002,
ADR-0004).

**The openai SDK** is the client for every OpenAI-compatible endpoint
(ADR-0006):
- OpenAI and Azure OpenAI;
- vLLM, Ollama, LiteLLM;
- the mock.

`app/llm.py` wraps it in a small interface: a stream of text, `Usage`
and `Finish`, and typed errors.
- **Trap:** openai 3.x ships its HTTP client as the separate `httpx2`
  package. `httpx` is only a test dependency here. Importing `httpx` in
  app code passed in development and crashed the production image
  (`make image-check` now catches it).

**PyJWT + cryptography** verify the identity provider's ID tokens: the
signature against its published keys (JWKS), then `iss`, `aud`, `azp`,
`exp`, `iat` and `nonce` ([security](security.md)). The alternative
was Authlib, a full OAuth framework. PyJWT does one thing, and the
protocol code around it is about 300 lines in `app/oidc.py`, every check
tested.

**prometheus-client** exports `/metrics`. Under gunicorn it runs in
multiprocess mode: each worker writes files, and the scrape sums them.
- **Trap:** multiprocess mode turns on when `PROMETHEUS_MULTIPROC_DIR`
  merely *exists*, even empty. The operator CLI removes it before
  importing the app.

**Tooling:**
- **ruff** lints and formats (it replaces flake8, isort and black).
- **mypy** in strict mode type-checks `app/` and `evals/`.
- **pytest** with **pytest-asyncio** and **pytest-cov** tests.
- **debugpy** takes breakpoints from VS Code into the container.

## The web (TypeScript)

**Node 24** (LTS), with **TypeScript 6** in strict mode.

**React 19**, with **TanStack Query** for server state: fetching,
caching, refetching and mutations. There is no Redux. Nearly all state
here is server state, and TanStack Query owns it. Local UI state is
`useState`.

**Vite** is the dev server, with hot reload and proxies for `/api` and
`/auth`, so sign-in stays on one origin in development. It is also the
production bundler. Alternatives: Next.js (server rendering, which this
app does not need), Create React App (deprecated), webpack (slower,
more configuration).

**The chat stream** is `fetch` plus a `ReadableStream` reader, parsing
SSE by hand (`src/lib/sse.ts`). The browser's `EventSource` cannot send
a POST body or custom headers.

**Vitest + jsdom + Testing Library** run component tests in a simulated
DOM. **oxlint** lints, much faster than ESLint, with fewer rules and
plugins; `tsc -b` type-checks.

**nginx** (the `nginxinc/nginx-unprivileged` image, non-root, port
8080) serves the built files and proxies `/api` to the api.
- **Trap:** the proxy must not buffer the chat stream (`proxy_buffering
  off` for `/api/chat/stream`). In the first production-shaped run it
  did, and streaming arrived all at once. Only the production shape
  showed it.

## The edge and sign-in

**Caddy** terminates TLS in front of nginx (ADR-0012):
- **Certificates:** automatic, from Let's Encrypt (ACME), or its
  internal CA locally.
- **Headers:** HSTS and the security headers.
- **Restarts:** it holds requests while nginx restarts (`lb_try_duration`).

Alternatives: TLS in nginx, which needs certbot plus reload scripts;
Traefik, which is configured by container labels. Caddy needs the
least configuration for automatic HTTPS. `make acme-test` rehearses
it against Pebble, Let's Encrypt's test CA.

**Keycloak** is the bundled OpenID Connect provider, for development
and demos: the realm, demo users and groups are imported from
`infra/keycloak/triage-realm.json`. In production, the organisation's
provider (Entra ID, Okta, Google) replaces it, with configuration only
([security](security.md)).
- **Trap:** Keycloak imports the realm only into an empty database, so
  a realm change needs the container recreated.
- **Trap:** its hostname settings (v2) decide the issuer in every
  token. It must match `OIDC_ISSUER` exactly.

## Testing tools

**Playwright** drives real browsers for the end-to-end suite. It is
published as three npm packages, one inside the other:

| Package | What it is | Use it for |
|---|---|---|
| `playwright-core` | The browser automation API alone: launch, pages, selectors, network. No test runner, no browsers. | Libraries and tools that bring their own browsers or connect to a remote one (`connect`, `connectOverCDP`), where install size matters. |
| `playwright` | `playwright-core` plus the CLI (`npx playwright install`, `codegen`, `show-trace`) and the test runner's machinery. | Scripts: automation and scraping, without a test suite. |
| `@playwright/test` | The test runner's public face: `test`, `expect` with auto-waiting assertions, fixtures, projects, parallel workers, retries, reporters, traces. It depends on `playwright`. | **Test suites.** This repo uses it (`tests/e2e/package.json`), and the lockfile pulls in the other two at the same version (1.63.0). |

- **Version lock:** each Playwright release is built against specific
  browser builds. The Docker image that runs the suite,
  `mcr.microsoft.com/playwright:v1.63.0-noble`, must match the npm
  version exactly, or the browsers are missing. Bump both together.
- **Why Playwright:**
  - Cypress runs inside the browser: no second tab, cross-origin steps
    need `cy.origin`, and parallel runs are its paid cloud service.
  - Selenium has no built-in auto-waiting, so its tests tend to be
    flakier.
  - Playwright drives browsers from outside: tabs, origins and network
    interception are ordinary API calls. It parallelises for free, and
    its trace viewer replays a failed run step by step.
- **What only Playwright caught here:** React reused one `<button>`
  for Ask and Stop, so Stop re-submitted. jsdom never showed it
  ([testing](testing.md)).

**The mock LLM** (`tools/mock-llm`, this repo's own) is
OpenAI-compatible, with tunable speed and failure modes: HTTP 429 or
500, a hang, a stream dropped mid-answer, an empty answer. Every
automated test uses it. **Ollama** runs real open models locally for evals
([AI engineering](ai-engineering.md)).

## Operations

**Docker Engine and Compose v2** run every environment:
- profiles (`mock`, `edge`, `idp`, `ollama`);
- health checks, and `up --wait`;
- three compose files: base, dev and prod (ADR-0003).

**GNU make** is the single entry point. It is written for make 3.81, so
it works with macOS's.

**GitHub Actions** runs CI and releases:
- actions are pinned to commit SHAs;
- releases build natively on amd64 and arm64 runners, push by digest,
  and attach provenance and an SBOM;
- **GHCR** hosts the public images;
- **Dependabot** opens grouped update PRs weekly.

**Prometheus, Alertmanager and Grafana** monitor the stack, fed by:
- the exporters for Postgres, PgBouncer, Valkey and the host;
- **cAdvisor**, for container metrics.

Dashboards, alert rules and the rules' tests are code
([observability](observability.md)).

**Load testing:** six tools with working scripts, and a measured
comparison ([load testing](load-testing.md)). k6 is the default.

**Debugging:**
- **netshoot** has network tools in the api's network namespace;
- **py-spy** profiles a live worker without restarting it;
- **strace** summarises system calls;
- **tcpdump** captures packets for Wireshark ([debugging](debugging.md)).

**cloud-init** turns a fresh Ubuntu VM into a host for the stack
(`infra/vm/cloud-init.yaml`, [the VM runbook](../runbooks/demo-vm.md)).

## Adding a technology

Before a new dependency or service lands:
1. **Justify it in the PR description**, in one line: what it does
   that the stack cannot (AGENTS.md).
2. **Check:**
   - the licence (Redis and Docker Desktop both changed theirs);
   - that it is maintained, and its security record;
   - that it publishes arm64 images or wheels;
   - its size.
3. **Pin it** in the lockfile or by image tag, and make sure Dependabot
   covers it.
4. **Write an ADR** if a reader would ask "why this?".
5. **When it bites, add a line** to the gotcha list in the guide.
