# Production: shipping it, handing it over, reaching real users

How a change travels from a laptop to users; what an infrastructure team
needs to take the service on; the stages between this repository and
real users, each with a way to prove it is done; how to keep proving it
once it is live; and what has never been tested.

✅ = done and measured here. 📘 = the plan, not exercised here. The step
by step for one VM is [the VM runbook](../runbooks/demo-vm.md). The
decisions: ADR-0011 (releases and deploys), ADR-0015 (rollbacks),
ADR-0022 (scanning), ADR-0023 (production refuses unsafe settings).

- [Where it stands](#where-it-stands)
- [The environments](#the-environments)
- [One artifact, promoted](#one-artifact-promoted): reproducibility, secrets
- [Releasing, deploying, rolling back](#releasing-deploying-rolling-back): canaries
- [Handing it to an infrastructure team](#handing-it-to-an-infrastructure-team): scale, backups, paging
- [Moving to a managed platform](#moving-to-a-managed-platform)
- [The road to real users](#the-road-to-real-users): stages 1 to 5
- [Keeping it working](#keeping-it-working): SLOs, game days, incidents
- [Never tested: the honest list](#never-tested-the-honest-list)

## Where it stands

✅ **Proven on one host, in the production shape:**
- HTTPS through the TLS edge, with automatic certificates rehearsed
  against Let's Encrypt's test CA;
- rolling deploys that dropped no request, and rollbacks across a
  migration;
- 22 failure drills;
- load up to the measured knee;
- backups, restored and rehearsed on a clean host;
- releases for amd64 and arm64, scanned for known vulnerabilities, and
  smoke-tested after publishing;
- a production mode that refuses unsafe settings (ADR-0023);
- sign-in against a real OIDC provider (Keycloak);
- evals against a real model (local);
- runbook search against a local embedding model, on a hand-written
  corpus;
- a trace per answer (retrieval, the model, their attributes), with its
  cost measured under load;
- an audit trail of who wrote what the assistant reads, and what each
  question was given, which the api cannot rewrite;
- an off switch for the assistant (ADR-0024); retention, export and
  erasure of personal data ([privacy](../privacy.md)).

📘 **Not yet:**
- a cloud VM with a real domain;
- the organisation's identity provider;
- a hosted model provider;
- real users;
- a second host.

[The road to real users](#the-road-to-real-users) closes these in order.
Each stage ends in something measurable, not a feeling.

## The environments

| Environment | What for | What runs | Data | Created / reset by |
|---|---|---|---|---|
| **Laptop (dev)** | writing and debugging code | the dev stack: hot reload, debugger, mock LLM, the bundled Keycloak (demo users) | seeded, disposable | `make up` / `make nuke` |
| **Laptop (prod shape)** | "does it work like production?" before pushing: nginx, gunicorn, read-only containers; e2e, load tests, drills | the production images, built locally | seeded | `make prod-up` / `make prod-down` |
| **Test stack** | integration tests | a throwaway compose project | created per run, deleted after | `make test-api` |
| **CI** | the same checks on a clean machine, for every PR | GitHub-hosted runners: lint, tests, image check, scans, e2e through the production stack | none kept | every push |
| **Staging / demo VM** | shared, always-on, production-shaped: demos, pilots, real-provider testing, drills | released images from GHCR; the bundled Keycloak, or a test tenant of the organisation's provider | demo data; never production data | `infra/vm/cloud-init.yaml`, then `make deploy` |
| **Production** | users | the same images, on one VM or a managed platform; **the organisation's identity provider**, never the bundled Keycloak | real | the platform's deploy |

Answers to the usual questions:
- **Do developers need a dev VM?** No: laptops run everything
  ([dev environment](dev-environment.md) covers each operating system).
  A cloud dev VM, with VS Code Remote-SSH, is the fallback where laptops
  can't run Docker (locked-down corporate machines) or are too small.
- **What is a staging VM for?** A shared place that runs the production
  shape all the time: real provider keys, demos, the alert and dashboard
  loop, failure drills. Nobody's laptop needs to be on.
- **For a manager or customer demo:** a staging-like VM, sized from the
  runbook's IT request (4 vCPU, 8 GB, 40 GB). Deploy a released tag, and
  run through the pre-demo checklist.
- **Different laptops** (Linux, Mac, Windows, Intel or ARM) all run the
  same images. Tool versions live in the images, not on the laptops.

## One artifact, promoted

The same image moves through every environment. Only configuration
changes:

```
laptop ──PR──► CI (lint, tests, scans, image check, e2e) ──squash──► main ──tag vX.Y.Z──► release workflow
                                                                                      │ scans, builds once, pushes
                                                                                      ▼
                                       ghcr.io/<owner>/triage-assistant-{api,web,edge}:X.Y.Z
                                                  │                           │
                                       make deploy tag=X.Y.Z        the same tag, later
                                                  ▼                           ▼
                                           staging / demo VM             production
```

- **Build once.** The bytes tested on staging are the bytes production
  runs. A host that builds from source can differ from the next one (a
  package republished, a base image patched in between).
- **Configuration only from the environment** (twelve-factor):
  `DATABASE_URL`, keys and limits come from `.env` on a VM, or from the
  platform's secret store. Nothing environment-specific is baked into an
  image.
- **Version = tag.** `latest` is never deployed: it can't be rolled back
  to, and two pulls of it can differ.

### Reproducibility

**Can we run exactly what was tested?** Yes, when you deploy the
release's images. A `vX.Y.Z` tag on `main` builds the api, web and edge
images once, for amd64 and arm64. It scans them, pushes them by digest,
then tags them, and smoke-tests the *published* images on both
architectures: HTTPS with a verified chain, a write, a read, a streamed
answer. Hosts pull by tag (`make deploy tag=X.Y.Z`) and never build. The
CI that tested the commit and the release that shipped it build from the
same tree, with the same lockfiles.

**Can a tag be moved under us?** A registry tag is a movable pointer:
anyone who can push the package can re-push it. Two controls:
- the release workflow is the only automation that pushes
  (`packages: write` appears nowhere else). People with write access to a
  package could still push by hand, so keep that list short;
- every image carries **provenance** and an **SBOM**:
  `docker buildx imagetools inspect <image>:<tag> --format '{{json .Provenance}}'`
  shows the commit and workflow that built it.

📘 For stricter control, deploy by digest (`image@sha256:...`), and record
the running digests at each deploy (`docker inspect -f '{{.RepoDigests}}'`).

**Can we rebuild an old release from source?** Yes, but not bit for bit.
- **Pinned exactly:**
  - Python dependencies: `uv.lock` with hashes, installed with `--frozen`;
  - npm dependencies: `package-lock.json`, installed with `npm ci`;
  - GitHub Actions: by commit SHA;
  - base images and the scanners: by tag and digest (ADR-0022). A
    rebuild months later gets the same base, unpatched: patches arrive as
    Dependabot PRs that move the digest, and the scans fail a build on a
    fixable HIGH or CRITICAL.
- **Timestamps differ:** file modification times in layers differ between
  two builds of the same commit. This repo met that directly: the edge
  image was "different" on every release until CI stamped it with a hash
  of its build inputs. Compare builds by their input hashes, not their
  layer digests. 📘 For bit-for-bit rebuilds, also set `SOURCE_DATE_EPOCH`.

**Do the environments match?**
- Development, tests and production start from one `compose.yaml`, with
  an overlay each (ADR-0003).
- The production image contains no development tools, and no pip. `make
  image-check` in CI proves it: non-root, a read-only root filesystem,
  every module importable.
- Configuration differs only through the environment, as the settings
  table in [daily work](daily-work.md) documents, plus the defaults that
  `APP_ENV=prod` changes on purpose: API docs off, 10% of traces kept, the
  chat's rate limiter failing closed (ADR-0023).
- The whole production stack comes up on a clean runner from committed
  files and a `.env` made by `make .env`: CI's e2e job does it on every
  push, serving HTTPS.

**Is the database schema reproducible?** Yes:
- Alembic migrations from an empty database to head run in CI on every
  push;
- `alembic check` then fails if the models and the migrations disagree;
- roles and grants are created by `infra/postgres/initdb`, and row-level
  security by the migrations.

**And the model?** Not reproducible, by nature. What makes it manageable:
- **Sampling varies.** Quality is a pass rate over repeated runs, with a
  committed baseline (ADR-0016).
- **Providers change models behind a name.** Pin a dated model version
  where the provider offers one, and re-run the evals when they announce
  a change. The reports record which model answered.
- **Local models are pinned by tag.** `ollama list` shows each one's
  digest: gpt-oss:20b is `17052f91a42e` here.

### Secrets

| Where | Secrets come from |
|---|---|
| laptop | `.env`, made once by `make setup` from `.env.example`, every `change-me` value generated, mode 600 |
| CI | the same `make .env`, for throwaway stacks |
| VM | `.env` generated at first boot by cloud-init (`openssl rand`), mode 600, owned by the `deploy` user; the model key and the organisation's client secret typed in once |
| managed platform | a secret store (AWS Secrets Manager or SSM Parameter Store, Azure Key Vault, GCP Secret Manager) injected as environment variables |

Never in the image, git, logs, or a ticket. A secret that reached any of
them is rotated, not just deleted. gitleaks scans every commit in CI,
GitHub's secret scanning with push protection is on, and production
refuses to start with a value from `.env.example` ([security](security.md)).

## Releasing, deploying, rolling back

- **Cut a release:** `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin
  vX.Y.Z` on main. The release scans the images first: a fixable HIGH or
  CRITICAL stops it (ADR-0022). Then it publishes them with provenance
  and an SBOM (the VM runbook, section 8).
- **Deploy to a VM:** `make deploy tag=X.Y.Z`: migrations, then the new
  api next to the old one, then the old one drained. No failed request
  in three measured runs through the TLS edge, which holds requests while
  nginx is replaced (without the edge: ~0.3 s of refused connections). A
  deploy that replaces the edge itself, when its build inputs changed,
  refuses connections for ~2 s. A new api that never gets healthy is
  removed after 90 s, and the old one keeps serving.
- **Roll back:** deploy the previous tag. The code rolls back, the schema
  never does (ADR-0015): the previous tag runs on the newer schema, which
  expand/contract makes safe back to the release before a contract
  migration.
- **Migrations are backward compatible** (expand, then contract), because
  the old version keeps serving while they run ([daily
  work](daily-work.md)). The teams migration (v0.2.0) is the worked
  example: measured under load, the previous release kept serving with 0
  errors ([operations](operations.md#reading-several-teams-at-once)).
- **Releases are never skipped when one contracts what another
  expanded.** v0.3.0's migration turns on row-level security, which hides
  every alert from v0.1.0 (it never says who is asking). Go through
  v0.2.0, which does. A release note names the minimum version it can
  follow.
- **A breaking API change is a release note, not a surprise.** v0.2.0
  made every endpoint require a session: any script or integration
  calling the api needs one (`make session`, or a real sign-in), and
  state-changing calls need `Origin`. Alertmanager's webhook kept its
  bearer token.
- **Backups:** `make backup`, copied off the host. A backup counts once
  you have restored it (`make restore`).

### Canary releases

📘 `make deploy` is a rolling deploy: all traffic moves to the new
version within seconds. That is safe for crashes and startup failures
(readiness gates it), but not for subtle regressions. Options, in order
of effort:

1. **Deploy, watch, roll back.** Keep the dashboard open for 30 minutes,
   then `make deploy tag=<previous>` if needed. Rollbacks work across
   migrations (ADR-0015). This is the current practice.
2. **A canary on one host.** Run the new release as a second api service
   (`api-canary`), and weight nginx's upstream (for example 90/10 with
   `split_clients`). Compare its `llm_*` and `http_*` metrics with the old
   version's before shifting everything.
3. **On a platform:** weighted target groups (AWS ALB), revisions with
   traffic splitting (Cloud Run, Azure Container Apps), or Argo Rollouts
   on Kubernetes, with automatic analysis against the SLO metrics.

**Prompt and model changes get a canary of their own:**
- **Offline:** the eval suite (ADR-0016).
- **Shadow mode:** the new prompt answers real questions in the
  background, and only the old answer is shown. Compare the two offline,
  where privacy terms allow.
- **Per team:** only then turn it on for one team.

## Handing it to an infrastructure team

What they need, all of it in this repo:

| They ask | Answer |
|---|---|
| What runs? | three images (api, web, the TLS edge), one migration command, Postgres 17 with pgvector, Valkey 8, a model provider, an OIDC identity provider |
| Ports | the edge 80/443; web 8080 (HTTP, non-root nginx); api 8010 (internal only) |
| Health | `/api/health` (liveness: the process is up), `/api/ready` (readiness: the database answers a real query; Valkey and the identity provider reported, not required), nginx `/healthz` |
| Resources | api: 2 CPU / 1 GiB for ~1,000 signed-in reads/s or 500 streams; idle ~170 MiB. Every limit: [scale](#scale) |
| Configuration | the settings table in [daily work](daily-work.md), secrets marked; `APP_ENV=prod` refuses unsafe values at startup (ADR-0023) |
| Logs | JSON lines on stdout, one request id across nginx and the api, the trace id when traced; never tokens, cookies or any question, alert or runbook text; users by id, not email |
| Metrics and alerts | Prometheus at `/metrics` on 8010 (multiprocess, summed across workers); alert rules with their tests in `infra/observability` |
| Traces | optional: OTLP/HTTP to `OTEL_EXPORTER_OTLP_ENDPOINT` (the platform's collector); ids and counts, never content |
| Deploy order | migrations (backward compatible) → api (rolling, readiness-gated) → web |
| Stop | SIGTERM; up to 120 s of graceful drain for streams; SIGKILL after 130 s |
| Egress | over HTTPS: the model provider (answers, and embeddings for runbook search); the identity provider (metadata, keys, the code exchange); Let's Encrypt. Optionally OTLP to a collector. Nothing else at runtime |
| Identity | an OIDC confidential client: redirect URI `<PUBLIC_URL>/api/auth/callback`, post-logout `<PUBLIC_URL>/`; a claim carrying `team:<slug>:<role>` values ([security](security.md)) |
| Backups | the database only: [backups](#backups-and-paging) |
| Known limits | the single points of failure, and the one fault nothing bounds ([operations](operations.md#single-points-of-failure-on-one-vm)) |

### Scale

**What does one host handle?** Measured on the production shape
([operations](operations.md#performance-and-load)):

| Load | Limit | What saturates |
|---|---|---|
| signed-in reads (`GET /alerts`) | ~1,000 req/s with 2 api CPUs (p95 4.0 ms at 500/s, 19.7 ms at 1,000/s) | api CPU |
| streamed answers (mock model) | 500 concurrent streams, 239 answers/s, 0% errors | api CPU: the openai SDK costs 126 µs per chunk |
| Postgres | a fraction of a core at those rates | not the bottleneck |
| runbook search (hybrid, local embedding model) | p50 10.5 ms, p95 12.2 ms a search, one at a time | 📘 not load-tested |
| a hosted model | its tokens-per-minute quota | 📘 binds first in real use |

**What limits are set?** Everything has a CPU and memory limit, and no
swap (`compose.prod.yaml`):

| Service | CPU / memory |
|---|---|
| api | 2 / 1 GiB |
| db | 2 / 1 GiB |
| web (nginx) | 1 / 256 MiB |
| keycloak | 1 / 1 GiB |
| valkey | 0.5 / 256 MiB |
| edge | 0.5 / 128 MiB |
| pgbouncer | 0.5 / 128 MiB |
| monitoring | Prometheus 1 GiB, Grafana 512 MiB, the rest 64–256 MiB |

**How does it scale out?**
- **The api is stateless:** sessions live in Postgres and rate-limit
  counters in Valkey. Run N replicas behind a load balancer, with
  `WEB_CONCURRENCY` equal to each replica's CPUs.
- **Connection budget:** replicas × workers × pool (2 × 20 = 40 per
  replica) must stay under PgBouncer's `MAX_CLIENT_CONN` (500), which
  allows about 12 replicas. Beyond that, raise it, or run one PgBouncer
  per host.
- **Postgres** has one primary for writes. Read replicas are 📘: nothing
  routes reads to them yet.
- **Valkey** is a single node. That is fine: its data is disposable. Most
  limits fail open without it; the chat's fails closed in production.

**What should autoscaling watch?** CPU, `event_loop_lag_seconds` (it
rises before errors do) and `llm_active_streams`. Not request rate
alone: a stream holds a worker's attention for up to 120 s.

**What about long-lived connections?**
- Chat streams last up to 120 s, with a heartbeat every 15 s. Load
  balancer idle timeouts must exceed 15 s.
- A deploy drains for up to 120 s, and SIGKILL comes at 130 s.
- 📘 On Kubernetes: `terminationGracePeriodSeconds: 130`; a short
  `preStop` sleep, so the load balancer deregisters the pod before it
  stops accepting connections; a PodDisruptionBudget.

**What happens at 10× today's measured capacity?**
1. **Reads:** add api replicas. The index work is done, so it is CPU,
   linear.
2. **Streams:** add replicas. Per-chunk CPU in the SDK is the cost; a
   thinner client would halve it (55 µs against 126 µs, measured).
3. **Connections:** check the budget above.
4. **The model provider:** the real ceiling. Negotiate quota or dedicated
   capacity, and budget for it: about 1,300 prompt and 170 answer tokens
   per question at p50, measured ([AI cost](ai-cost.md)).

**Is it multi-region?** No, and not designed to be. It would need a
replicated database, an identity provider reachable from each region, a
model endpoint per region for data-residency rules, and a decision on
where sessions live. Write an RFC before promising it.

**Does it need Kubernetes?** Not at this scale, and not for one service.
Compose on one VM carries it to about 1,000 req/s, and a managed
container platform (ECS, Cloud Run, Container Apps) is the next step
without a cluster to run ([below](#moving-to-a-managed-platform)). The
contract any platform needs is already here: health probes (`/health`
for liveness, `/ready` for readiness), graceful drain, resource limits,
stateless replicas, metrics at `/metrics`.

### Backups and paging

**What must be backed up?**
- **Postgres**, and only Postgres: `make backup`, which runs `pg_dump` in
  custom format and keeps the newest 14. Runbooks are in it, with their
  vectors. The vectors can be rebuilt (`make reembed`); the runbook text
  cannot.
- It holds sessions: a restore signs out whoever signed in after the
  dump.
- **Restore:** 6 s in one transaction on the demo data. Rehearse it at
  production size before trusting it.
- **Copy each backup off the host.** A dump on the VM's disk dies with
  the VM.
- Valkey holds disposable counters, and needs no backup.

**What will page us?** The alert rules in
`infra/observability/prometheus/alerts.yml`, each with a section in [the
alert runbook](../runbooks/alerts.md). They route through Alertmanager:
point its receiver at your pager. Today it sends alerts into the product
itself, as a demo.

### What is infrastructure as code here?

- ✅ In the repo: the host bootstrap (`infra/vm/cloud-init.yaml`), the
  containers (`compose*.yaml`), the monitoring (rules with unit tests,
  generated dashboards), the demo identity realm, the TLS edge's
  configuration.
- 📘 Not yet code: the VM itself, DNS, the firewall and the backup
  bucket. The VM runbook's "What to ask IT for" lists them. They are the
  first Terraform or OpenTofu module to write.

## Moving to a managed platform

📘 When one VM is no longer enough (availability, scale, compliance),
each container maps onto a managed service. The table leads with AWS;
Azure and GCP have the same shapes.

| Here | AWS | Azure | GCP | What changes in this repo |
|---|---|---|---|---|
| api container (gunicorn) | ECS on Fargate (or EKS) | Container Apps / AKS | Cloud Run / GKE | `IMAGE_PREFIX` → the cloud registry. One task per 1–2 vCPU with `WEB_CONCURRENCY` = the task's vCPUs. The read-only root filesystem needs a writable `/tmp` volume (metrics files, the control socket) |
| the TLS edge (Caddy) + nginx (web) | ALB for TLS and routing (edge profile off); static files on S3 + CloudFront, or keep the nginx container | Application Gateway / Front Door | HTTPS LB + Cloud CDN | drop `edge` from `COMPOSE_PROFILES` and publish nginx; the SSE rules (no buffering, idle timeout > 15 s) move to the load balancer and CDN config |
| Postgres | RDS or Aurora PostgreSQL, Multi-AZ | Azure Database for PostgreSQL (flexible) | Cloud SQL | `DATABASE_URL`, `MIGRATIONS_DATABASE_URL`; the roles script (`infra/postgres/initdb`) run once as a migration or by hand; backups become the service's snapshots plus point-in-time recovery |
| PgBouncer | RDS Proxy, or keep PgBouncer as a sidecar | PgBouncer built into the flexible server | a sidecar | RDS Proxy "pins" sessions that use session state (the app uses none: transaction-scoped only). Re-run the database drills against whichever you choose |
| Valkey | ElastiCache for Valkey | Azure Cache for Redis | Memorystore for Valkey | `REDIS_URL` (TLS: `rediss://`) |
| mock LLM | the real provider (or Bedrock / Azure OpenAI / Vertex, all OpenAI-compatible or behind a gateway) | | | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |
| the bundled Keycloak | the organisation's identity provider (IAM Identity Center, Cognito, or the corporate Entra ID / Okta) | Entra ID | Cloud Identity / an IdP federated in | `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_GROUPS_CLAIM`; `idp` out of `COMPOSE_PROFILES` ([security](security.md), "Connecting your organisation's provider") |
| `migrate` service | a one-off task run before the service update (in the deploy pipeline) | a Container Apps job | a Cloud Run job | nothing: it's already a separate command |
| Prometheus / Grafana / Alertmanager | Amazon Managed Prometheus + Managed Grafana, or CloudWatch | Azure Monitor managed Prometheus + Grafana | Managed Service for Prometheus | scrape via service discovery; cAdvisor and node-exporter give way to the platform's container metrics |
| `.env` | Secrets Manager / SSM Parameter Store | Key Vault | Secret Manager | nothing in the code: they arrive as environment variables |
| `make deploy` | ECS rolling deployment with the deployment circuit breaker; health check on `/ready` | revisions | revisions | `/ready` is the target-group health check; `/health` is the liveness check |
| the retention job and backups (cron) | a scheduled task (EventBridge Scheduler → ECS task) | a Container Apps job on a schedule | Cloud Scheduler → a Cloud Run job | nothing: they are commands already (`python -m app.cli retention --apply`) |

What stays true on any platform: the health endpoints' split (liveness
doesn't check dependencies; readiness does, but not the soft ones), the
graceful drain (`stop_grace_period` > `graceful_timeout`), the timeout
chain (ADR-0010), and the failure drills. Run them again on the new
platform: its load balancer and proxies behave differently.

## The road to real users

### Stage 1: one cloud VM behind HTTPS

📘 Follow [the VM runbook](../runbooks/demo-vm.md): what to ask IT for,
the bootstrap with cloud-init, `.env`, the first start, HTTPS, deploys,
backups.
- **Network:**
  - DNS points a name at the VM;
  - inbound traffic only on 443, plus 80 for certificate renewal and the
    redirect;
  - SSH only from the company network, or through the cloud's session
    manager (no port 22 at all).
- **Certificates:** Caddy gets and renews them from Let's Encrypt. It was
  rehearsed against Pebble, Let's Encrypt's test CA. The first real
  renewal is about 60 days after the first certificate: put a calendar
  reminder on it.
- **Backups leave the host.** Copy each dump to object storage with a
  retention rule.

**Done when:**
- `make prod-up` brings the stack up on the VM from the checkout;
- the site serves a publicly trusted certificate;
- `make drills` matches the failure-mode matrix;
- a backup copied off the VM restores onto a fresh VM, with the time
  written down: that time is your recovery time (RTO).

### Stage 2: the organisation's identity provider

📘 [Security](security.md) has the steps: "Connecting your organisation's
provider".
- **Register** a confidential client.
- **Redirect URIs:** `<PUBLIC_URL>/api/auth/callback`, and `<PUBLIC_URL>/`
  after logout.
- **The groups claim** must carry `team:<slug>:<role>` values:
  - Entra ID: app roles;
  - Okta: a groups claim with a filter;
  - Google: groups via Cloud Identity.
- **Remove the bundled Keycloak:** take `idp` out of `COMPOSE_PROFILES`,
  and `demo_identity_provider` out of `PROD_CHECKS_WAIVED`.

**Done when:**
- people sign in with their company accounts;
- each role is checked by a real member: viewer, responder, admin, org
  admin;
- removing someone at the provider, then `make revoke email=...`, ends
  their sessions at once;
- the security team has agreed the session lifetimes (12 h absolute, 2 h
  idle by default).

### Stage 3: a hosted model provider

- 📘 **The API key:**
  - a key for this service only, with a spending limit;
  - it lives in the secret store, never a chat or a ticket.
- **The data terms:**
  - zero data retention, or no training on your data;
  - a region your data may be processed in.

  Alert text, runbook text and questions leave your network, to the chat
  model and to the embedding model ([privacy](../privacy.md)). Ask
  vendors for zero data retention, quota, region and price in writing,
  before choosing.
- **Evals first:**
  - calibrate the judge;
  - run the whole suite with `--repeat 10`, and the leak case with
    `--repeat 200` at least: one leak in 200 already puts the bound over
    the 1.5% target, and then it takes more runs;
  - run the retrieval benchmark with the provider's embedding model
    (`--target retrieval`), then `make reembed`;
  - commit the provider's baseline ([AI engineering](ai-engineering.md)).
- **Size the rate limits from the provider's quota** (tokens per minute),
  not the api's capacity. The quota binds first.
- **Know how to stop it.** The off switch stops every model call without
  a deploy ([turn the assistant off](../runbooks/turn-the-assistant-off.md)).
  Rehearse it once, from the UI and from the host.

**Done when:**
- the eval gate passes against the production model;
- a budget alarm exists at the provider;
- the cost panel shows real traffic;
- `PROD_CHECKS_WAIVED` is empty: nothing unsafe is waived (ADR-0023).

### Stage 4: a pilot team

📘 Access is already per team, so a pilot is a matter of who holds
groups: give one team `team:<slug>:*` at the provider, and leave everyone
else without access. For two to four weeks:
- **Watch the SLOs (below) daily.**
- **Collect feedback on answers.** A thumbs-up or down per answer is not
  built yet: it is the first product addition this stage asks for ([the
  PRD](../prd/0001-triage-assistant.md)).
- **Add real failures to the evals.** Every answer the pilot reports as
  wrong becomes an eval case, anonymised. Its trace says what it was
  given: note the trace id with the report (the chat's `meta` event has
  it).
- **Trace a share of requests,** not all: production's default keeps a
  tenth (`parentbased_traceidratio`, 0.1; ADR-0023), which kept p95 within
  0.2 ms of no tracing; every trace doubled it. Send them to the
  platform's OpenTelemetry Collector, and restrict who can read the trace
  store: it ignores teams ([AI observability](ai-observability.md)).
- **Import the pilot team's runbooks, and label 30 of their real
  questions** for the retrieval benchmark: the one cost RFC-0001 asked
  for that is not measured yet.
- **Settle the privacy questions** in [privacy](../privacy.md) (📘): the
  legal basis, the data-processing agreement, the works council, the
  retention periods.

**Done when:**
- the SLOs held for the whole pilot;
- the pilot team wants to keep it;
- every reported bad answer is a passing eval case.

### Stage 5: general availability, then a second host

📘 Grant access team by team. Plan the second host before the first
outage, not after it: [moving to a managed
platform](#moving-to-a-managed-platform) maps each container to a managed
service, and [operations](operations.md#single-points-of-failure-on-one-vm)
lists the single points of failure that a second host removes.

## Keeping it working

### Service level objectives

📘 SLOs turn "is it working?" into numbers with a budget. They use the
metrics that already exist ([operations](operations.md#what-is-measured)):

| SLO | Measured as | Target (30 days) |
|---|---|---|
| **Availability** | the share of api requests not answered 5xx: `http_requests_total{status!~"5.."} / http_requests_total` | 99.5%, a budget of 3.6 hours of errors a month |
| **Read latency** | p95 of `http_request_duration_seconds` for `GET /alerts` | under 300 ms |
| **Time to first word** | p95 of `llm_time_to_first_token_seconds` | under 3 s (0.2 s measured with a local model; a hosted one is usually slower) |
| **Answers completed** | the share of model calls with outcome `ok` or `truncated`, among those not `cancelled` | 99% |
| **Answer quality** | the eval suite on the production model, per release | the gate passes; safety 100% |

**The error budget is the point.** While budget remains, ship. When it
is spent, reliability work comes before features until it recovers.
Agree this with the product owner *before* the first incident.

The alerts today are threshold alerts (5% errors for 5 minutes). Once the
SLOs are agreed, replace them with **multi-window burn-rate alerts**:
- page when the budget burns 14.4× too fast over 1 hour (and over 5
  minutes): 2% of the month's budget gone in an hour;
- open a ticket when it burns 1× too fast over 3 days.

This pages on what users feel, and not on blips. A deliberate "off" is
not an outage: the off switch answers 503, which the error-rate alert
leaves out (`chat_refusals_total`), and so should the availability SLO.

### Game days

📘 The drills (`make drills`) inject one fault at a time into a stack
nobody depends on ([operations](operations.md#failure-modes)). A game day
adds the humans:
- **Schedule it.** Announce it, and have one person inject while the
  on-call engineer responds without knowing the scenario.
- **Scenarios worth rehearsing:**
  - the VM is gone: restore onto a new one from off-host backups, and
    time it;
  - the identity provider is down: signed-in users continue, new
    sign-ins fail; is the alert clear?
  - the model provider is down or rate limiting: chat errors, and
    everything else works;
  - the assistant gives harmful answers: turn it off, find what it was
    given in the audit trail, turn it back on;
  - the disk fills;
  - a bad release: roll back under load;
  - a leaked credential: rotate it, and see what breaks.
- **Record** for each: the time to detect (did an alert fire, or did a
  person notice?), the time to mitigate, what users saw, and what the
  runbook got wrong. Fix the runbook the same day.

### Incidents

📘
- **Severity** is decided by user impact, not cause:
  - SEV1: nobody can use it;
  - SEV2: a core flow is broken for some;
  - SEV3: degraded, with a workaround.
- **Roles:** one incident lead, who decides and communicates, and the
  people doing the fixing. In a small team, one person may hold both;
  say so out loud.
- **Timeline:** write it as you go. The request id in every error (`make
  trace id=...`) and the dashboards make it reconstructible. For a
  harmful answer, the audit trail says what the model was given and who
  wrote it (`make audit`, [AI security](ai-security.md)).
- **A blameless review within a week, for every SEV1 and SEV2:** what
  happened, why the system allowed it, what detection missed, and action
  items as issues, each with an owner. Most reviews should end in a new
  drill, an alert, or a gotcha line.

## Never tested: the honest list

Treat each of these as unknown until tested. Each one needs its own run
before the stage that depends on it.

| Not tested | Why it matters | How to test it |
|---|---|---|
| A cloud VM, a real domain, real Let's Encrypt certificates, and their renewal over months | the first renewal is 60 days in, when nobody is watching | stage 1; a certificate-expiry alert (an outside-in check) |
| The organisation's identity provider (Entra ID, Okta) | claims, group limits and token lifetimes differ from Keycloak's | stage 2; one real user per role |
| A hosted model provider: its latency, rate limits, outages, data terms | every chat number here is from a local GPU or the mock | stage 3: evals, a load test within quota, a provider-outage drill |
| macOS and Windows machines | the dev-environment chapter's advice for them is standard guidance, not observed | one developer on each platform through day one |
| Browsers other than Chromium | Playwright runs Chromium only | add the `firefox` and `webkit` projects to `tests/e2e/playwright.config.ts` |
| More than one host; managed Postgres; RDS Proxy; Kubernetes | pooling, failover and load balancer behaviour change | re-run `make drills` and `make load` on the new platform |
| Real user traffic | capacity numbers come from synthetic load on one box | the pilot's metrics |
| Tracing at production volume, through a collector | tracing's cost was measured at 200 req/s on one process, straight to Jaeger | the pilot, sampled, through the platform's collector: watch p99 and `event_loop_lag_seconds` |
| Restoring production-sized data | restores were rehearsed on small dumps (seconds) | a restore of a full-size dump, timed |
| Months of operation | table bloat and vacuum, log and disk growth, a major Postgres upgrade (17 → 18) | a staging host kept running; an upgrade rehearsal |
| A third-party security review or penetration test | the attack table ([security](security.md)) is self-assessed | before handling sensitive data |
| An accessibility audit | tests find controls by role, but nobody has audited with a screen reader | an audit, or axe in the e2e suite |
| Retention periods and erasure against a real policy | the job, export and erasure are built and tested; their periods (365 days) are placeholders, and keeping the audit trail after an erasure is legal's call | [privacy](../privacy.md): the decisions outside engineering |
| Answer quality on real questions | the evals hold 19 hand-written cases | the pilot's reported bad answers become cases |
| Retrieval on real runbooks, with a hosted embedding model | recall was measured on 8 hand-written runbooks and 19 questions, with a local model | the pilot's runbooks and 30 labelled questions; `--target retrieval` with the provider's model |
