# Security

The controls in place, why each exists, and what is left to do before
real users or real data. ✅ = in place and checked here, 📘 = recommended,
not done.

## Secrets ✅

- **Never in git.** The repository is public. `.env` is gitignored, and
  `.env.example` holds placeholders only. Dumps (`backups/`) and packet
  captures (`.captures/`) are gitignored too.
- **Never in images.** Configuration arrives as environment variables at
  runtime. `.dockerignore` keeps `.env` out of the build context.
- **Generated on servers.** cloud-init replaces every placeholder
  password with `openssl rand -hex 24`, and makes `.env` readable by its
  owner only.
- **Typed as secrets in code.** `SecretStr` fields print as `**********`.
  An empty webhook token disables the webhook rather than accepting
  anything.
- **A leaked secret is rotated, not deleted.** Anything pasted into a
  chat, a ticket, a log or a commit is burned. On a public repository it
  has been copied the moment it was pushed.
- 📘 On a managed platform: a secret store (Secrets Manager, Key Vault)
  with rotation, injected into the task environment.

## Least privilege

**Database roles** (✅ verified by trying each forbidden operation):

| Role | Can | Cannot | Used by |
|---|---|---|---|
| owner (`POSTGRES_USER`) | everything, including DDL | — | migrations only, directly to Postgres |
| app (`APP_DB_USER`) | read and write rows | `DROP` ("must be owner"), `CREATE TABLE` ("permission denied for schema public"), `ALTER` | the api, through PgBouncer |
| monitor (`MONITOR_DB_USER`) | read statistics (`pg_monitor`) | read or change data | postgres-exporter |

The app role also carries its own limits: `statement_timeout 10s`,
`idle_in_transaction_session_timeout 30s`. A compromised or buggy api
cannot drop the table, and cannot hold the database hostage with one
query.

**Containers** (✅ `compose.prod.yaml`, checked by `make image-check`):
- Non-root: the api runs as UID 10001, nginx as the unprivileged
  image's user.
- Read-only root filesystem, with a tmpfs `/tmp`.
- `cap_drop: ALL` and `no-new-privileges`.
- The app's code is owned by root and read-only to the app user, so a
  compromised process cannot rewrite it.
- No dev tools in the production image (no ruff, pytest or uv).
- CPU and memory limits, with no swap, on every service.
- Debug tooling (py-spy, strace) runs as a separate container that
  borrows the target's namespaces on demand. The api never gets
  `SYS_PTRACE`.

**The host:**
- The `docker` group is root-equivalent: only the deploy user is in it.
- SSH keys only, no passwords, root login off (cloud-init).
- Automatic security updates.

## Network exposure ✅

- **Only nginx is published.** The api, database, pooler and Valkey have
  no host ports. The dashboards listen on 127.0.0.1 (reach them through
  an SSH tunnel), and so do the dev ports.
- **Published ports bypass the host firewall** (networking chapter, with
  the NAT rules). Anything added with `ports:` is reachable, whatever
  ufw says.
- **nginx refuses the internal endpoints:** `/api/metrics` and the
  Alertmanager webhook return 404 from outside. Prometheus and
  Alertmanager reach them on the private network.
- **debugpy is never in production.** The debug overlay is a separate
  compose file, bound to 127.0.0.1. An open debugpy port is remote code
  execution.
- The OpenAPI docs (`/api/docs`) are on by default, which is useful for
  demos. Set `DOCS_ENABLED=false` on an internet-facing deployment that
  shouldn't advertise its API.

## HTTP ✅

- **Security headers** on every page (`snippets/security-headers.conf`):
  - a Content Security Policy (`default-src 'self'`, no inline scripts,
    `frame-ancestors 'none'`); the e2e suite fails on any CSP violation;
  - `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`;
  - a strict `Referrer-Policy` and a `Permissions-Policy`.
- **`server_tokens off`:** no nginx version in headers. A 1 MB request
  body limit.
- **Client identity for rate limits** comes from `X-Forwarded-For`,
  which nginx *overwrites* with the connecting address. A client cannot
  claim another IP (measured). Behind a load balancer, trust only the
  load balancer's subnet (networking chapter).
- **Rate limits** per client IP: 60 alert requests and 10 chat requests
  per minute by default. When Valkey is down they fail *open* (ADR-0004
  weighs the three options), so during that outage there is no limit.
  If rate limiting becomes a security control rather than a courtesy,
  revisit that choice: fail closed for chat, which costs money.
- **The Alertmanager webhook** needs a bearer token, compared in
  constant time (`hmac.compare_digest`). The token reaches Alertmanager
  as a mounted secret file, not an environment variable.
- **Errors never leak internals:** tracebacks go to the log, and the
  client gets a code, a message and the request id.

## The model: LLM-specific risks

- **Prompt injection.** Alert text is attacker-controllable: anyone who
  can send an alert, or influence a monitored system's messages, can
  write text that ends up in the prompt. The system prompt tells the
  model that alert text is data, not instructions. That is a mitigation,
  not a guarantee. The real control is that **the model has no tools**:
  the worst a successful injection can do is produce a wrong or
  misleading answer. If the assistant ever gets actions (restart a
  service, open a ticket), every action needs human confirmation, and
  the permissions of the account behind it bound the damage.
- **Output handling.** The answer is rendered as text (`white-space:
  pre-wrap`), never as HTML or markdown. A model coaxed into writing
  `<script>` shows it, rather than running it. Keep it that way, or
  sanitise if you render markdown.
- **Data sent to the provider.** Alert text and user questions leave
  your network. Check the provider's retention and training terms,
  and prefer a zero-retention agreement or a model in your own cloud
  account (Bedrock, Azure OpenAI, Vertex). Redact what should not leave:
  secrets that systems print into alerts, personal data.
- **Cost as an attack.** Chat requests cost money. There is a per-client
  rate limit (10/min), `LLM_MAX_OUTPUT_TOKENS` (800), the total stream
  cap (120 s), and a cost-per-hour dashboard panel. 📘 Add a
  provider-side budget alert.
- **Logs.** Questions and answers are not logged: only lengths, timings,
  outcomes and the request id.

## Supply chain

- ✅ **Lockfiles with hashes:** `uv.lock` (installed with `--frozen`),
  `package-lock.json` (`npm ci`). A build installs exactly what was
  reviewed.
- ✅ **GitHub Actions pinned to commit SHAs**, not tags. A tag can be
  moved to malicious code, as in the 2025 tj-actions/changed-files
  compromise.
- ✅ **Pinned versions** of base images and tools, several with checksum
  verification: the vegeta and JMeter downloads, the oha image by
  digest.
- ✅ **Release images carry provenance and an SBOM** (what went into them),
  stored next to them in GHCR.
- ✅ **Dependabot** (`.github/dependabot.yml`) proposes updates weekly,
  grouped, for GitHub Actions, the api (uv), the web (npm), the e2e
  suite and the Dockerfiles. Each update is a PR that has to pass CI.
- 📘 **Image scanning** (Trivy or Grype) on the release images, and a
  policy on what severity blocks a release.
- 📘 **Secret scanning** in CI (gitleaks via its CLI; the GitHub Action
  needs a licence for organisations). Enable GitHub's secret-scanning
  push protection on the repository too.
- 📘 **Pin base images by digest** too, with Dependabot bumping them:
  tags like `python:3.13-slim` move under you.

## Before real users 📘

- [ ] HTTPS in front (runbook, section 5), and HSTS once it's permanent
- [ ] `DOCS_ENABLED=false` if the API should not be advertised
- [ ] Authentication: there is none. Anyone who can reach the site can
      read alerts and ask questions. Put it behind SSO (an identity-aware
      proxy, or OIDC in the app) before any real data goes in.
- [ ] Rate limiting that fails closed for chat, if abuse matters more
      than availability
- [ ] Image and secret scanning in CI
- [ ] The provider's data-retention terms reviewed; redaction of
      secrets in alert text
- [ ] Backups encrypted and stored off the host
- [ ] A security contact and a way to report issues (`SECURITY.md`)
