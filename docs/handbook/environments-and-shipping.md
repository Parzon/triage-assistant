# Environments and shipping

Where the code runs on its way from a laptop to users, what each place is
for, and how a change moves between them. The step-by-step for a single
VM is `docs/runbooks/demo-vm.md`; the decisions are in ADR-0011.

## The environments

| Environment | What for | What runs | Data | Created / reset by |
|---|---|---|---|---|
| **Laptop (dev)** | writing and debugging code | the dev stack: hot reload, debugger, mock LLM, the bundled Keycloak (demo users) | seeded, disposable | `make up` / `make nuke` |
| **Laptop (prod shape)** | "does it work like production?" before pushing: nginx, gunicorn, read-only containers; e2e, load tests, drills | the production images, built locally | seeded | `make prod-up` / `make prod-down` |
| **Test stack** | integration tests | a throwaway compose project | created per run, deleted after | `make test-api` |
| **CI** | the same checks on a clean machine, for every PR | GitHub-hosted runners: lint, tests, image check, e2e through the production stack | none kept | every push |
| **Staging / demo VM** | shared, always-on, production-shaped: demos, pilots, real-provider testing, drills | released images from GHCR; the bundled Keycloak, or a test tenant of the organisation's provider | demo data; never production data | `infra/vm/cloud-init.yaml`, then `make deploy` |
| **Production** | users | the same images, on one VM or a managed platform (below); **the organisation's identity provider**, never the bundled Keycloak | real | the platform's deploy |

Answers to the usual questions:
- **Do developers need a dev VM?** No: laptops run everything (the
  dev-environment chapter has each operating system). A cloud dev VM,
  with VS Code Remote-SSH, is the fallback where laptops can't run
  Docker (locked-down corporate machines) or are too small.
- **What is a test or staging VM for?** A shared place that runs the
  production shape all the time: real provider keys, demos, the alert
  and dashboard loop, failure drills. Nobody's laptop needs to be on.
- **For a manager or customer demo:** a staging-like VM, sized from the
  runbook's IT request (4 vCPU, 8 GB, 40 GB). Deploy a released tag,
  and run through the pre-demo checklist.
- **Different laptops** (Linux, Mac, Windows, Intel or ARM) all run the
  same images. Tool versions live in the images, not on the laptops.

## One artifact, promoted

The same image moves through every environment. Only configuration
changes:

```
laptop ──PR──► CI (lint, tests, image check, e2e) ──squash──► main ──tag vX.Y.Z──► release workflow
                                                                              │ builds once, pushes
                                                                              ▼
                                   ghcr.io/<owner>/triage-assistant-{api,web,edge}:X.Y.Z
                                              │                           │
                                   make deploy tag=X.Y.Z        the same tag, later
                                              ▼                           ▼
                                       staging / demo VM             production
```

- **Build once.** The bytes tested on staging are the bytes production
  runs. A host that builds from source can differ from the next one
  (a base image updated in between, a package republished).
- **Configuration only from the environment** (twelve-factor):
  `DATABASE_URL`, keys and limits come from `.env` on a VM, or from the
  platform's secret store. Nothing environment-specific is baked into
  an image.
- **Version = tag.** `latest` is never deployed: it can't be rolled back
  to, and two pulls of it can differ.

### Secrets per environment

| Where | Secrets come from |
|---|---|
| laptop | `.env` from `.env.example` (placeholder passwords, the demo users' included; fine locally) |
| CI | `.env.example` as is (throwaway stacks) |
| VM | `.env` with generated passwords (cloud-init: `openssl rand`), `chmod 600`, owned by the `deploy` user; the LLM key and the identity provider's client secret typed in once |
| managed platform | a secret store (AWS Secrets Manager / SSM, Azure Key Vault, GCP Secret Manager) injected as environment variables into the task |

Never in: the image, git, logs, or a ticket. A secret that reached any of
them is rotated, not just deleted.

## Releasing and deploying

- **Cut a release:** `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin
  vX.Y.Z` on main. CI publishes both images with provenance and an SBOM
  (runbook, section 8).
- **Deploy to a VM:** `make deploy tag=X.Y.Z`: migrations, then the new
  api next to the old one, then the old one drained. Zero failed
  requests during the api swap (measured), ~0.3 s of refused connections
  when nginx is replaced. A new api that never gets healthy is removed
  automatically.
- **Roll back:** deploy the previous tag.
- **Migrations are backward compatible** (expand, then contract), because
  the old version keeps serving while they run (daily-work chapter). The
  teams migration (v0.2.0) is the worked example: measured under load,
  the previous release kept serving with 0 errors (performance chapter).
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
  you have restored it (`DUMP=... make fresh-host-test`).

## Moving to a managed platform 📘

When one VM is no longer enough (availability, scale, compliance), each
container maps onto a managed service. The table uses AWS; Azure and GCP
have the same shapes.

| Here | AWS | Azure | GCP | What changes in this repo |
|---|---|---|---|---|
| api container (gunicorn) | ECS on Fargate (or EKS) | Container Apps / AKS | Cloud Run / GKE | `IMAGE_PREFIX` → the cloud registry. One task per 1–2 vCPU with `WEB_CONCURRENCY` = the task's vCPUs. The read-only root filesystem needs a writable `/tmp` volume (metrics files, the control socket) |
| the TLS edge (Caddy) + nginx (web) | ALB for TLS and routing (edge profile off); static files on S3 + CloudFront, or keep the nginx container | Application Gateway / Front Door | HTTPS LB + Cloud CDN | drop `edge` from `COMPOSE_PROFILES` and publish nginx; the SSE rules (no buffering, idle timeout > 15 s) move to the load balancer and CDN config |
| Postgres | RDS or Aurora PostgreSQL, Multi-AZ | Azure Database for PostgreSQL (flexible) | Cloud SQL | `DATABASE_URL`, `MIGRATIONS_DATABASE_URL`; the roles script (`infra/postgres/initdb`) run once as a migration or by hand; backups become the service's snapshots plus point-in-time recovery |
| PgBouncer | RDS Proxy, or keep PgBouncer as a sidecar | PgBouncer built into the flexible server | a sidecar | RDS Proxy "pins" sessions that use session state (the app uses none: transaction-scoped only). Re-run the database drills against whichever you choose |
| Valkey | ElastiCache for Valkey | Azure Cache for Redis | Memorystore for Valkey | `REDIS_URL` (TLS: `rediss://`) |
| mock LLM | the real provider (or Bedrock / Azure OpenAI / Vertex, all OpenAI-compatible or behind a gateway) | | | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |
| the bundled Keycloak | the organisation's identity provider (IAM Identity Center, Cognito, or the corporate Entra ID / Okta) | Entra ID | Cloud Identity / an IdP federated in | `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_GROUPS_CLAIM`; `idp` out of `COMPOSE_PROFILES` (security chapter, "Connecting your organisation's provider") |
| `migrate` service | a one-off task run before the service update (in the deploy pipeline) | a Container Apps job | a Cloud Run job | nothing: it's already a separate command |
| Prometheus / Grafana / Alertmanager | Amazon Managed Prometheus + Managed Grafana, or CloudWatch | Azure Monitor managed Prometheus + Grafana | Managed Service for Prometheus | scrape via service discovery; cAdvisor and node-exporter give way to the platform's container metrics |
| `.env` | Secrets Manager / SSM Parameter Store | Key Vault | Secret Manager | nothing in the code: they arrive as environment variables |
| `make deploy` | ECS rolling deployment with the deployment circuit breaker; health check on `/ready` | revisions | revisions | `/ready` is the target-group health check; `/health` is the liveness check |

What stays true on any platform: the health endpoints' split (liveness
doesn't check dependencies; readiness does, but not the soft ones), the
graceful drain (`stop_grace_period` > `graceful_timeout`), the timeouts
chain (ADR-0010), and the failure drills. Run them again on the new
platform: its load balancer and proxies behave differently.

## Handing it to an infrastructure team

What they need, all of it in this repo:

| They ask | Answer |
|---|---|
| What runs? | three images (api, web, the TLS edge), one migration command, Postgres 17, Valkey 8, a model provider, an OIDC identity provider |
| Ports | web 8080 (HTTP, non-root nginx); api 8010 (internal only) |
| Health | `/api/health` (liveness: process up), `/api/ready` (readiness: database reachable; Valkey and the identity provider reported, not required), nginx `/healthz` |
| Resources (measured) | api: 2 CPU / 1 GiB for ~1,000 signed-in reads/s or 500 streams; idle ~170 MiB. Postgres: 1 GiB. The rest in `compose.prod.yaml` |
| Scale on | CPU and `event_loop_lag_seconds` (api), active streams |
| Configuration | the settings reference (daily-work chapter); secrets marked |
| Logs | JSON lines on stdout, one request id across nginx and the api |
| Metrics | Prometheus at `/metrics` on 8010 (multiprocess, summed across workers); alert rules and their tests in `infra/observability` |
| Deploy order | migrations (backward compatible) → api (rolling, readiness-gated) → web |
| Stop | SIGTERM; up to 120 s of graceful drain for streams; SIGKILL after 130 s |
| Egress | the model provider's API, and the identity provider's (metadata, keys, the code exchange), over HTTPS; nothing else at runtime |
| Identity | an OIDC confidential client: redirect URI `<PUBLIC_URL>/api/auth/callback`, post-logout `<PUBLIC_URL>/`; a claim carrying `team:<slug>:<role>` values (security chapter) |
| Backups | the database only (Valkey holds disposable rate-limit counters). It holds sessions: a restore signs out whoever signed in after the dump |
| Known limits | the failure-modes chapter (single points of failure, the one unbounded fault) |
