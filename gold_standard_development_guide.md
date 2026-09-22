# Gold Standard Development Guide

This document explains **every non-obvious decision** behind this
repo's shape — not what the code does, but *why it's organized the way
it is* — so this repo can serve as the template future AI projects on
this team copy from. If you're new here, read this before your first
PR. See `docs/adr/0001-standard-project-shape.md` for the condensed,
permanent decision record; this doc is the fuller "why," including the
real problems hit building it.

## 1. Naming conventions

- **Repo name (`triage-assistant`)**: lowercase, hyphenated, describes
  what it does, not how it's built. No `-service`/`-app` suffix noise.
- **Directory names (`apps/api`, `apps/web`)**: `api`, not `backend` —
  it says what the service's job actually *is* (serving an API), not
  just its position in an architecture diagram. `web`, not `frontend`
  — shorter, and the more common convention in monorepos this size.
  Both are one word, lowercase, no abbreviation guessing needed.
- **Docker Compose service names** (`api`, `web`, `db`, `pgbouncer`,
  `redis`) match the directory names where one exists, and are the
  plain, boring name of the thing otherwise (`db`, not `postgres-main`
  or `database-primary` — there's only one, name it for its role).
  These names are also DNS hostnames inside the Compose network
  (`api` resolves to the api container from `web`, etc.) — a
  needlessly clever name here becomes a needlessly clever hostname
  everywhere in code and config.
- **Branch names**: `type/<issue-number>-<short-desc>` (e.g.
  `feat/1-redis-rate-limit`). The issue number makes the branch
  traceable back to why it exists without opening GitHub.
- **Commit messages**: [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `chore:`...) — enables automated changelogs later
  without anyone having to hand-write one.
- **Docker image build targets** (`dev`, `production`) inside a
  multi-stage Dockerfile: plain English, not abbreviations — `docker
  build --target production` should be guessable without reading the
  Dockerfile first.

## 2. Directory structure, folder by folder

```
apps/api/         FastAPI backend
apps/web/          React + Vite frontend
infra/             empty (see below) — infra's future home
docs/              PRD / RFC / Design Doc / ADR templates + content
scripts/           empty (see below) — one-off ops scripts' future home
.github/workflows/ CI
docker-compose.yml  the whole local stack, one command
AGENTS.md / CLAUDE.md   instructions for AI coding tools
```

### Why `apps/` and why `api`/`web` are separate

Everything under `apps/` is an independently **buildable, deployable
artifact** — each has its own Dockerfile, its own dependency manifest
(`pyproject.toml` vs `package.json`), gets built into its own image,
and is tested by its own CI job (`lint`/`build` vs `web-build`).
Keeping them separate, even though today it's one team working on
both, means:

- They can scale independently later — the API might need 10
  replicas under load, the frontend might be served from a CDN with
  two nginx replicas. Totally different profiles.
- Each has its own toolchain (`uv`/Python vs `npm`/Node) that
  shouldn't leak into the other — no shared `node_modules` next to
  Python code, no Python venv confusion in frontend tooling.
- CI can build/test them in parallel, and a frontend-only change
  doesn't need to wait on a Python dependency install.
- It matches the "one monorepo, multiple deployables" call already
  made in ADR-0001 over splitting into separate repos.

### Why `infra/` exists and is currently empty

This project is pre-production — there's nothing to deploy yet beyond
`docker compose up` on a laptop. `infra/` is a **placeholder with
intent**: once this graduates past prototype and needs to actually run
on AWS, this is where Terraform (or whatever IaC tool infra standardizes
on) goes — VPC/subnet definitions, ECS/EKS task definitions or k8s
manifests, ECR repo definitions, IAM roles, the golden AMI definition.
The point of having the empty folder now, with this doc explaining it,
is that whoever inherits this repo from the AI team has an obvious,
already-agreed-upon place to put their code — they don't have to
propose a repo restructure first.

### Why `scripts/` exists and is currently empty

For one-off operational scripts that don't belong in application code:
a one-time data migration, a script to seed the dev DB with realistic
fixture data, a credential-rotation script, a bulk-export script for
debugging a production incident. None have been needed yet — the app
is this early — but the convention exists so that when someone
inevitably needs "a quick script to fix prod data," there's a
designated, git-tracked, discoverable place for it, instead of it
living in someone's home directory or a throwaway branch that gets
lost.

### `docs/`

See `docs/README.md` for the full breakdown of PRD vs RFC vs Design
Doc vs ADR and when to use each. Short version: these are the
documents a real team needs to communicate with stakeholders (PRD),
propose and review technical approaches (RFC), record the detailed
plan (Design Doc), and permanently record *why* a decision was made
(ADR) — separate from this guide, which is onboarding material, not a
decision record.

### Root-level files

`AGENTS.md`/`CLAUDE.md` live at the repo root (not in `docs/`) because
that's the actual convention AI coding tools look for — same reason
`README.md` lives at the root and not in a folder. `README.md` is the
30-second "what is this, how do I run it" for a human. This file is
the deeper "why is it built this way" for a human who's about to
contribute.

## 3. How a new developer starts contributing

1. **Install Docker. That's it.** No Python, no Node, no Postgres
   client needed on your machine — everything runs inside containers.
2. Clone the repo, `cp .env.example .env`.
3. `docker compose up --build`. This brings up five containers:
   - **`db`** — Postgres, the actual data store.
   - **`pgbouncer`** — sits in front of `db`, pools connections. Why
     it exists: many short-lived API requests each opening a raw
     Postgres connection is expensive; pgbouncer holds a small pool of
     real connections and multiplexes app requests through them. The
     API talks to `pgbouncer`, never directly to `db`.
   - **`redis`** — currently backs rate limiting on `POST /alerts`;
     the natural place to add caching or a task queue later (see the
     complexity-dial reasoning in the practice workspace's `NOTES.md`
     — add either only when there's a measured need, not preemptively).
   - **`api`** — FastAPI, hot-reloading in dev mode (bind-mounted
     source, `uvicorn --reload`).
   - **`web`** — React + Vite, hot-reloading dev server.
4. Verify: `curl localhost:8010/health`, open `localhost:5173` in a
   browser.
5. **Make a change.** Edit `apps/api/main.py` or `apps/web/src/App.tsx`
   — both hot-reload automatically, no rebuild command needed. (This
   wasn't true for the API until this hardening pass — see Gotchas #7.)
6. **Test.** `docker compose exec api uv run pytest` (once tests
   exist), `docker compose exec api uv run ruff check .`,
   `docker compose exec web npm run build` (type-checks + bundles).
7. **Open a PR.** Branch per the naming convention above, reference an
   issue (`Closes #N`), push, open the PR. CI (`lint`, `build`,
   `web-build`) must pass before merge — branch protection enforces
   this, it isn't optional discipline.

## 4. Two paths: development vs. production

Every service that needs it (`api`, `web`) has a **multi-stage
Dockerfile** with a `dev` target and a `production` target — this is
the actual mechanism behind "identical scaffold regardless of the AI
logic inside" from ADR-0001.

| | `dev` (what Compose runs) | `production` |
|---|---|---|
| **api** | Source bind-mounted, `uvicorn --reload`, single process, fast iteration | `gunicorn` managing N `uvicorn` worker processes, non-root user, `HEALTHCHECK` baked in, no bind mount — the image *is* the artifact |
| **web** | Source bind-mounted, Vite dev server, hot module reload | Static assets built (`npm run build`) and served by `nginx` (unprivileged, non-root, port 8080), which also reverse-proxies `/api/*` to the `api` service |

`docker compose up` always builds `dev` (`target: dev` set explicitly
in `docker-compose.yml`). A real deploy builds `production`:
```
docker build --target production -t triage-assistant-api ./apps/api
docker build --target production -t triage-assistant-web ./apps/web
```
The frontend's production nginx config (`apps/web/nginx.conf`) does
the *exact same job* Vite's dev-only proxy does locally — forwards
`/api/*` to the backend — so there's no behavior surprise moving from
dev to prod, just a different tool doing the forwarding.

## 5. Hardening decisions, and why

- **Non-root containers** (`api` production target, `web` production
  target via `nginxinc/nginx-unprivileged`) — a compromised app
  process can't touch anything root-owned inside the container. The
  plain `nginx` image needs root to bind port 80; the unprivileged
  variant runs as a real user on port 8080 instead, which is why the
  production frontend listens on 8080, not 80 (a real load
  balancer/ingress in front of it maps the public 443/80 to this).
- **Multi-process (`gunicorn` + `UvicornWorker`)** in production —
  the FastAPI-recommended production shape, and the same pattern
  already audited live in this team's `unifiedlearning` service.
  Worker count is `2 x cores + 1` by default (gunicorn's own
  long-standing recommendation), **overridable via `WEB_CONCURRENCY`**
  rather than hardcoded — see Gotcha #7 for why the default alone is
  dangerous.
- **Healthchecks on every service that can have a meaningful one**
  (`db`, `pgbouncer`, `redis`, `api`, and `web`'s production nginx
  image) — `depends_on: condition: service_healthy` only works if the
  thing being depended on actually reports health, and an
  orchestrator (ECS, k8s) needs the same signal to know when to route
  traffic to a new instance. `web`'s *dev* target deliberately has no
  healthcheck — it's a local convenience server, nothing depends on
  its health status for an orchestration decision.
- **Secrets never in the image or in git** — `.env` is gitignored,
  `.env.example` documents required vars with placeholder values,
  real secrets are injected at runtime (locally via `.env`, in
  production via whatever secrets manager infra wires up — the app
  code doesn't change either way, it just reads env vars).

## 6. Gotchas — real problems hit building this

Everything below actually happened building this repo, in this order,
live — not a hypothetical list. If you hit one of these, this is
where the answer already is.

1. **Host Node too old for the Vite scaffolding CLI.** This box's
   Node was 18.19; the current `create-vite` needs Node 20+
   (`node:util`'s `styleText` export doesn't exist before then). Fix:
   don't upgrade host Node — scaffold via `docker run node:20-slim`
   instead. You never need a "correct" Node on your host at all; the
   container is the correct environment, always.
2. **Container-created files come out root-owned.** Scaffolding via a
   plain `docker run` (no `--user` flag) writes files as root into
   whatever host directory is bind-mounted. Fix: a throwaway
   `docker run --rm -v <path>:/app alpine chown -R $(id -u):$(id -g) /app`
   — no `sudo` needed, since the container itself has root inside its
   own namespace regardless of the host user running it.
3. **Port collisions from a stale, forgotten stack.** An old
   standalone container (or, later, the superseded practice-workspace
   stack) silently held port 8010, and `docker compose up` failed with
   "port is already allocated." Fix: `docker ps` first, always, before
   assuming a fresh `up` will just work — `docker compose down` the
   stale stack.
4. **GitHub branch protection needs a paid plan on a private repo.**
   Both the classic branch-protection API and the newer rulesets API
   return the identical 403 on a free-tier private repo. Real options:
   GitHub Pro/Team, a different host with a free private-repo tier
   (GitLab), or go public if nothing sensitive is in the repo (what
   this project did — verified no secrets were ever committed first).
5. **`mv source existing-dir` nests instead of renaming.** Moving this
   repo into `/opt/triage-assistant` (which already existed, freshly
   created) put the whole repo one level too deep
   (`/opt/triage-assistant/triage-assistant/`) instead of flattening
   into it. `mv` only renames when the destination *doesn't already
   exist* — moving into an existing directory always nests. Fix: move
   the nested contents up one level, remove the now-empty directory.
6. **`sudo` needs a real terminal for its password prompt.** Any
   sudo command run by an agent/non-interactive process fails with
   "a terminal is required to read the password." Either the human
   runs the one-time privileged command themselves (creating
   `/opt/triage-assistant` and `chown`ing it), or — if a password is
   explicitly provided — `sudo -S` reads it from stdin instead.
   **If you ever paste a real password into a chat/log to unblock
   this, treat it as burned and rotate it afterward** — it's now in
   plaintext history.
7. **`gunicorn`'s worker-count formula reads the HOST's core count,
   not a real per-container allocation.** `multiprocessing.cpu_count()`
   inside a container returns the *host's* total cores unless the
   container has a CPU limit AND a Python version that respects
   cgroup quotas — neither was true here. On this 24-core dev box, the
   formula (`2 x cores + 1`) spawned **49 worker processes** for a
   tiny health-check service, live-confirmed via `docker top`. Fix:
   an explicit `WEB_CONCURRENCY` env var override for local dev
   (`=2` here), with the formula still available as production's
   sane default once a real, deliberately-sized CPU allocation exists.
8. **Don't trust a 429 alone as proof a distributed rate limiter
   works.** It's easy for a rate limiter to *look* correct while
   silently running in-memory per-process (the exact bug already found
   in `unifiedlearning`). Real verification: `redis-cli KEYS '*'`
   inside the redis container, confirming the counter genuinely lives
   in Redis, not process memory.
9. **The plain `nginx` image needs root to bind port 80.** Ports
   below 1024 need `CAP_NET_BIND_SERVICE` or root. Fix:
   `nginxinc/nginx-unprivileged`, which binds 8080 as a real non-root
   user instead — consistent with the API's own non-root hardening.
10. **A second, unrelated port collision, this time on 8080** — an
    already-running process on this shared dev box (unrelated to this
    project) was already bound to 8080, so the first verification run
    of the production nginx image failed with the identical "address
    already in use" error as gotcha #3, on a completely different
    port. Same fix: check what's actually listening (`ss -ltnp`)
    before assuming the port is free, use a different port for a
    one-off manual test rather than fighting for the "real" one.
11. **Committed directly to `main`'s working tree before branching,
    twice.** Made real file edits, then remembered branch discipline
    only after the fact. Fix used both times: `git switch -c
    <branch-name>` *after* the edits — uncommitted changes carry over
    onto the new branch cleanly, nothing was lost, just don't repeat
    the mistake. Branch first, always, even for "just one small
    thing."
12. **`EventSource` can't send a POST body.** The native browser SSE
    client only supports `GET` with no body, but a real prompt needs
    to go in the request. Fix, and the actual pattern real LLM
    streaming SDKs use: `fetch` + manually reading `response.body`'s
    `ReadableStream`, parsing `data: ...` lines by hand instead of
    using `EventSource` at all.
13. **`ps` doesn't exist inside `python:3.12-slim`.** Slim images
    strip most utilities to stay small. Fix: `docker top <container>`
    runs from the *host* and lists a container's processes without
    needing any tool installed inside it — the tool to reach for when
    you need process visibility into a minimal image.
