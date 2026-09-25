# Tech stack

Every technology in this repository: its role, what it does here,
whether a new project should start with it, when to add it if not, and
why. Then [how sign-in works](#how-sign-in-works) (passwords, JWTs,
sessions, secrets), and how to decide when to add architecture, and when
to take it away.

No versions here: a version table goes stale within weeks. The lockfiles
and image lines are the truth, and Dependabot moves them: `apps/api/uv.lock`,
`apps/web/package-lock.json`, `tests/e2e/package-lock.json`, the `FROM`
lines in the Dockerfiles and the `image:` lines in the compose files.

Two marks on every item:
- **How well to know it,** for an engineer on an AI team: ● learn
  deeply · ◐ explain and review · ○ know what it is. The AI, the data
  and the access rules deeply; the plumbing well enough to review a
  change to it.
- **A new project:** **day one** (every new service starts with it) ·
  **when ...** (add it when that happens, not before) · **rarely** (only
  for a reason you can name).

## At a glance: what a new project starts with

**Day one.** The api (Python, uv, FastAPI, SQLAlchemy, Alembic), Postgres,
row-level security if data belongs to teams or customers, company
sign-in (OIDC, with Keycloak locally), the model behind the
OpenAI-compatible seam with the mock for tests, the eval harness,
`/metrics` and JSON logs, Docker and Compose behind a Makefile, CI with
tests and both scanners, Dependabot. With a UI: React, Vite, TanStack
Query, streaming with `fetch`, nginx.

**Add it when:**

| Add | When |
|---|---|
| pgvector and full-text search | the answers must come from your documents |
| Valkey | more than one process shares a limit, a cache or a lock |
| PgBouncer (or RDS Proxy) | connections near Postgres' limit, or new connections churn |
| Caddy | a VM serves HTTPS itself, with no cloud load balancer in front |
| OpenTelemetry and a trace store | someone asks "what did this answer see, and where did its time go?" (with retrieval, that is early) |
| Prometheus, Alertmanager, Grafana | someone is on call |
| Ollama | evals need a real model without a paid key, or data must not leave the machine |
| an MCP server | other AI clients should use the service's tools |
| agent mode | the steps are not known before the model runs |
| Playwright | the UI has a flow a unit test can't prove |
| k6 | a capacity question has a number in it |
| cloud-init, ufw, unattended-upgrades | you run a VM yourself |

**Rarely:** a second database for vectors (only when Postgres is
measured short), Kubernetes (many services and a platform team),
Keycloak in production (never: the organisation's provider replaces it).

## The stack

### AI

**An OpenAI-compatible API** (`app/llm.py`) ● · day one<br>
One interface to any model (OpenAI, Ollama, vLLM, or Bedrock behind a
gateway), so changing models is a configuration change. It is the seam
between the service and every model: timeouts, retries, typed errors and
token counts live there (ADR-0006), and the mock and a local model plug
into it like any provider. Start with it even with one provider: the
next one arrives. A provider-only feature (guardrails, a provider's
prompt caching) goes behind the seam, not around it.

**pgvector + Postgres full-text search** ● · when the answers must come
from your documents<br>
Hybrid runbook search, with the two result lists merged by rank. It
lives in the same database as the data it protects; a separate vector
database would be a second place to enforce team access (ADR-0017).
Instead, a dedicated vector database (Qdrant, Weaviate, OpenSearch)
only when Postgres is measured short at your size: it adds a datastore,
a backup and a second copy of who may see what. Watch: under an
approximate vector index, a team filter can find nothing (0 of 20 when
the team owned 1% of the rows): [RAG](docs/handbook/rag.md), `make
bench-rag-filter`.

**The eval harness** (`apps/api/evals/`, written for this repo) ● · day
one for anything that calls a model<br>
Test cases in TOML, deterministic checks, an AI judge checked against
human-labelled answers, and a statistical test against a stored
baseline. It gates every prompt and model change (ADR-0016): a change
moves answers silently, and a failure in 1 run of 40 hides in a handful
of manual tries. CI runs it in plumbing mode against the mock; quality
runs use a real model, repeated ([AI
engineering](docs/handbook/ai-engineering.md)).

**MCP Python SDK** ● · when other AI clients should use your tools<br>
Offers the two read-only tools (`list_alerts`, `search_runbooks`) to
other AI apps, such as Claude Code (`app/mcp_server.py`, ADR-0020). They
are the same functions the agent mode calls: they act with the asker's
rights, take the team from the session, and are audited.

**openai SDK** ◐ · day one, inside the seam<br>
The client for that API, with streaming and tool calls. Watch: its
defaults are a 600 s read timeout and 2 retries (set both); it costs
126 µs of CPU per streamed chunk, which is the api's ceiling for
streams (ADR-0009).

**Ollama** ◐ · when evals need a real model without a paid key<br>
Runs open models on your own GPU, so evals need no paid key. Here
gpt-oss:20b answers, nomic-embed-text embeds and gemma3:27b judges (the
`ollama` profile). In production, self-hosting pays only when the GPU
stays busy: break-even here was about 2 million questions a month
against a hosted small model ([AI cost](docs/handbook/ai-cost.md)).

**mock-llm** (written for this repo) ◐ · day one<br>
A fake model provider with latency and failure knobs, so tests and CI
never need a real model: HTTP 429 or 500, a hang, a stream dropped
mid-answer, an empty answer, on demand (`make mock`). Every automated
test and failure drill uses it.

**OpenTelemetry GenAI spans** ◐ · when an answer takes more than one
step<br>
One trace per answer (retrieval, model call, tokens), with no question
or answer text in it (ADR-0018). Off until an endpoint is set;
production keeps a tenth of them. Instead, a vendor's agent can read the
same spans over OTLP; LLM tracers (Langfuse, Phoenix) are built to store
prompts and answers, which this service keeps out of telemetry ([AI
observability](docs/handbook/ai-observability.md)).

### API

**Python 3.13 + uv** ◐ · day one<br>
uv installs exactly what `uv.lock` pins, in seconds. The images install
with `uv sync --frozen`, which fails rather than re-resolving: an image
gets exactly what was reviewed. Instead: pip with pip-tools, Poetry,
PDM; uv is the fastest, and one tool replaces pip, pip-tools and
virtualenv.

**FastAPI + Pydantic** ◐ · day one<br>
Routes, input validation, and typed settings read from environment
variables. Typed dependencies make access control part of a route's
signature (`principal: CurrentUser`), and settings are checked at
startup: production refuses unsafe ones (ADR-0023). Instead: Flask with
an async bridge, Django with Ninja, Litestar. Watch: a `yield`
dependency closes after a streamed response is sent, so a database
session would hold its connection for a whole answer.

**Uvicorn under gunicorn** ◐ · day one, in the production image<br>
Uvicorn runs the async event loop; gunicorn runs one worker per CPU and
replaces any that die. It also drains streams on a deploy. Development
runs plain Uvicorn with `--reload`. Watch: one async worker per CPU, not
the sync-era `2 × CPU + 1`; the worker count comes from the container's
CPU limit, which `os.cpu_count()` ignores.

**SQLAlchemy + asyncpg** ◐ · day one<br>
Database access, with typed models. **Alembic** ◐: schema changes
(migrations) kept in git, written expand-then-contract so the previous
release runs on the new schema (ADR-0015). Instead: psycopg 3, raw
asyncpg (no ORM), SQLModel. Watch: no client-side query timeout here on
purpose, because it leaked connections (ADR-0010); autogenerate writes a
rename as drop plus add, so read every migration.

**PyJWT** ○ · day one, with sign-in<br>
Checks the identity provider's signed tokens: the signature against its
published keys, then issuer, audience, expiry and nonce. Instead:
Authlib, a whole OAuth framework; PyJWT does one thing, and the protocol
code around it is about 300 lines, every check tested.

**httpx2** ○ · comes with the openai SDK<br>
The HTTP client under the openai SDK and the app's own calls. It is
Pydantic's maintained continuation of httpx, not a look-alike package.
The tests still use the original httpx, as a dev-only dependency: one of
the two would do.

**redis-py** ○ · with Valkey<br>
The Valkey client. Set its socket timeouts and pool size explicitly: a
hung store otherwise holds requests.

**prometheus-client** ○ · day one<br>
Serves `/metrics`. Under gunicorn it runs in multiprocess mode: each
worker writes files, and a scrape sums them. The endpoint costs nothing
on day one; the stack that reads it comes when someone is on call.

### Data and state

**PostgreSQL 17** ● · day one<br>
One database for alerts, runbooks and their vectors, users, sessions and
audit. One system to back up, secure and reason about. Watch: a major
version upgrade needs a dump and restore or `pg_upgrade`, never just a
new image tag.

**Row-level security** ● · day one, when data belongs to teams or
customers<br>
Postgres itself hides other teams' rows, so a forgotten `WHERE` clause
returns nothing instead of leaking data (ADR-0014). Its cost: every
transaction names its caller (`set_config(..., true)`), and tests run as
the app role, since the owner bypasses the policies. A new table with
team data gets its policies and their tests in the same migration.

**PgBouncer** ◐ · when connections near Postgres' limit<br>
Lets many app connections share a few Postgres connections, so you can
add api copies without exhausting Postgres. Add it when replicas ×
workers × pool size nears Postgres' `max_connections` (100 by default;
each api replica here uses 40), or when new connections churn (196
logins in 20 s, measured). On AWS, RDS Proxy may replace it. Watch:
transaction pooling keeps no session state between transactions, so
per-request settings are transaction-local; its default md5 auth cannot
answer Postgres' SCRAM, while `pg_isready` stays green.

**Valkey** ◐ · when more than one process shares a limit, a cache or a
lock<br>
Rate-limit counters shared across workers. A counter kept in each
worker's memory is a classic bug: with N workers, a user gets N times the
limit. Valkey is the open-licence fork of Redis and speaks the same
protocol (ADR-0002). Decide what happens when it is down: here limits
fail open (ADR-0004), except the chat's, which fails closed in
production because each question costs money (ADR-0023). Its
`allkeys-lru` eviction suits counters, not data you must keep.

### Web

**React + TypeScript**, bundled by **Vite**; **TanStack Query** caches
data fetched from the server ○ · day one, with a UI<br>
There is no Redux: nearly all state is server state, which TanStack
Query owns. Instead: Next.js (server rendering, not needed here),
webpack (slower, more configuration). An API-only service has none of
these.

**fetch + ReadableStream** ◐ · day one, for a streamed answer<br>
Reads the streamed answer token by token. This is the pattern every LLM
SDK uses. The browser's `EventSource` can't send a POST body or headers.

**nginx** ◐ · day one, in the production shape<br>
Serves the built UI and forwards `/api` to the api, without buffering
the stream. It also answers in JSON when the api is down, and sets the
security headers. Watch: in the first production-shaped run it buffered
the stream, which only the production shape showed. On AWS, S3 and
CloudFront for the files and an ALB for `/api` can replace it.

### Sign-in and edge

**OIDC with PKCE** ◐ · day one<br>
Company sign-in. The api holds the tokens; the browser only ever gets a
cookie (ADR-0013). Teams and roles come from the provider's groups claim
(`team:<slug>:<role>`), so access is managed where people are. Step by
step, with what is hashed and what is secret: [how sign-in
works](#how-sign-in-works).

**Keycloak** ○ · day one locally, never in production<br>
A stand-in identity provider with four demo users, so development and
tests sign in for real. The company's provider replaces it, with
configuration only, and production refuses to start with it
(`demo_identity_provider`).

**Caddy** ◐ · when a VM serves HTTPS itself<br>
The only container reachable from the internet, serving HTTPS with
automatic Let's Encrypt certificates (ADR-0012). It also holds requests
while nginx restarts, which makes rolling deploys lossless. Behind a
cloud load balancer that terminates TLS (an ALB with ACM), drop it.
Instead: nginx with certbot and reload scripts; Traefik, configured by
container labels. Watch: its Go dependencies carry known
vulnerabilities with no fixed release yet, accepted until a date in
`.trivyignore.yaml`.

### Containers and delivery

**Docker** ◐ · day one<br>
Builds small images that run as non-root with read-only filesystems.
**Docker Compose** ◐: one base file, plus overlays for dev, prod, tests
and debugging (ADR-0003). The image is the environment, so there is no
"works on my machine".

**GitHub Actions** ◐ · day one, or the company's CI<br>
CI on every PR, and a release on every version tag. Actions are pinned
by commit SHA; releases build natively on amd64 and arm64, and attach
provenance and an SBOM.

**Make** ○ · day one<br>
One entry point for all commands: `make` lists the ones you need first,
`make help-all` every one. Each target is a thin wrapper: read it to see
the real command.

**GHCR** ○ · day one, on GitHub<br>
GitHub's image registry. On AWS it is likely ECR: `IMAGE_PREFIX` changes,
nothing else.

**Dependabot** ○ · day one<br>
Weekly dependency-update PRs: packages, Actions, base-image digests and
the compose files' images.

**cloud-init, ufw, unattended-upgrades** ○ · when you run a VM
yourself<br>
A VM's first-boot setup, its firewall, and automatic security patches.
A managed platform has none of them.

**Bash** ○ · day one, kept small<br>
The deploy, backup and drill scripts, checked by shellcheck.

### Supply chain

**Trivy** ◐ · day one<br>
Scans the production images and the web's dependencies in CI and in
every release: a HIGH or CRITICAL finding that has a fix fails the
build. An accepted risk goes in `.trivyignore.yaml` with a reason and an
expiry at most 90 days out (ADR-0022). Deciding which severity blocks a
release, and who may accept a risk, is the team lead's call. Watch: the
scanner is supply chain too. In March 2026 Trivy's own releases and its
GitHub Action's tags were replaced by code that stole CI credentials, so
it runs here from an image pinned by digest, never the Action.

**gitleaks** ◐ · day one<br>
Finds secrets in every commit (`make secrets-scan`, CI's security job).
A fake credential in a test is allowed by fingerprint only, with the
value shown to be fake. A real leaked secret is rotated, not deleted.

### Quality

**pytest** ◐ · day one<br>
api tests, with an 85% coverage gate. Integration tests run against real
Postgres, PgBouncer, Valkey, Keycloak and the mock, in a throwaway stack.
**ruff** and **mypy** ○: lint, format and strict type checks.
**Vitest**, **oxlint**, **tsc** ○: the same for the web app.

**Playwright** ○ · when the UI has a flow a unit test can't prove<br>
Browser tests through the production stack: sign-in on the provider's
page, a streamed answer, Stop. It caught a bug jsdom could not show
(Stop re-submitted the question). Instead: Cypress runs inside the
browser, and parallel runs are its paid service; Selenium has no
auto-waiting. Watch: the Docker image's version must match the npm
package's exactly.

**k6** ○ · when a capacity question has a number in it<br>
Load tests, with open and closed models (`make load`). On demand, never
in CI: a shared runner's timing varies too much to gate on.

**shellcheck**, **promtool** ○ · with scripts, with alert rules<br>
Check the shell scripts, and unit-test the alert rules.

### Monitoring (optional)

**Prometheus** ◐ · when someone is on call<br>
Metrics and alert rules; the rules have tests. On AWS: Amazon Managed
Service for Prometheus, or CloudWatch.

**Jaeger** ◐ · when traces need a place to be read<br>
Traces: OTLP in, the newest 20,000 in memory, a UI that compares two.
On a platform, the company's tracing backend, reached through an
OpenTelemetry Collector: the api changes an address, nothing else.

**Grafana** ○: dashboards, generated from code. **Alertmanager** ○:
routes alerts (to a pager, for a real team). **Exporters and cAdvisor**
○: collect metrics from Postgres, PgBouncer, Valkey, the host and the
containers; a managed platform's own container metrics replace them. All
with Prometheus, not before it.

## How sign-in works

The short answers:
- **Passwords:** the app never sees one. People type theirs on the
  identity provider's page, and the provider stores it salted and hashed
  (the demo Keycloak uses Argon2id). In production, that is the
  organisation's provider's job.
- **JWT:** exactly one, the ID token: the provider's signed statement of
  who signed in. The api checks it once, at sign-in, and never sends it
  to the browser.
- **The session is not a JWT.** It is 32 random bytes in a cookie;
  Postgres stores their SHA-256, and every request looks it up.
- **Salts** protect secrets people choose, because those can be guessed,
  so only passwords need them. A session token is 256 random bits, with
  nothing to guess: a plain SHA-256 is enough, and a slow salted hash
  would only slow every request. The database's own passwords are salted
  too: Postgres stores them as SCRAM-SHA-256.

**Step by step** (as a diagram: [security](docs/handbook/security.md#how-signing-in-works)):
1. **Sign in** opens `/api/auth/login`. The api makes three random
   values and keeps them for 10 minutes:
   - `state`, which ties the provider's answer to this browser (it is
     also set in a short-lived cookie);
   - a `nonce`, which ties the ID token to this sign-in;
   - a PKCE verifier, without which a stolen code is useless.
2. **The provider's page** takes the password, and MFA if the
   organisation uses it. The app sees neither.
3. **Back at `/api/auth/callback`** with a one-time code, the api checks
   `state`. It then trades the code, the verifier and its **client
   secret** for an ID token, on a direct connection the browser never
   sees.
4. **The ID token** is checked with PyJWT:
   - its signature, against the public keys the provider publishes
     (RS256 here; `none` and HMAC are refused);
   - its issuer, audience (this app), expiry and nonce.

   The provider also sends an access token. The api drops it: it calls
   nothing else as the user.
5. **Roles** come from the token's `groups` claim (`team:payments:responder`,
   `org:admin`). They replace the user's memberships at every sign-in.
6. **The session** starts: a new random token in the cookie, and its
   SHA-256 in the `sessions` table. From then on, each request costs one
   query and no call to the provider. The ID token stays in that row only
   to end the provider's session at sign-out.

**The cookie** is `__Host-triage_session` (plain `triage_session` in
development over http). It is `HttpOnly`, so scripts can't read it; it is
also `Secure` and `SameSite=Lax`. By default a session ends:
- 12 hours after sign-in;
- after 2 hours idle;
- at sign-out;
- when an operator ends it with `make revoke email=...`.

Every POST, PUT, PATCH and DELETE must also carry `Origin: <PUBLIC_URL>`
(the CSRF check).

**Why not a JWT as the session?** It would save one database read per
request, but:
- it cannot be ended before it expires, not even by signing out, unless
  you keep a list of revoked tokens, which is a session table again;
- it carries the roles it was issued with until it expires;
- it grows with every claim, and anyone holding it can read them: a JWT
  is signed, not encrypted;
- it adds a signing key to guard and rotate.

Here the read is part of the one query each request makes anyway
([operations](docs/handbook/operations.md#the-cost-of-signing-in)).
ADR-0013 has the alternatives.

**The secrets.** `make setup` generates each one (`openssl rand -hex 24`)
except the model provider's key, which you type in. Production refuses to
start while one of the api's own is still an example value (ADR-0023).

| Secret | Proves | To |
|---|---|---|
| `OIDC_CLIENT_SECRET` | this api is the registered application | the identity provider, when trading a code |
| `APP_DB_PASSWORD`, `POSTGRES_PASSWORD`, `MONITOR_DB_PASSWORD` | the api, the schema owner, the metrics exporter | Postgres |
| `LLM_API_KEY` | this service | the model provider (the mock needs none) |
| `ALERTMANAGER_WEBHOOK_TOKEN` | Alertmanager: a service, so a bearer token, not a session | the internal webhook, compared in constant time |
| `KEYCLOAK_ADMIN_PASSWORD`, `DEMO_USER_PASSWORD` | the demo provider's admin and users | Keycloak, locally only |
| `GRAFANA_ADMIN_PASSWORD` | the dashboards' admin | Grafana |

The provider's signing key never leaves the provider: the api fetches
only its public half. More detail:
- where secrets live on a laptop, a VM and a managed platform:
  [production](docs/handbook/production.md#secrets);
- every attack on sign-in, what stops it, and its test:
  [security](docs/handbook/security.md#each-attack-and-what-stops-it).

## When to add architecture, and when to take it away

**Start from the smallest shape that is still production-shaped:** the
"day one" list above. Every other piece waits for a trigger you can
name, and the name goes in the PR or an ADR:
1. **A measurement:** a number, and the command that produced it. The
   alerts index came from `make db-top-queries` under load; the fixed
   database pool from counting logins in PgBouncer.
2. **A requirement:** a written one. Company sign-in, an SLO, a
   compliance rule.
3. **A failure users would see,** shown by a drill. The rolling deploy
   came from 6.7 s of 502s, measured while a plain container
   replacement drained.

**Price it before adding it.** Each piece costs:
- an image to patch and scan;
- settings and secrets;
- a failure mode to decide and drill;
- a backup, if it holds state;
- an upgrade path;
- monitoring: an exporter, alerts, a runbook section;
- an ADR, and every new engineer's reading time.

Valkey, for rate-limit counters alone, cost: a fail-open decision
(ADR-0004), a fail-closed exception for the chat (ADR-0023), three
alerts, two drills, an exporter, and two sizing fixes under load.

**Take a piece away when:**
- nobody on the team can say what happens when it fails;
- a managed service does the same job. On AWS: an ALB with ACM instead
  of Caddy, RDS Proxy instead of PgBouncer, managed Prometheus and
  Grafana instead of the monitoring containers, ECR instead of GHCR;
- two things do one job (httpx beside httpx2);
- it was added "in case", and no measurement has needed it since. On
  that rule ADR-0021 removed five load-testing tools, the deep-debugging
  kit, two host rehearsals and three document types. Git history keeps
  them.

**Questions for a design review:**

| Question | Yes | No |
|---|---|---|
| Must the data survive a restart? | Postgres | memory is enough |
| Does the data belong to teams or customers? | row-level security, from the first table | the service's own checks |
| Must answers come from documents? | pgvector and full-text, in the same Postgres | no retrieval |
| Are the steps known before the model runs? | a pipeline: code decides what the model reads | an agent with tools (ADR-0020). Measured here, the agent cost 3.8× the prompt tokens for the same quality |
| Should other AI clients use the tools? | an MCP server over the same functions | nothing |
| Does more than one process share a limit, cache or lock? | Valkey | in-process |
| Will connections to Postgres near its limit? | PgBouncer, or RDS Proxy | connect directly |
| Does a VM serve HTTPS itself? | Caddy | the load balancer terminates TLS |
| Is someone on call? | alerts with tests, a dashboard, traces for the AI path | logs and `/metrics` |
| Many services, and a platform team to run a cluster? | Kubernetes can pay for itself | a managed container service (ECS, Cloud Run) |

**The same service at three sizes:**

| | A new project, week one | This repository (one VM) | On AWS 📘 |
|---|---|---|---|
| Compute | api (Uvicorn) and web in compose | gunicorn workers, rolling deploys | ECS on Fargate behind an ALB |
| Data | Postgres (with pgvector if it retrieves) | + PgBouncer, backups off the host | RDS or Aurora, Multi-AZ; RDS Proxy |
| Shared state | none | Valkey | ElastiCache for Valkey |
| Edge | localhost | Caddy, then nginx | ALB with ACM; CloudFront for the files |
| Sign-in | Keycloak locally | the organisation's provider | the organisation's provider |
| Model | the mock, and one provider | the mock, Ollama, a provider | Bedrock or a provider, behind the same seam |
| Watching | JSON logs, `/metrics` | Prometheus, Grafana, Alertmanager, Jaeger | managed Prometheus and Grafana, or CloudWatch; an OpenTelemetry Collector |
| Delivery | CI with tests and scans | + releases to GHCR, `make deploy` | + ECR, infrastructure as code (Terraform or CDK) |

The mapping container by container, with what changes in this repo:
[production](docs/handbook/production.md#moving-to-a-managed-platform).

## Adding a technology

Before a new dependency or service lands:
1. **Justify it in the PR description,** in one line: what it does that
   the stack cannot (AGENTS.md).
2. **Check:**
   - its licence (Redis and Docker Desktop both changed theirs);
   - that it is maintained, and its security record;
   - that it publishes arm64 images or wheels;
   - its size.
3. **Pin it** in a lockfile, or by image tag and digest, and make sure
   Dependabot and the scans cover it.
4. **Price it** (above), and write an ADR if a reader would ask "why
   this?".
5. **Add it here,** with its trigger. When it bites, add a line to the
   gotcha list in the [guide](gold_standard_development_guide.md).
