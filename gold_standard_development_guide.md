# Gold standard: building and running an AI service here

This repository is a small, complete AI service: alerts in Postgres,
owned by teams; sign-in with the organisation's identity provider; an
assistant that streams answers from any OpenAI-compatible model, about
the alerts the asker may see, with the steps from the team's own
runbooks, cited; a React UI. It is also the reference for how every such service here is built,
tested, shipped, watched and repaired. This file is the map. The details
live in the handbook chapters it links to.

It is written from what was **measured in this repo**, not from what
usually works. Every number has a command that reproduces it. Every
failure mode listed was injected and watched. ✅ means done and verified
here; 📘 means recommended practice not exercised here (usually because it
needs a real cloud account). Where the two differ, trust ✅.

## Read this first

| You are… | Read, in order |
|---|---|
| deciding about the project (not reading code) | [the overview](docs/overview.md) → [PRD-0001](docs/prd/0001-triage-assistant.md) → [ARD-0001](docs/ard/0001-triage-assistant.md) |
| starting a new service from this template | [using this template](docs/handbook/using-this-template.md) → [code style](docs/handbook/code-style.md) → [tech stack](docs/handbook/tech-stack.md) → the rules below |
| a new developer | Day one (below) → [dev environment](docs/handbook/dev-environment.md) → [daily work](docs/handbook/daily-work.md) → [testing](docs/handbook/testing.md) → the gotchas below |
| reviewing a PR | the rules and the gotchas below; [testing](docs/handbook/testing.md) (what each endpoint needs) |
| on call | [alert runbook](docs/runbooks/alerts.md) → [debugging](docs/handbook/debugging.md) → [failure modes](docs/handbook/failure-modes.md) |
| preparing a demo or a server | [the VM runbook](docs/runbooks/demo-vm.md) (includes the request to send IT) |
| on the infrastructure team | [environments and shipping](docs/handbook/environments-and-shipping.md) (the handoff table) → [infrastructure Q&A](docs/handbook/infrastructure-qa.md) (reproducibility, scale) → [networking](docs/handbook/networking.md) → [security](docs/handbook/security.md) |
| taking it to production | [going to production](docs/handbook/production.md) (stages, SLOs, canaries, game days, what was never tested) → [the VM runbook](docs/runbooks/demo-vm.md) |
| changing the prompt, the model or the provider | [AI engineering](docs/handbook/ai-engineering.md): evals, the judge, reasoning models, the prompt's measured history |
| changing runbook search, or the embedding model | [RAG](docs/handbook/rag.md): the pipeline, the measurements, `make reembed` → [the RAG debugging lab](labs/rag-debugging/README.md) |
| an answer got worse or slower | [AI observability](docs/handbook/ai-observability.md): read its trace → [the AI observability lab](labs/ai-observability/README.md) |
| deciding what to build next | [architecture](docs/handbook/architecture.md) → [failure modes](docs/handbook/failure-modes.md) (bottlenecks, single points of failure) → "Not done yet" below |

## The system on one page

```
                   ┌──────────────── one host (VM or laptop), one compose project ────────────────┐
browser ──:443───► │ edge (Caddy)  HTTPS, automatic certificates; :80 redirects to HTTPS          │
                   │   ├─ /auth/realms/triage/* ─► Keycloak (the bundled identity provider)       │
                   │   ▼ everything else: http://web:8080                                         │
                   │ nginx (web)  static React build, /api/* → api, JSON errors, security headers │
                   │   ▼ http://api:8010                                                          │
                   │ api  gunicorn → N uvicorn workers (N = CPU limit), FastAPI                   │
                   │   │  every request: session cookie → user and teams (one query)              │
                   │   ├─► PgBouncer :5432 (transaction pooling) ─► Postgres 17 + pgvector        │
                   │   │     alerts and runbooks (owned by teams), users, memberships, sessions   │
                   │   ├─► Valkey :6379 (rate-limit counters; if down, requests pass: fail-open)  │
                   │   ├─► the identity provider's back channel (code exchange, keys; Keycloak)   │
                   │   └─► the model: any OpenAI-compatible API (mock-llm locally), SSE to user;  │
                   │         its embeddings for runbook search                                    │
                   │                                                                              │
                   │ Prometheus ◄─ scrapes api, exporters, cAdvisor ─► Grafana; Alertmanager ─►   │
                   │   api (alerts appear in the app); Jaeger ◄─ api's traces (OTLP)              │
                   │                                             [dashboards on 127.0.0.1 only]   │
                   └──────────────────────────────────────────────────────────────────────────────┘
```

In production the organisation's identity provider (Entra ID, Okta,
Keycloak...) replaces the bundled Keycloak: `OIDC_*` settings, no code
change ([security](docs/handbook/security.md)).

Four request paths matter:
- **Sign in:** the browser goes to `/api/auth/login`, then to the identity
  provider's page. The provider sends it back to `/api/auth/callback`. The
  api exchanges the code over its back channel, verifies the ID token,
  copies the user's teams and roles from its groups claim, and sets a
  session cookie.
- **Read alerts:** edge (TLS) → nginx → api (session → user → teams) →
  PgBouncer → Postgres. Only the caller's teams' alerts, ~4 ms at
  500 req/s.
- **Ask the assistant:** edge and nginx (no buffering) → api finds the
  runbook sections that match the question (full-text and vector search,
  the asker's teams only) and loads the asker's visible alerts →
  redacts credentials → streams the model's answer as Server-Sent
  Events, with a heartbeat every 15 s, then the sections it cited. Stop
  in the browser cancels the model call. With tracing on, each step is a
  span of one trace.
- **Alert webhook:** Alertmanager → api (bearer token) → a row in
  Postgres, in the team its `team` label names.

The same images run everywhere. Only configuration changes: `.env` on a
host, a secret store on a platform.

```
apps/api/        FastAPI service (Python 3.13, uv): app/, evals/ (the model's evals, the retrieval benchmark), tests/{unit,integration}, migrations/
apps/web/        React + Vite UI (Node 24); nginx config for production
tools/           mock-llm (a provider stand-in with failure modes, chat and embeddings), py-spy and load-tool images
labs/            hands-on exercises, one fault at a time: rag-debugging (each stage of runbook retrieval)
tests/           e2e (Playwright through production nginx), load (k6, Locust, vegeta, JMeter, Artillery)
infra/           observability (Prometheus rules + tests, Alertmanager, Grafana as code), postgres roles, keycloak (the demo realm), vm (cloud-init)
scripts/         deploy, backup, restore, failure drills, fresh-host test, SQL helpers, debug scripts
docs/            overview.md, handbook/ (the chapters), runbooks/, adr/ (decisions), prd/ ard/ rfc/ rfq/ design-docs/ (templates + this project's own)
compose*.yaml    base / dev (auto-merged) / prod shape / test / debug overlays
Makefile         every command; `make` lists them
```

## Day one: from clone to a merged change

1. Install Docker (Engine on Linux; Docker Desktop, Colima or OrbStack on
   macOS; WSL2 on Windows: see [dev environment](docs/handbook/dev-environment.md)),
   plus make and git. Not Python, Node or Postgres.
2. `git clone https://github.com/Parzon/triage-assistant.git && cd
   triage-assistant`
3. `make setup` creates `.env` from `.env.example` and builds the images.
4. `make up && make ps`: every service `(healthy)`. Open
   http://localhost:5173, sign in as `alice` (password: `DEMO_USER_PASSWORD`
   in `.env`), and ask the assistant about an alert. `bob`, `carol` (org
   admin) and `dave` (in no team) show the other roles.
5. `make check`: lint, types, all tests, as CI runs them (~40 s).
6. Pick an issue. `git switch -c fix/<issue>-<what>`. Change code with hot
   reload running. Add tests for the success and the failure paths.
7. `make prod-up && make e2e` if you touched anything a browser or nginx
   sees.
8. Push, open a PR (`Closes #N`, how you verified it, one line per new
   dependency). CI's five checks must pass. Squash-merge.

## The rules

Each rule exists because breaking it cost something measurable here.

1. **Everything runs in containers; the Makefile is the interface.** No
   host toolchains, so no "works on my machine": the image is the
   environment. ([dev environment](docs/handbook/dev-environment.md))
2. **Prove it in the production shape before calling it done.**
   Streaming worked in dev and was fully buffered by production nginx. A
   dev dependency masked a missing runtime one. `make prod-up`, `make
   e2e`, `make image-check`. ([testing](docs/handbook/testing.md))
3. **Build once, promote the same image.** A `vX.Y.Z` tag publishes it;
   hosts pull it by tag; `latest` is never deployed.
   ([shipping](docs/handbook/environments-and-shipping.md), ADR-0011)
4. **Configuration from the environment; secrets never in git, images or
   logs.** A leaked secret is rotated, not deleted.
   ([security](docs/handbook/security.md))
5. **Every endpoint is tested on its failure paths**: dependency down,
   dependency hung, invalid input, rate limit. Not just the happy path.
   ([testing](docs/handbook/testing.md))
6. **Every wait is bounded, and bounded where the work happens.** Server
   side: `statement_timeout`, PgBouncer's timeouts. Client-side query
   timeouts leaked connections here. Failures answer in JSON with a
   request id. ([ADR-0010](docs/adr/0010-database-timeouts-and-failure-behaviour.md))
7. **Liveness is not readiness.** `/health` checks nothing external.
   `/ready` checks hard dependencies, and reports soft ones (Valkey)
   without failing. ([failure modes](docs/handbook/failure-modes.md))
8. **Nothing blocks the event loop.** One synchronous call inside async
   code made `/health` take 4.8 s, and linters did not notice.
   ([performance](docs/handbook/performance.md))
9. **Metrics have bounded labels; dashboards and alerts are code, and
   alerts have tests.** ([observability](docs/handbook/observability.md))
10. **Migrations are backward compatible and never lock a table
    silently**: expand/contract, `lock_timeout`, concurrent indexes.
    ([daily work](docs/handbook/daily-work.md))
11. **Deploy with `make deploy`, never by recreating a live container.**
    A recreate refused requests for the whole drain. The rolling deploy
    dropped none. ([VM runbook](docs/runbooks/demo-vm.md))
12. **Every container: non-root, read-only root filesystem, no
    capabilities, CPU and memory limits, no swap.**
    ([security](docs/handbook/security.md))
13. **Only the front door is published.** Published ports bypass the host
    firewall; everything else binds to 127.0.0.1.
    ([networking](docs/handbook/networking.md))
14. **Measure before optimising; one change at a time; keep the numbers.**
    Open-model load, production-sized data.
    ([performance](docs/handbook/performance.md),
    [load testing](docs/handbook/load-testing.md))
15. **Drill the failures, and re-drill after changing the path.** The
    worst bug here (a permanent pool leak) appeared only when a database
    froze under load. ([failure modes](docs/handbook/failure-modes.md))
16. **Pin everything**: lockfiles with hashes, image tags, Actions by
    commit SHA; updates arrive as PRs.
    ([security](docs/handbook/security.md))
17. **Write the decision down.** An ADR for any "why is it like this?",
    a runbook for any procedure needed under pressure, a line in the
    gotcha list below for any trap.
18. **Access is decided on the server, from the identity provider, in one
    place.** Every data route takes the caller's `Principal`. Reads go
    through the shared visibility query. What a caller cannot see is a 404.
    The model is only ever given what the asker may see. Hiding a button
    is never the control. Postgres enforces the same rule underneath
    (row-level security), so a forgotten filter returns nothing rather
    than leaking. ([security](docs/handbook/security.md), ADR-0013,
    ADR-0014)
19. **A prompt or model change ships with eval runs before and after.**
    The whole suite, repeated, against the baseline, with a calibrated
    judge. Every prompt fix here had a side effect elsewhere, and a
    2.5% failure showed only in 200 runs.
    ([AI engineering](docs/handbook/ai-engineering.md), ADR-0016)
20. **A safety property that matters goes in code, not in the prompt.**
    A prompt rule is a probability: "never repeat credentials" failed
    about 1 run in 100. Redacting them before any model call cannot fail
    for the formats it knows. ([security](docs/handbook/security.md))
21. **Telemetry is a copy of the data.** Logs and traces carry ids,
    counts and durations, never questions, prompts, answers or document
    text: the stores they land in ignore the access rules the service
    enforces. ([AI observability](docs/handbook/ai-observability.md),
    ADR-0018)
22. **Whoever writes what the model reads is on record.** Every change to
    runbooks and alerts, and every question with the versions it was
    given, is an audit event in the change's own transaction, in a table
    the api can add to but not rewrite. A new way to feed the model (a
    sync, a tool) records its events too.
    ([AI security](docs/handbook/ai-security.md), ADR-0019)

## The handbook

| Chapter | Read it when |
|---|---|
| [Using this template](docs/handbook/using-this-template.md) | starting a new service from this repo: rename, settings, package visibility, replacing the domain |
| [Code style](docs/handbook/code-style.md) | writing code here: functions or classes, errors, async, tests, rules for AI engineering teams and AI coding agents |
| [Tech stack](docs/handbook/tech-stack.md) | every technology: why it, what else, its version, its trap (Playwright's three packages included) |
| [Development environment](docs/handbook/dev-environment.md) | setting up a machine: Linux, macOS, Windows, Apple Silicon, corporate proxies |
| [Daily work](docs/handbook/daily-work.md) | adding a dependency, an endpoint, a setting, a migration, a metric; the Git workflow; **every setting, in one table** |
| [Testing](docs/handbook/testing.md) | writing tests; what each layer proves; which layer caught which real bug |
| [Debugging](docs/handbook/debugging.md) | something is wrong: symptom → tool, how each tool works, real output |
| [Observability](docs/handbook/observability.md) | adding metrics, panels or alerts; reading the dashboard |
| [AI observability](docs/handbook/ai-observability.md) | an answer got worse or slower: tracing it through retrieval and the model; what spans may hold; what tracing costs; with a hands-on [lab](labs/ai-observability/README.md) |
| [Performance](docs/handbook/performance.md) | something is slow; capacity numbers; how the bottlenecks were found |
| [Load testing](docs/handbook/load-testing.md) | choosing a tool; open vs closed models; reference scripts for six tools |
| [Networking](docs/handbook/networking.md) | Docker networking, nginx, load balancers, a cloud network design |
| [Environments and shipping](docs/handbook/environments-and-shipping.md) | laptop → CI → staging → production; managed-platform mapping; the infra handoff |
| [Architecture](docs/handbook/architecture.md) | the monolith, what to split first and when, the scaling path |
| [AI engineering](docs/handbook/ai-engineering.md) | changing the prompt or the model; writing eval cases; trusting an LLM judge; reasoning models; a real model on your machine |
| [RAG](docs/handbook/rag.md) | runbook search: how retrieval works, a team filter under a vector index, choosing an embedding model, `make reembed`; with a hands-on [debugging lab](labs/rag-debugging/README.md) |
| [Security](docs/handbook/security.md) | sign-in and roles (and connecting your identity provider), secrets, least privilege, exposure, supply chain, LLM-specific risks |
| [AI security](docs/handbook/ai-security.md) | what the model reads and what it can affect: redaction (measured), the audit trail, output handling, and the rules before the assistant gets tools; with a hands-on [lab](labs/ai-security/README.md) |
| [Failure modes](docs/handbook/failure-modes.md) | what happens when each part fails (measured), SPOFs, bottlenecks, game days |
| [Going to production](docs/handbook/production.md) | the stages to real users and their exit criteria; SLOs; canaries; game days; incidents; everything never tested |
| [Infrastructure Q&A](docs/handbook/infrastructure-qa.md) | an infrastructure team's questions: reproducibility, scale, limits, backups, Kubernetes |
| Runbooks: [alerts](docs/runbooks/alerts.md), [one VM](docs/runbooks/demo-vm.md) | an alert fired; setting up or operating a server |

## Gotchas: the complete list

Every trap met while building this repo, with what fixed it. One line
each; the linked chapter has the evidence. Add to this list whenever
something bites.

### Docker and Compose
- **A variable in `.env` reaches a container only if its service lists
  it** under `environment:`. 22 of the api's settings could not be set from
  `.env`, silently. List each with no value (`DB_POOL_SIZE:`): passed when
  set, absent otherwise, so the code's default applies. `make lint` checks
  every setting is listed.
- **`.env` is shared by every compose project in the directory.** On a host
  running both the dev and the production stack, `IMAGE_TAG` (recorded by
  deploys) made the dev api report the release's version, and both stacks'
  monitoring wanted the same ports. Give the dev one other ports on the
  command line (`GRAFANA_PORT=3001 ... make obs-up`).
- **The Dev Containers CLI attaches to an already running compose
  container without building its features**: a user added by a feature
  didn't exist ("unable to find user dev"). Put the user in the image
  itself (the dev image's `dev` user, with your UID).
  ([dev environment](docs/handbook/dev-environment.md))
- **After the dev container switched to a non-root user, mypy died with
  "INTERNAL ERROR"**: its old cache was root-owned. `make fix-perms`
  once.
- **`docker compose up --build` after a dependency change still runs the
  old dependencies.** The anonymous `.venv`/`node_modules` volume
  survives. Use `make rebuild` (`--renew-anon-volumes`).
  ([daily work](docs/handbook/daily-work.md))
- **Published ports bypass ufw.** DNAT happens in `PREROUTING`, before
  the `INPUT` rules. Bind to 127.0.0.1; filter in `DOCKER-USER`.
  ([networking](docs/handbook/networking.md))
- **Docker never restarts an *unhealthy* container.** Restart policies
  act on exit only; "unhealthy" matters to `depends_on` and orchestrators.
- **`docker kill` counts as a manual stop:** the container stayed down
  (exit 137, RestartCount 0). Simulate crashes with SIGKILL from the host
  PID namespace. ([failure modes](docs/handbook/failure-modes.md))
- **Inside a container, PID 1 ignores SIGKILL** sent from within its own
  PID namespace.
- **A stopped container vanishes from Docker DNS.** Clients get a name
  resolution error (`gaierror`), not "connection refused": map it to 503.
- **Docker hands out the lowest free IP.** Sequential test containers
  shared an IP and looked like a single global rate limit.
- **`-f` turns off the automatic `compose.override.yaml` merge**: list it
  explicitly with an overlay.
- **`--profile X` replaces `COMPOSE_PROFILES` from `.env`**: the mock LLM
  dropped out. Set the full list instead.
- **Compose merging:** `ports` concatenate, `environment` merges by key,
  and `!reset` clears.
- **An empty variable is not an unset one.** `${WEB_CONCURRENCY:-}` passed
  `""`, and gunicorn crashed parsing it at import.
- **A duplicate YAML key**: compose rejects it, but many YAML parsers
  silently keep the last one.
- **`docker compose run` never rebuilds an existing image**: tests ran on
  stale dependencies. Use `run --build`.
- **A memory limit without `memswap_limit` allows as much again in
  swap.** A 50 MB limit reached 95 MB, silently, with no OOM.
  ([failure modes](docs/handbook/failure-modes.md))
- **Lowering a live container's memory limit OOM-kills processes even
  with swap allowed**: reclaim gives up quickly. Use it for drills only.
- **Docker's `OOMKilled` flag can read `false` after PID 1 was
  OOM-killed.** Read the kernel log and cAdvisor's counter.
  ([debugging](docs/handbook/debugging.md))
- **An OOM-killed gunicorn worker leaves the container "healthy"**: the
  only traces are a log line, the cgroup counter and the alert.
- **A directory bind mount pins the directory, not the path.** `git
  checkout` recreated it, and Prometheus kept an empty view until its
  reload failed. Recreate the container.
- **`docker events --since` can't look back**: the daemon keeps 256
  events, and healthchecks filled them in 44 s. Stream events to the
  journal.
- **A rolling deploy renames the container** (`api-2`, `api-3`): never
  hardcode a container name; use `docker compose ps -q api`.
- **`docker compose up` without the deployed `IMAGE_TAG` silently rolls
  back**: `make deploy` records the tag in `.env`.
- **A value in `.env` is visible to compose, not to your scripts**:
  `deploy.sh` built the edge's image name from `$IMAGE_PREFIX` (unset in
  its shell), found no such image, took "none" for "unchanged", and never
  replaced the edge on a GHCR host. A v0.1.0 edge would have stayed, with
  no `/auth` route. Ask compose (`config --images`), and fail when the
  answer is empty.
- **A rollback ran the old image's migrations**, and old Alembic has never
  heard of the new revision: "Can't locate revision". The deploy failed
  safely, but rolling back was impossible across any migration. Now a
  database ahead of the image means a code-only rollback (ADR-0015).
- **`COPY` records file times, and every CI checkout gets new ones**: a
  rebuilt edge never had the same layers, so every release replaced it
  (~2 s refused). CI stamps the image with a hash of its inputs, and the
  deploy compares that.
- **`make prod-up` on a host that runs releases** would build the
  checkout and name it like the release: it refuses when `IMAGE_PREFIX`
  is a registry.
- **`docker run --rm` then `docker logs`**: the logs went with the
  container.
- **Piping a script into `docker run` without `-i`**: nothing runs, and
  there's no error.
- **`docker logs --since <timestamp without a zone>` is local time.** Use
  relative times.
- **Container-created files in bind mounts come out owned by root.** Run
  as your UID (`AS_ME`), or `make fix-perms`.
- **Docker-in-Docker needs a volume for `/var/lib/docker`**: overlay
  can't stack on overlay.
- **Postgres refuses a data directory from an older major version**
  (16 → 17): dump/restore or `pg_upgrade`. The `postgres:18` image also
  moved `PGDATA`.
- **Docker Hub limits pulls per IP**, and an office shares one IP: log in,
  or use a mirror. ([dev environment](docs/handbook/dev-environment.md))
- **Docker Desktop needs a paid licence** above 250 employees or $10 M
  revenue.
- **Slim images have no `ps`**: use `docker top` from the host.
- **"port is already allocated"** is usually a forgotten stack: `docker
  ps`, `ss -ltnp`.

### The edge, nginx and the network
- **The official Caddy image won't start with `no-new-privileges`**: its
  binary carries a file capability, and the kernel refuses to run it
  (EPERM). Remove the capability; bind 80/443 as non-root through the
  `net.ipv4.ip_unprivileged_port_start` sysctl.
  ([networking](docs/handbook/networking.md))
- **Behind two proxies, every user had the edge's address**: one rate
  limit for all. The edge overwrites the client's `X-Forwarded-For`;
  nginx trusts it only from private ranges (`realip`).
- **HSTS on `localhost` forces HTTPS on every local port**, the Vite dev
  server included: `HSTS_MAX_AGE=0` locally, a year only for a real
  domain.
- **With remapped host ports, Caddy's redirect and `alt-svc` name 443**:
  it knows its container ports, not the host's. It's right on a VM using
  80/443.
- **Replacing nginx refused connections for ~0.3 s** until the edge
  retried the upstream (`lb_try_duration 5s`): a rolling deploy then
  failed nothing.
- **Let's Encrypt stopped sending expiry emails (2025)**: monitor
  certificate expiry yourself.
- **Pebble's release `v2.10.1` is image tag `2.10.1`**: GitHub release
  names and image tags don't always match.
- **nginx buffers responses**: SSE arrived all at once. Set
  `proxy_buffering off` on the stream location, and send
  `X-Accel-Buffering: no`. ([networking](docs/handbook/networking.md))
- **nginx resolves an upstream name once at start**, then connects to a
  dead IP forever. Use `resolver 127.0.0.11` plus `server api:8010
  resolve`.
- **Behind a proxy, every client has the proxy's IP**, so the rate limit
  was global. nginx overwrites `X-Forwarded-For`; gunicorn trusts it only
  from nginx.
- **Appending to `X-Forwarded-For` lets clients forge their IP**:
  overwrite it at the edge.
- **The side that closes idle keep-alive connections must be the
  proxy**: nginx 60 s < gunicorn 75 s.
- **nginx doesn't retry a POST on a reset keep-alive connection**: that's
  a 502. Worker recycling caused bursts of them.
- **A request on an existing keep-alive connection to a vanished
  upstream waits the read timeout** (30 s), not the 2 s connect timeout.
- **`$host` drops the port**: use `$http_host` when the port matters.
- **Starlette's slash redirects behind a prefix-stripping proxy** pointed
  at the wrong path and port: `redirect_slashes=False`.
- **nginx's own 502/504 pages are HTML**: `error_page 502 504` → a JSON
  location, status kept.
- **An `add_header` in a location discards every inherited
  `add_header`**: include the security headers in each location.
- **`server_tokens on` advertises nginx's version.**
- **Load balancers close idle connections** (ALB: 60 s): the SSE heartbeat
  (15 s) must stay below the smallest idle timeout in the path.
- **Behind a load balancer**, trust `X-Forwarded-For` only from its subnet
  (the realip module), or the rate limit is per load balancer.

### Python, async, FastAPI, gunicorn
- **A synchronous call in async code blocks every request on the
  worker.** `ruff --select ASYNC` did not flag an SDK's sync client.
  ([performance](docs/handbook/performance.md))
- **On a busy event loop, every wall-clock timeout fires early**: limiter
  fail-opens and pool timeouts at 46% CPU. Watch `event_loop_lag_seconds`.
- **`asyncio.timeout()` cancels once, and cleanup can block again**
  afterwards: a readiness probe took over 10 s despite a 2 s timeout.
  Abandon the task instead. (ADR-0010)
- **A second `task.cancel()` before the first is delivered merges into
  one.**
- **Done-callbacks run one loop iteration after the task finishes.**
- **`StreamingResponse` never `aclose()`s your generator**: a cancelled
  stream kept the model call running. Wrap it in `contextlib.aclosing`.
- **Starlette's exception handler runs outside your middleware**: the
  request id was lost on 500s. Handle errors in the middleware.
- **`logging.dictConfig` inside the app factory removed pytest's
  `caplog`**: configure logging in `asgi.py`.
- **`async with lifespan_context(app) as x` binds `None`**, not the app.
- **`assert` disappears under `python -O`**: use `isinstance` plus
  `raise` for runtime checks.
- **`os.cpu_count()` ignores container CPU limits.** Worker count is read
  from cgroup `cpu.max`. "2 × cores + 1" is the *sync*-worker heuristic;
  async workers need about one per core.
- **gunicorn's `max_requests` recycling caused 502 bursts under load**:
  keep it off without a measured leak.
- **gunicorn's control socket defaults to `$HOME`**, which is read-only:
  put it in `/tmp`.
- **`uvicorn --reload` runs the app in a child process with stdin on
  `/dev/null`**: pdb needs a foreground server without reload, and
  debugpy can't debug the child.
  ([debugging](docs/handbook/debugging.md))
- **A paused breakpoint stops the whole event loop**: every request on
  that worker waits.
- **A forgotten `breakpoint()` hangs a production worker** until gunicorn
  kills it: `PYTHONBREAKPOINT=0` in images.
- **prometheus_client reads `PROMETHEUS_MULTIPROC_DIR` at import**: the
  directory must exist first, and the variable must never be `""`. An
  empty value still turns multiprocess mode on (it checks presence). A
  one-off CLI in the server's container must remove the variable before
  its imports. ([observability](docs/handbook/observability.md))
- **The openai SDK depends on `httpx2`, not `httpx`.** Importing `httpx`
  worked only because it was a dev dependency, and the production image
  crashed.
- **The openai SDK's defaults are a 600 s read timeout and 2 retries**:
  set both explicitly.
- **openai 3.17 wraps transport errors even mid-stream**, and
  `APITimeoutError` subclasses `APIConnectionError`: catch it first.
- **`httpx2` logs every request at INFO**: set it to WARNING.
- **pydantic's `model_copy()` skips validators**: validate values set that
  way yourself.
- **anyio's default thread pool has 40 slots**: sync endpoints queue
  behind a hung dependency.

### Database: Postgres, PgBouncer, SQLAlchemy, asyncpg, Alembic
- **PgBouncer's default md5 auth can't answer Postgres' SCRAM**, so every
  query failed, while `pg_isready` stayed green (it doesn't
  authenticate). Use `AUTH_TYPE=scram-sha-256`, and `/ready` runs a real
  query.
- **A `SET` before Alembic's `begin_transaction()` opens a transaction
  first**: the migration logged success and was rolled back. Use `SET
  LOCAL` inside it.
- **Transaction pooling ignores session `SET`s**: put limits on the role
  (`statement_timeout`).
- **asyncpg's `command_timeout`, or cancelling its task, sends a cancel
  and then waits for the acknowledgement forever** if the connection dies
  first: 13 of 40 pool connections leaked. No client-side query timeout.
  (ADR-0010)
- **SQLAlchemy's checkout event fires only after the pre-ping
  succeeds**: a gauge built on it missed stuck requests. Sample
  `pool.checkedout()`.
- **SQLAlchemy discards overflow connections on return**: bursty load
  churned them (196 logins in 20 s), a metastable slow state. Pool 20,
  overflow 0. ([performance](docs/handbook/performance.md))
- **Pre-ping adds round trips per checkout**, and can double the wait
  during a frozen database: one run took 10.6 s instead of 5.3 s, most
  likely two PgBouncer queue waits in a row.
- **`team_id = ANY(:teams) ORDER BY created_at DESC LIMIT n` lets the
  planner walk the global time index and filter**: 13 ms (103k rows
  discarded) when one of a user's teams was large but quiet, growing with
  the table. A LATERAL join reads each team through its own index:
  0.06 ms, bounded. ([performance](docs/handbook/performance.md))
- **Adding a validated foreign key blocks writes for the whole scan**:
  add it `NOT VALID`, then `VALIDATE CONSTRAINT` in its own transaction.
- **An unnamed constraint cannot be dropped by a downgrade** that Alembic
  generated: name every constraint.
- **FastAPI's `yield` dependencies close after a streamed response has
  been *sent***: a request-scoped database session held its connection
  for the whole answer. `Depends(..., scope="function")`.
- **Coverage reports lines after an `await session...` as never run**:
  SQLAlchemy's asyncio layer switches greenlets. `concurrency =
  ["greenlet", "thread"]`.
- **A connection closed mid-query is a generic `DBAPIError`**, not an
  `OperationalError`: classify by SQLSTATE (08*, 57P01-3, 53300).
- **PgBouncer's defaults suit batch jobs, not a web app**:
  `query_wait_timeout` 120 s, `server_login_retry` 15 s, and
  `dns_nxdomain_ttl` 15 s, which kept failing after Postgres was back.
- **An idle `EXPLAIN` is not a load test**: 43 ms idle, p95 7 s at 40
  req/s.
- **A DDL statement waiting on a lock makes every later query on the
  table wait behind it**: `lock_timeout` on migrations.
- **`CREATE INDEX` blocks writes for the whole build**: use
  `CONCURRENTLY`, outside a transaction.
- **Alembic's autogenerate writes a rename as drop plus add** (data
  lost), and misses some constraints: read every generated migration.
- **`CREATE TABLE ... (LIKE x INCLUDING DEFAULTS)` doesn't copy
  `IDENTITY`**: add `INCLUDING IDENTITY`.
- **Monitoring has a cost**: the exporter's statistics query was 25% of
  DB time on an idle database.
- **Docker's default 64 MB `/dev/shm` is too small for parallel
  queries**: `shm_size: 256mb`.

### Sign-in and access
- **`SameSite=Strict` breaks sign-in with a real identity provider**: the
  provider sends the browser back from another site, and a Strict cookie
  stays behind. The bundled Keycloak, on the app's own origin, would not
  show it. Lax, pinned by a test. ([security](docs/handbook/security.md))
- **Keycloak in Docker has two addresses**: browsers use the public one,
  the api uses `keycloak:8080`, and tokens must name one issuer.
  `KC_HOSTNAME` (a full URL) plus `KC_HOSTNAME_BACKCHANNEL_DYNAMIC`, and
  `OIDC_DISCOVERY_URL` for the api.
- **Keycloak publishes an encryption key in the same key set**
  (`use: enc`, RSA-OAEP), which PyJWT cannot load. Keep `use: sig` keys
  only.
- **A cached provider hides its own outage**: sign-in redirects came from
  cached metadata, so no request failed while the provider was down. A
  30 s check, `/ready` and an alert (`IdentityProviderDown`).
- **Authentication doubled the CPU of the cheapest request.** A query per
  table and a transaction of its own made p95 1.07 s at 1,000 req/s.
  One query, in the request's transaction: 19.7 ms.
  ([performance](docs/handbook/performance.md))
- **Deleting a `__Host-` cookie needs `Secure` too**: browsers ignore a
  `Set-Cookie` for that prefix without it.
- **RFC 6749 form-encodes the client id and secret before base64**
  (`client_secret_basic`): a secret with `+` or `%` fails against strict
  providers otherwise.
- **Entra ID's `groups` claim holds object ids, not names**: use app
  roles (`OIDC_GROUPS_CLAIM=roles`). Google has no groups claim, and no
  sign-out endpoint.
- **An email is not an identity**: emails change and get reassigned. Key
  users on `(issuer, subject)`.
- **Setting the tenant with a session-level `SET` through PgBouncer leaks
  it to the next client** on that server connection: `set_config(...,
  true)`, local to the transaction, tested.
- **Under row-level security, a query that forgot the context returns
  nothing, silently**: a test fixture's `DELETE FROM alerts` as the app
  role deleted 0 rows. Declare the context (the fixture runs as an org
  admin). Querying as the owner hides the policies entirely: test as the
  app role.
- **A setting reads `NULL` before it was ever set in a connection, and
  `''` after the transaction that set it ended**: `''::bigint[]` is an
  error. `nullif(..., '')` in the policy.
- **A function call in a policy runs per row**: wrapped in a scalar
  subquery, `(SELECT f(...))`, it is an InitPlan evaluated once. 44 ms
  against 100 ms over 1 M rows. But `= ANY ((SELECT ...))` is the
  *subquery* form of ANY (`bigint = bigint[]` error): cast it,
  `(SELECT ...)::bigint[]`.
- **`INSERT ... RETURNING` also needs the SELECT policy** to allow the
  new row: the Alertmanager service reads every team, not only writes.
- **The contract migration must not run before every instance sends the
  context**: against the release before it, every alert would vanish.
  Releases are never skipped (v0.2.0 → v0.3.0).
- **A compose `${VAR:?message}` is checked for every service, including
  ones whose profile is off**: it would force demo passwords on
  deployments without the bundled Keycloak. The check lives in the
  Keycloak container's command.
- **Keycloak's image has no curl or wget**: its healthcheck speaks HTTP
  with bash's `/dev/tcp`, on the management port (9000), under the same
  `/auth` prefix.
- **Keycloak imports a realm only when it doesn't exist**: after editing
  the realm file, recreate the container (its database lives inside it).
- **make echoes recipes, secrets included**: pass them to `docker run` as
  `-e NAME` (the value from the environment), never `-e NAME=value`.
- **Chrome logs a 401 response as a console error**: a "no console
  errors" browser test must run signed in.
- **Importing a helper from a Playwright setup file registers its setup
  tests again** in the importing spec: shared constants go in their own
  module.

### Valkey / Redis
- **redis-py's socket timeouts must be set explicitly**, with a bounded
  pool: a hung Valkey otherwise holds requests (older versions waited
  forever).
- **Redis 7.4 moved to source-available licences** (8.x adds AGPLv3):
  Valkey is the BSD-licensed fork, with the same protocol (ADR-0002).
- **`allkeys-lru` evicts counters when full.** That's fine for rate
  limits, wrong for data you must keep.

### Streaming, the model, the frontend
- **The browser's `EventSource` can't send a POST body**: use `fetch` and
  read the stream.
- **Raw SSE `data:` lines can't carry a model's newlines**: JSON-encode
  each event (ADR-0007).
- **A silent stream is closed by proxies**: heartbeat comments every
  15 s.
- **React reused one `<button>` for Ask and Stop**, and its type flipped
  mid-click, so Stop re-submitted. jsdom didn't reproduce it; Playwright
  did. Use distinct `key`s. ([testing](docs/handbook/testing.md))
- **A stream that ends without `done` or `error` left the UI spinning**:
  treat it as `stream_incomplete`.
- **Model output is untrusted**: render it as text, never HTML.
  ([security](docs/handbook/security.md))
- **Alert text reaches the prompt, so anyone who can send an alert can
  attempt prompt injection.** There are no tools, so the worst case is a
  wrong answer.

### The model, the prompt and evals
All measured with gpt-oss:20b and gemma3:27b; the evidence is in
[AI engineering](docs/handbook/ai-engineering.md).
- **A reasoning model's thinking counts against the output limit.** At
  its default effort, gpt-oss spent all 800 tokens thinking in 3 answers
  of 5 and said nothing. Set `LLM_REASONING_EFFORT=low`, and treat an
  empty answer as an error (`llm_empty_answer`), never a blank success.
- **Only send parameters the model supports.** `reasoning_effort` makes
  models without reasoning answer HTTP 400. OpenAI's reasoning models
  refuse any temperature but 1.
- **More reasoning is not more safety.** At medium effort, v4 printed
  its prompt 1 run in 3, and cost 3× the tokens.
- **Every prompt rule has side effects.** "If there are no alerts, say
  so" made the model say "There are no alerts." when there was one, 8
  runs in 40. Run the whole suite, not only the case you fixed.
- **Small samples hide rare failures.** 110 passing calls missed a
  failure that happens 1 run in 40. Zero failures in *n* runs means a
  rate below about 3/*n*.
- **"Passed before, fails now" is not a regression.** A case that fails
  1 run in 30 would "regress" a third of the time. The gate compares
  failure counts with a one-sided Fisher exact test (p < 0.05).
- **One failure is an anecdote, not a cause.** A marker blamed for 1
  failure in 10 caused 0 in 60 when put back (p = 0.14). Write a cause
  into a comment only once it reproduces.
- **A check that passes a wrong answer is worse than none.** Read the
  answers, not just the pass rate.
- **Model output uses typography**: a non-breaking hyphen in `db‑1`, a
  curly apostrophe. Normalise before substring checks.
- **A check that quotes the prompt goes blind when the prompt is
  reworded.** Compare against the current prompt.
- **An LLM judge fails silently unless a missing verdict fails the run.**
  Here it inherited a parameter it rejects, and every check "passed".
- **Calibrate the judge on labelled answers, including bad ones.**
  Laid out as `Question: … Answer: …`, it graded correctness and passed
  "The capital of France is Paris." as a refusal. Negated criteria
  ("without claiming…") passed wrong answers 3 runs in 3.
- **Safety cases never rest on the judge alone.** It reads output from
  a model that may have been injected.
- **A GPU container can lose its GPU and keep running.** Ollama fell back
  to the CPU with no error: a minute's work had not finished in ten.
  `nvidia-smi` inside said "Failed to initialize NVML". `ollama ps`
  shows the processor. Recreate the container.

### AI security
The evidence is in [AI security](docs/handbook/ai-security.md) and
[the lab](labs/ai-security/README.md).
- **`\bpassword` never matches `DB_PASSWORD`**: `_` is a word character.
  The first redactor caught 17 of 54 secrets in real log shapes. Match
  names that *end* with the word, and leave `max_tokens` alone.
- **Score patterns on a held-out set.** Written against a set, they score
  well on it by construction: 54 of 54 on the tuning set, 21 of 24 on
  shapes written first and not used to tune.
- **An unbounded scan in a regular expression is a denial of service** on
  text others write. Restarting at every "mysql", 100,000 characters took
  6.4 s on the event loop. Bound every scan (`{0,200}`), and test with
  hostile input that the time stays linear.
- **`INSERT ... RETURNING` is checked against the SELECT policy.** A table
  only org admins may read refuses inserts that ask for their id back:
  insert with `Insert.inline()`. `implicit_returning=False` does not help:
  SQLAlchemy then fetches the id itself, and `GENERATED ALWAYS` refuses
  it.
- **Append-only needs the grant revoked, not only a missing policy.**
  Without an UPDATE policy, row-level security turns an update into "0
  rows", silently. The revoked grant makes it an error, which gets noticed.
- **Tests cannot clean an append-only table.** Find each test's rows by
  action and target, or by a user made for the test; a runbook and an
  alert can share an id.

### Runbook retrieval (RAG)
Measured with nomic-embed-text and gpt-oss:20b; the evidence is in
[RAG](docs/handbook/rag.md) and [the lab](labs/rag-debugging/README.md).
- **A team filter under an approximate vector index can find nothing.**
  HNSW hands back its 40 nearest rows, then the filter runs. With 1% of
  the rows the caller's, a one-team search found 0 of 20, and nothing
  failed. Send the filter as a plain `team_id = ANY(...)`, never behind
  an OR, and turn on iterative scans (pgvector 0.8+) where there is no
  filter. `make rag-overfiltering-lab` shows all four plans.
- **Change the embedding model, its dimensions or its document prefix,
  and every stored vector is meaningless against new questions.**
  Similarity search still returns rows. Here each vector carries the key
  it was made with: old ones are ignored, search says
  `keyword_only (no_current_vectors)`, `RetrievalDegraded` fires. Fix:
  `make reembed`.
- **"Hybrid" search can quietly be keyword-only.** The first version said
  hybrid while its semantic half returned nothing. Report what actually
  contributed.
- **`websearch_to_tsquery` requires every word.** A question rarely uses
  every word of its answer: OR the question's own lexemes, and let the
  ranking sort them.
- **Postgres full-text ranking is not BM25.** `ts_rank` and `ts_rank_cd`
  ignore how rare a word is: a word in 1 of 101 documents scored the same
  as one in 100 of them.
- **Task prefixes are part of the embedding model.** nomic-embed-text
  expects `search_query: ` and `search_document: `, trailing space
  included: quote them in `.env`. Here they moved distances a lot and
  recall not at all.
- **A distance cutoff is per model, and often impossible.** With
  nomic-embed-text, relevant sections reached 0.43 and unanswerable
  questions started at 0.41.
- **Reciprocal rank fusion weights both lists equally.** "comes back
  down" matched "Roll back" by keyword and outranked the semantic winner.
- **Too little context makes the model invent.** With 1 section, it
  invented a step, without the runbook's safety conditions. With 4, the
  answer was right.
- **Models cite however they like.** Asked for `[R1]`, gpt-oss also wrote
  `(R1)`, `【R1】`, `[**R1**]` and a bare R1. A strict pattern failed a
  quarter of the correct answers.

### Tracing (OpenTelemetry)
Measured building the traces; the evidence is in
[AI observability](docs/handbook/ai-observability.md).
- **Never make a span current inside an async generator.** It stays
  current in the caller's code between chunks, and detaching fails when
  the generator is closed from another context. Start it, end it in
  `finally`.
- **Instrumenting a generator by wrapping it in another breaks
  cancellation.** Closing the outer generator left the inner one, and the
  provider's stream, open until garbage collection.
- **The ASGI instrumentation package adds a span per response chunk**
  unless `exclude_spans=["send", "receive"]`: hundreds per streamed answer.
- **`SQLAlchemyInstrumentor` is a process-wide singleton:** a second
  `instrument()` is ignored. Instrument in startup, uninstrument in
  shutdown.
- **The SQL commenter writes the trace id into every statement,** so no
  two match, and prepared-statement caches (asyncpg, PgBouncer) churn.
  Leave it off.
- **`OTEL_EXPORTER_OTLP_TIMEOUT`: the spec says milliseconds, the Python
  exporter reads seconds.** Pass the timeout in code.
- **With the trace backend down, each worker's shutdown waits out the
  export timeout** (10 s by default): 10.25 s against 0.42 s. With 2 s,
  1.27 s. Requests are not affected.
- **An unsampled request still has a valid trace id.** Hand one out only
  when `trace_flags.sampled`; at 10%, nine in ten led nowhere.
- **Full sampling costs the tail:** at 200 req/s, p99 went from 7–13 ms to
  39–44 ms; at 10%, p95 stayed within 0.2 ms of off.
- **Redaction removes secrets, not facts.** A captured question kept "payroll
  export failed for 1,200 employees" for every trace reader. Content stays
  off spans in production.
- **prometheus_client's multiprocess mode has no exemplars**, so metrics
  cannot link to traces under gunicorn. Log lines carry the trace id.
- **Jaeger 2 dropped the old search API** (`/api/services`,
  `/api/traces?service=` answer 404). Use `/api/v3/traces`.

### Observability
- **A ratio with a numerator that doesn't exist yet is "no data", not 0**:
  `or vector(0)`. ([observability](docs/handbook/observability.md))
- **`rate()` needs two samples, and a new series' first increment is
  invisible** to `rate()`/`increase()`.
- **A removed service has no `up` series**: alert with `absent()`.
- **cAdvisor exports every container label by default**: whitelist
  them.
- **cAdvisor's newer releases are only on ghcr.io**, and
  `grafana/grafana-oss` has no 13.x tag (`grafana/grafana` is the OSS
  image).
- **Alertmanager's config can't read environment variables**: pass
  secrets as files.
- **promtool runs as `nobody`** and can't read a 700 directory.
- **A fault shorter than the scrape interval can leave no trace in
  metrics.**
- **A dashboard edited in the Grafana UI is lost on reload**: change the
  generator, `make dashboard`.
- **curl treats `{…}` in a PromQL URL as a glob**: use `-G
  --data-urlencode`.

### Testing and CI
- **Required approvals block a solo owner**: GitHub never lets you
  approve your own PR. Until a second engineer exists, the owner merges
  with the admin bypass (`gh pr merge --admin`).
- **Browser tests must reach the site the way users do**: Playwright on
  the host network, opening `https://localhost:<edge port>`, and the mock
  reached by its container IP. The browser and the api reach the
  identity provider at different URLs; its tokens must still name one
  issuer (`KC_HOSTNAME`).
- **Sign in once per test run, not per test**: a Playwright setup project
  signs the demo users in through the real login page and saves their
  cookies (`storageState`).
- **Images from a public repository's workflow are public on GHCR**:
  anyone can pull them without a token.
- **CI runs as UID 1001, which has no account in the node image**, so
  `HOME=/` broke Vitest. `AS_ME` sets `HOME=/tmp`.
  ([testing](docs/handbook/testing.md))
- **Coverage is a floor, not a proof**: the Stop re-submit bug passed its
  unit tests; only a real browser showed it.
- **Run CI's steps locally before pushing** (`make check`, `obs-check`,
  `image-check`): skipping one let a crash reach CI.
- **Branch protection needs a paid plan on private repositories.**
- **The gitleaks GitHub Action needs a licence for organisations**: use
  its CLI.
- **An Action pinned by tag can be moved to malicious code**: pin by
  commit SHA.
- **npm rewrites a hidden lockfile inside `node_modules`**: a root-owned
  volume makes it fail with EACCES.
- **`tsc -b` writes build info under `node_modules/.tmp`**, which also
  needs to be writable.
- **`make help`'s pattern skipped targets with digits** (`e2e`).

### Load testing
- **A closed model (N users waiting on replies) hides the queue**, and
  with it coordinated omission: use arrival-rate executors.
  ([load testing](docs/handbook/load-testing.md))
- **Arrival shape matters as much as rate**: the same 100 req/s gave
  1.5 ms evenly spread, 29 ms in clumps. After sign-in doubled the
  per-request cost, 200 users pacing 1 req/s in step saw p50 182 ms,
  against 3 ms for the same rate spread out.
- **Every tool needs the session**: a cookie header from `make session`
  (k6 `SESSION_COOKIE`, JMeter `-Jcookie`, vegeta's targets file), and
  `Origin` on POSTs.
- **A saturated load generator measures itself**: Artillery needed 534%
  CPU for 200 req/s.
- **k6 and Artillery phone home by default.**
- **Rate limits turn a load test into a 429 test**: raise them for the
  run.
- **JMeter and Artillery report whole milliseconds**, which can't
  resolve a 1.5 ms service.

### Operations
- **Recreating a single container refuses requests for the whole
  drain** (6.7 s, up to 120 s with long streams): `make deploy`.
  ([VM runbook](docs/runbooks/demo-vm.md))
- **Replacing the only nginx refuses connections for ~0.3 s**; only a
  load balancer removes that.
- **A backup on the same disk dies with it, and an untested restore is a
  hope**: copy dumps off the host, and rehearse
  (`DUMP=... make fresh-host-test`).
- **The first bottleneck came from missing data, not missing
  hardware**: a missing index, invisible until 2 M rows.

### Shell, Git, host
- **`git checkout <file>` to undo an experiment discards every other
  uncommitted change in that file.** A documented correction was lost this
  way, and its lab kept saying it had been made. Commit first, or revert
  the experiment with a reverse patch (`git diff > x.patch; git apply -R`).
- **In a YAML folded block (`>-`), a more-indented line keeps its
  newline**: cloud-init's secret loop, continued on an indented line,
  became two shell commands (`for ... in <newline>`). Parse the YAML and
  run the resulting command once (`yaml.safe_load`).
- **`! cmd` is exempt from `set -e`**: test explicitly with `if`.
- **`pgrep -f pattern` matches the shell running it** when the pattern
  is in that shell's own command line: a wait loop never ended, and a
  `kill` loop killed itself.
- **`mv src existing-dir` moves into it** instead of renaming.
- **`sudo` needs a terminal for its password, and a password pasted into
  a chat is burned**: rotate it.
- **Branch before the first edit**; `git switch -c` carries uncommitted
  changes over.
- **Windows checkouts turn scripts into CRLF**: `.gitattributes` forces
  LF.
- **On Windows, a repo under `/mnt/c` gets no file events**, so hot
  reload stops: clone into WSL.
- **A formatter that is never checked drifts**: CI runs `ruff format
  --check`.
- **shellcheck found an unguarded `cd`** in a script without `set -e`: it
  runs in `make lint` and CI.
- **`mapfile` needs bash 4**; macOS ships 3.2 (the deploy script is for
  Linux hosts).
- **The host's Node was too old for a scaffolding tool**: run toolchains
  in containers, not on the host.

## Failure modes, in one table

Measured with `make drills`; the full matrix is in
[failure modes](docs/handbook/failure-modes.md).

| When this fails… | users see… | back after |
|---|---|---|
| Valkey (down or frozen) | nothing, but rate limits are off | 0 s |
| PgBouncer or Postgres (down or frozen) | JSON 503 within 5–10 s; nothing hangs, nothing leaks | 1–2 s |
| the model provider (down, 429, 500, hang, drop) | a typed error in the chat stream; everything else works | 0 s |
| the identity provider | signed-in users: nothing. New sign-ins fail on the provider's page. `IdentityProviderDown` fires | 0 s |
| one api worker (killed, OOM) | its in-flight requests cut; a new worker starts | 0 s |
| the api container (crash) | in-flight streams cut, ~0.5 s of 502 | < 1 s |
| a deploy | nothing with `make deploy` through the edge (134,917 requests, 0 failed); ~2 s of refused connections when the edge itself changes; 6.7 s of 502 with a plain recreate | — |
| nginx or the VM | the site is down, and **nothing inside the stack alerts** | — |

## Bottlenecks, in the order they bite

A missing index → pool churn → api CPU (~530 signed-in reads/s per core,
~920 before sign-in; 500 streams per 2 CPUs, the SDK's per-chunk cost) → event-loop saturation
turning into timeouts elsewhere → connection budgets (~12 replicas) →
the provider's quota. Details and numbers:
[performance](docs/handbook/performance.md).

## Decisions

The ADRs in [docs/adr](docs/adr/) record what was decided and why:
- the project shape (0001)
- Valkey (0002)
- compose and make (0003)
- rate limiter fail-open (0004)
- database access (0005)
- the model seam (0006)
- the SSE format (0007)
- observability (0008)
- performance defaults (0009)
- database timeouts (0010)
- releases and deploys (0011)
- HTTPS at the edge (0012)
- sign-in and team access (0013)
- row-level security (0014)
- rollbacks roll back code, not the schema (0015)
- evals gate prompt and model changes (0016)
- runbook retrieval in Postgres, hybrid, under row-level security (0017)
- traces with OpenTelemetry and the GenAI conventions, no content by default (0018)
- an append-only audit trail of what the assistant reads, and who wrote it (0019)

A merged ADR is never edited: a new one supersedes it.

## Not done yet

What a real launch still needs. Each item is a known gap, not an
oversight:
- **Credentials for machine clients** (OAuth client credentials), and
  back-channel logout from the identity provider.
- **Outside-in monitoring**: an uptime check, and a dead man's switch for
  Prometheus itself. Nothing notices when the VM or the edge is down.
- **A second host.** One VM has single points of failure (listed in
  failure modes).
- **Let's Encrypt for real**: rehearsed against Pebble (`make
  acme-test`); a real domain is the step left.
- **Image and secret scanning** in CI (GitHub's secret scanning with push
  protection is on).
- **Quality evals on a schedule** against the production model, and
  real answers sampled into the eval set. Today quality evals run on
  demand, against a local model.
- **Central logs** (Loki, CloudWatch), once there is more than one host.
- **An OpenTelemetry Collector:** tail sampling (keep every failing
  trace), and durable trace storage with access control
  ([AI observability](docs/handbook/ai-observability.md)).
- **Audit history the database owner cannot rewrite:** a copy of each
  event outside the database, or a hash chain
  ([AI security](docs/handbook/ai-security.md)).
- **Tools for the assistant**, and the rules they need first: the user's
  own rights, confirmation for changes, allow-listed egress, every call
  audited ([AI security](docs/handbook/ai-security.md)).
- **Better runbook answers:** a reranker, the neighbouring sections in
  context, shadow mode before a team turns runbooks on, a sync from the
  wiki, and retrieval measured on real questions
  ([RAG](docs/handbook/rag.md)).

## Where things are written

| Question | Document |
|---|---|
| What is this, how do I run it? | `README.md` |
| What is it, in one page, for a decision-maker? | `docs/overview.md` |
| How do I contribute? | `CONTRIBUTING.md` |
| How should an AI coding agent work here? | `AGENTS.md` (read by Claude Code, Codex, others) |
| How is it built and run, and why, in depth? | this file and `docs/handbook/` |
| Why was X decided? | `docs/adr/` |
| What do I do when Y happens? | `docs/runbooks/` |
| What are we building next, and should we? | `docs/prd/`, `docs/rfc/`, `docs/design-docs/` (templates in each) |
| Is the architecture fit to go live? | `docs/ard/` (the architecture review) |
| What do we ask vendors to quote? | `docs/rfq/` |
