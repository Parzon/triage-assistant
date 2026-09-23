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

## Sign-in and access ✅

Who may do what, how it is enforced, and how to connect your
organisation's identity provider. Decision record: ADR-0013.

### How signing in works

```
browser                    api (/api/auth/...)              identity provider
   | GET /api/auth/login     |                                     |
   |------------------------>| state, nonce, PKCE verifier stored  |
   |<-- 302 to the provider  | (login_requests); state cookie set  |
   |------------------------------------------------------------->| login page:
   |<------------------------------------------------------------| password, MFA...
   |      302 /api/auth/callback?code&state&iss                   |
   |------------------------>| state == cookie? iss == ours?       |
   |                         |-- code + verifier + client secret ->| (back channel)
   |                         |<-- ID token ------------------------|
   |                         | verify: signature, iss, aud, azp,   |
   |                         | exp, nonce; sync teams; new session |
   |<-- 302 next, session cookie                                   |
```

After that, every request carries one cookie, `__Host-triage_session`.
The api looks it up (one query: session, user, memberships), tells
Postgres who is asking (`set_config`, for row-level security), and runs
the route.

### Each attack and what stops it

| Attack | Control | Where | Test |
|---|---|---|---|
| Stealing the session with an XSS bug | `HttpOnly`: scripts cannot read the cookie. No token is ever in the browser. | `app/sessions.py` | `test_auth_flow.py`: cookie flags |
| Session on plain HTTP, or set by a subdomain | `Secure` and the `__Host-` prefix | same | same |
| Another site POSTing with your cookie (CSRF) | `Origin` must equal `PUBLIC_URL` on every state-changing request; `SameSite=Lax` as a second layer | `check_same_origin` | `test_sessions.py`, e2e `access.spec.ts` |
| Signing a victim in as the attacker (login CSRF) | `state` bound to a cookie set when *this browser* started signing in | `routes/auth.py` | `test_callback_from_another_browser_is_refused` |
| A stolen authorization code | PKCE (S256): the code is useless without the verifier, which never left the api | `oidc.py` | `test_authorization_url_asks_for_a_code_with_pkce` |
| A replayed code or callback | the sign-in row is deleted on first use | `finish_login` | `test_a_code_works_once` |
| A replayed or injected ID token | `nonce`, `exp`, `aud`, `azp`, and a signature by the provider's published key | `oidc.verify` | 11 cases in `test_oidc.py` |
| `alg: none`, or HMAC with a public key | algorithm allow-list (RS256, PS256, ES256), checked before any key is used | same | `test_unsigned_and_hmac_tokens_are_refused` |
| Another provider's response (mix-up) | the discovery `issuer` and the callback's `iss` must equal `OIDC_ISSUER` | `oidc.metadata`, `callback` | `test_metadata_naming_another_issuer...`, `test_provider_errors_and_mix_ups...` |
| Open redirect via `?next=` | only paths on this site (`//evil`, `/\evil`, `https://...` become `/`) | `safe_next` | `test_next_is_never_another_site` + unit |
| Probing other teams' ids | invisible = 404, like missing | `require_role` | `test_other_teams_alerts_are_not_found_not_forbidden` |
| A leaked database or backup | only SHA-256 of session tokens is stored | `UserSession` | — |
| Brute-forcing sign-in | per-IP limit on `/auth/*` (30/min); the provider's own lockout (Keycloak: `bruteForceProtected`) | `rate_limit_by_ip` | `test_sign_in_is_limited_per_address` |
| One user exhausting the service | rate limits per user, not per IP | `rate_limit` | `test_rate_limits_are_per_user_not_per_address` |
| The assistant quoting another team's alerts | its context is read with the caller's visibility, the same query as the list | `queries.newest_alerts` | `test_the_assistant_only_sees_the_askers_alerts` |

### Roles

| | viewer | responder | admin | org admin |
|---|---|---|---|---|
| read the team's alerts, ask the assistant | ✓ | ✓ | ✓ | every team |
| create alerts for the team | | ✓ | ✓ | every team |
| delete the team's alerts, list its members | | | ✓ | every team |

Ranked (`Role` is an `IntEnum`): a check is `role >= needed`, so adding a
right to a role is one line. The UI hides what a role cannot do; the api
enforces it. Hiding a button is never the control.

### Where roles come from

From the identity provider, at every sign-in: the claim named by
`OIDC_GROUPS_CLAIM` (default `groups`) is read. Values:
- `team:<slug>:<role>`: a role in a team. The slug is lowercase letters,
  digits and dashes. The highest role wins if there are several.
- `org:admin`: org admin.
- Anything else is ignored.

A team is created the first time a sign-in names it. Memberships are
replaced every time: someone removed from a group loses the role at their
next sign-in. **For immediate effect** (a leaver, a compromised account):
`make revoke email=<address> ENV=prod` ends all their sessions now.

### Connecting your organisation's provider

1. Register a **confidential web application** with the provider:
   - redirect URI `<PUBLIC_URL>/api/auth/callback`;
   - post-logout redirect URI `<PUBLIC_URL>/`;
   - authorization code flow with PKCE; client authentication by secret
     (`client_secret_basic`).
2. Make it send the roles claim, with values in the format above:

   | Provider | How |
   |---|---|
   | Keycloak | groups named `team:payments:responder`, with a "Group Membership" mapper, *Full group path* off (a leading `/` is tolerated anyway). See the committed realm. |
   | Okta | groups with those names, and a groups claim filter on the app (for example "starts with `team:`"). |
   | Microsoft Entra ID | **app roles** with those values (`team:payments:responder`), assigned to groups; set `OIDC_GROUPS_CLAIM=roles`. Entra's `groups` claim carries object ids, not names. |
   | Google Workspace | has no groups claim in its ID token. Put an IdP that has one in between (Keycloak, Okta, Entra), or add a lookup against the Directory API. |

3. In `.env`: remove `idp` from `COMPOSE_PROFILES`, and set:
   - `OIDC_ISSUER`: exactly the `iss` of the provider's tokens;
   - `OIDC_DISCOVERY_URL=` (empty);
   - `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`;
   - `OIDC_GROUPS_CLAIM`, if it is not `groups`.
4. Check: `make prod-up`, then `curl -s localhost:8088/api/ready` shows
   `"identity_provider": "ok"`. Sign in, and `GET /api/me` lists your
   teams.

Gotchas:
- **The issuer must match exactly**, trailing slash included. Entra's is
  `https://login.microsoftonline.com/<tenant>/v2.0`; Okta's
  `https://<org>.okta.com/oauth2/default` or `https://<org>.okta.com`,
  depending on the authorization server.
- **The api must reach the provider over HTTPS from inside its
  container.** The production image trusts the system CAs. A provider
  behind a corporate CA needs that CA added to the image.
- **The api's clock must be right** (NTP): tokens are checked with 60 s
  of leeway.
- **Google does not support sign-out at the provider:** signing out ends
  only this site's session.

### Row-level security: the second barrier ✅

The app's checks are one line per query; Postgres enforces the same rule
underneath (ADR-0014). The api tells Postgres who is asking in every
transaction, and policies on `alerts` use it:
- **SELECT:** the caller's teams (any role), or an org admin, or the
  Alertmanager service.
- **INSERT:** teams where the caller is a responder or above.
- **DELETE:** teams where they are an admin.
- **UPDATE:** nobody.

**No context means no rows:** a query that forgot to say who is asking
sees nothing and changes nothing. The owner role (migrations, seeding,
backups) is not subject to the policies; the app role is.

✅ `test_row_level_security.py` goes around the app and shows Postgres
refusing. One test runs the org admin's "every team" read for a viewer
(the bug this exists for), and gets only the viewer's team back.

### Demo users (the bundled Keycloak)

`alice` (payments responder, platform viewer), `bob` (platform admin),
`carol` (org admin), `dave` (no team), all with `DEMO_USER_PASSWORD`.
Keycloak's admin console is at `http://localhost:5173/auth/admin/` in dev
(`admin` / `KEYCLOAK_ADMIN_PASSWORD`). The edge never exposes it, nor the
master realm (measured: 404). On a demo reachable from the internet, set
strong values for both passwords, or remove the demo users from the realm
file.

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

- **Only the edge is published** (nginx stays on the host's loopback).
  The api, database, pooler, Valkey and Keycloak have no host ports. The
  edge passes the bundled Keycloak's sign-in pages only
  (`/auth/realms/triage/*`, `/auth/resources/*`); its admin console and
  the master realm answer 404. The dashboards listen on 127.0.0.1 (reach them through
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

- **HTTPS everywhere** (ADR-0012):
  - the TLS edge terminates TLS 1.3 with automatic certificates, and
    redirects plain HTTP;
  - HSTS is set for real domains;
  - the edge overwrites any client-sent `X-Forwarded-For`;
  - nginx and the api are not reachable from outside.
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
- **Rate limits** per signed-in user: 60 alert requests and 10 chat
  requests per minute by default. Sign-in itself is limited per IP (30
  per minute). When Valkey is down they fail *open* (ADR-0004 weighs the
  three options), so during that outage there is no limit. If rate
  limiting becomes a security control rather than a courtesy, revisit
  that choice: fail closed for chat, which costs money.
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
  outcomes and the request id. Access log lines carry the user's id
  (who did what), never their email or name.

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

- [ ] A real domain in `SITE_ADDRESS` (runbook, section 5), and `HSTS_MAX_AGE=31536000` once HTTPS works
- [ ] `DOCS_ENABLED=false` if the API should not be advertised
- [ ] Your organisation's identity provider connected (above), the
      bundled Keycloak's profile removed
- [ ] Rate limiting that fails closed for chat, if abuse matters more
      than availability
- [ ] Image and secret scanning in CI
- [ ] The provider's data-retention terms reviewed; redaction of
      secrets in alert text
- [ ] Backups encrypted and stored off the host
- [ ] A security contact and a way to report issues (`SECURITY.md`)
