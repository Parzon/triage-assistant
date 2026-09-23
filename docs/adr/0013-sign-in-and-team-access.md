# ADR-0013: Sign-in with the organisation's identity provider; teams own alerts

**Status:** accepted
**Date:** 2026-09-23

## Context

Until v0.1.0, anyone who could reach the site saw every alert and could
ask the assistant about all of them. A real deployment needs:
- **Single sign-on.** People sign in with the account they already have:
  one password, the organisation's MFA, access removed in one place when
  they leave.
- **Ownership.** A team sees its own alerts, not every team's.
- **Ranked permissions.** Reading, creating and deleting are not the same
  right.
- **An assistant that cannot leak.** The model must only ever be given
  alerts the person asking may see. Otherwise "summarise what's on fire"
  would quote another team's incidents.

## Decision

1. **OpenID Connect, authorization code flow with PKCE, the api as a
   confidential client** (the "backend for frontend" pattern). The browser
   carries a one-time code and nothing else. The api exchanges the code
   with its client secret, verifies the ID token itself (`app/oidc.py`),
   and issues its own session. No token ever reaches the browser, so none
   can be stolen from it.
2. **Server-side sessions in Postgres.**
   - The cookie is `__Host-triage_session`: `HttpOnly`, `Secure`,
     `SameSite=Lax`, `Path=/`, no `Domain`.
   - It holds a random 256-bit token. Only the token's SHA-256 is stored.
   - A session lasts 12 hours at most, and ends after 2 idle hours.
   - Signing out deletes the row, and `make revoke` ends every session of
     a user.
3. **The identity provider is the source of truth for access.**
   - Its groups claim (`OIDC_GROUPS_CLAIM`) carries `team:<slug>:<role>`
     and `org:admin`.
   - Every sign-in replaces the user's memberships with what the claim
     says, and creates a team the first time it is named.
   - Users are identified by `(issuer, subject)`, never by email.
4. **Ranked roles per team** (`app/access.py`):
   - viewer: read the team's alerts, ask the assistant about them;
   - responder: also create alerts;
   - admin: also delete alerts, and see the team's members;
   - an org admin is admin in every team.
   - A resource in a team the caller cannot see is a **404**, the same
     answer as for one that does not exist. A 403 means "you can see it,
     your role is too low".
5. **The same visibility rule everywhere.** The alert list and the
   assistant's context use one query (`app/queries.py`). Several teams
   are read team by team and merged (a LATERAL join), so the cost is
   bounded whatever the data looks like.
6. **Browser attacks, each with its own control:**
   - Cross-site requests: every state-changing request must carry
     `Origin` equal to `PUBLIC_URL`.
   - Login CSRF: the `state` is bound to a short-lived cookie.
   - Code interception: PKCE, S256.
   - Token replay: a nonce.
   - Open redirects: `next` must be a path on this site.
   - Identity provider mix-up: the discovery document's `issuer`, and
     the callback's `iss` (RFC 9207), must equal `OIDC_ISSUER`.
7. **A bundled identity provider for everything but production:** the
   `idp` compose profile runs Keycloak with a committed realm
   (`infra/keycloak/triage-realm.json`), as `mock` runs a stand-in model.
   - Browsers reach it under `/auth` on the app's own origin: through
     the edge, which exposes the realm's user-facing endpoints only, and
     through the Vite proxy in dev.
   - The api calls it directly, on its back channel.
   - Production swaps it for the organisation's provider with `OIDC_*`
     settings. No code changes.
8. **The identity provider is a soft dependency.** Sessions are this
   service's own, so signed-in users carry on through a provider outage.
   - The api checks the provider every 30 s.
   - `/ready` reports it as `degraded`, never `not_ready`.
   - `IdentityProviderDown` alerts: nothing else would notice.
9. **Postgres is told who is asking, in every transaction**
   (`set_config(..., true)`, `app/db.py`). This is the expand half for
   row-level security (ADR-0014).
   - The migration adds `alerts.team_id` with a default, so the previous
     release keeps inserting during a rolling deploy.
   - The contract release enables the policies and drops the default.
10. **Scripts get sessions the same way people do.**
    - `app.cli session` runs `sign_in()` without the provider, under a
      separate issuer, so it can never take over a real account.
    - Load tests, drills and smoke checks use it, or they sign in
      through the real flow (`scripts/lib/session.sh`).

## Alternatives considered

- **The SPA as a public OIDC client, holding tokens in the browser.** Any
  XSS bug could then read the access token, and refresh tokens have no
  safe home in a browser. The IETF's guidance for browser-based apps
  recommends a backend for anything sensitive.
- **A signed token (JWT) as the session cookie.** It is stateless, but
  cannot be revoked before it expires, carries stale roles, and grows
  with every claim. One primary-key lookup per request is cheap
  (measured below).
- **Sessions in Valkey.** Ours is configured as disposable: LRU eviction,
  no persistence (ADR-0002/0004). Under memory pressure it would sign
  people out. Postgres is already the durable store.
- **An auth library (Authlib) instead of our own protocol module.** A
  fine choice. Ours, `app/oidc.py`, is ~320 lines including its
  comments, with every check tested (`tests/unit/test_oidc.py`). All
  cryptography is PyJWT's and `cryptography`'s: nothing hand-rolled.
- **An identity-aware proxy (oauth2-proxy) in front of nginx.** It
  authenticates, but team-level authorization still needs the identity
  and the groups inside the app. That would mean passing headers from
  the proxy, and trusting them. A reasonable choice for apps without
  per-team data.
- **Memberships managed in the app.** That is a second source of truth,
  and it lags offboarding: someone removed at the provider would keep
  their teams here.

## Consequences

Measured on this project:
- **Tests.**
  - 190 api tests (96% coverage), including the real flow against
    Keycloak: code replay, login CSRF, mix-up, open redirect, sign-out
    at the provider, provider down.
  - 47 web tests.
  - 12 browser tests: sign-in and sign-out through Keycloak's page,
    isolation between two users, a refused cross-site POST, the admin
    console not reachable.
- **Cost.** A/B on one host, same data, the cheapest endpoint (the
  global alert list):

  | | 500 req/s | 1,000 req/s | 1,500 req/s offered |
  |---|---|---|---|
  | before sign-in (v0.1.0) | p95 2.1 ms, 0.53 core | p95 2.6 ms, 1.09 cores | p95 6.4 ms, 1.71 cores |
  | with sign-in | p95 4.0 ms, 0.95 core | p95 19.7 ms, 1.86 cores | saturated at 1,039 req/s |

  Authentication roughly doubles the CPU of a trivial read: about 530
  instead of 920 such reads per second per core. The first version (a
  query per table, a transaction of its own) was worse: 933 req/s
  achieved and p95 1.07 s at 1,000 offered. Folding it into one query
  in the request's transaction gave 999 req/s and p95 19.7 ms. For chat,
  the model dominates; the cost does not show.
- **Migration under load.** The expand migration ran against 2 M alerts
  (379 MB) while v0.1.0 served reads and writes: 1.5 s, and 116,708
  requests with 0 errors.
- **Breaking change.** Every endpoint now needs a session, so v0.2.0 is
  a breaking release. Machine clients other than Alertmanager (which
  keeps its bearer token) have no credential type yet (📘 below).
- **Role changes apply at the next sign-in**, up to 12 hours later.
  `make revoke email=...` makes them immediate.
- **During a provider outage** the sign-in redirect still goes to the
  provider (its metadata is cached), and the user sees its error page.
  The alert, not the user, is the signal.

Not done (📘):
- **Back-channel logout:** the provider telling us a session ended
  there.
- **Credentials for machine clients:** OAuth client credentials, with
  access tokens the api would verify.
- **A session list for users.**
- **An append-only audit log:** today it is the access log's `user_id`.
