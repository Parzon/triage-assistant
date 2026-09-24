# Going to production, and battle-testing it

What stands between this repository and real users, in stages, each
with a way to prove it is done. Then:
- how to keep proving it in production: SLOs, canaries, game days,
  incidents;
- the list of what has never been tested.

✅ = done and measured here. 📘 = the plan, not exercised here.

## Where it stands

✅ **Proven on one host, in the production shape:**
- HTTPS through the TLS edge, with automatic certificates rehearsed
  against Let's Encrypt's test CA;
- rolling deploys that dropped no request, and rollbacks across a
  migration;
- 22 failure drills;
- load up to the measured knee;
- backups, restored and rehearsed on a clean host;
- releases for amd64 and arm64, smoke-tested after publishing;
- sign-in against a real OIDC provider (Keycloak);
- evals against a real model (local);
- runbook search against a local embedding model, on a hand-written
  corpus;
- a trace per answer (retrieval, the model, their attributes), with its
  cost measured under load;
- an audit trail of who wrote what the assistant reads, and what each
  question was given, which the api cannot rewrite.

📘 **Not yet:**
- a cloud VM with a real domain;
- the organisation's identity provider;
- a hosted model provider;
- real users;
- a second host.

The stages below close these in order. Each stage ends in something
measurable, not a feeling.

## Stage 1: one cloud VM behind HTTPS 📘

Follow [the VM runbook](../runbooks/demo-vm.md): what to ask IT for,
the bootstrap with cloud-init, `.env`, the first start, HTTPS, deploys,
backups.

- **Network:**
  - DNS points a name at the VM;
  - inbound traffic only on 443, plus 80 for certificate renewal and the
    redirect;
  - SSH only from the company network, or through the cloud's session
    manager (no port 22 at all).
- **Certificates:** Caddy gets and renews them from Let's Encrypt. It
  was rehearsed against Pebble, Let's Encrypt's test CA. The
  first real renewal is about 60 days after the first certificate:
  put a calendar reminder on it.
- **Backups leave the host.** A dump on the VM's disk dies with the VM.
  Copy each one to object storage with a retention rule.

**Done when:**
- `make prod-up` brings the stack up on the VM from the checkout;
- the site serves a publicly trusted certificate;
- `make drills` matches the failure-mode matrix;
- a backup copied off the VM restores onto a fresh VM, with the time
  written down: that time is your recovery time (RTO).

## Stage 2: the organisation's identity provider 📘

The security chapter has the steps: "Connecting your organisation's
provider".
- **Register** a confidential client.
- **Redirect URIs:** `<PUBLIC_URL>/api/auth/callback`, and
  `<PUBLIC_URL>/` after logout.
- **The groups claim** must carry `team:<slug>:<role>` values:
  - Entra ID: app roles;
  - Okta: a groups claim with a filter;
  - Google: groups via Cloud Identity.
- **Remove the bundled Keycloak:** take `idp` out of
  `COMPOSE_PROFILES`.

**Done when:**
- people sign in with their company accounts;
- each role is checked by a real member: viewer, responder, admin, org
  admin;
- removing someone at the provider, then `make revoke email=...`, ends
  their sessions at once;
- the security team has agreed the session lifetimes (12 h absolute,
  2 h idle by default).

## Stage 3: a hosted model provider 📘

- **The API key:**
  - a key for this service only, with a spending limit;
  - it lives in the secret store, never a chat or a ticket.
- **The data terms:**
  - zero data retention, or no training on your data;
  - a region your data may be processed in.

  Alert text, runbook text and questions leave your network, to the chat
  model and to the embedding model ([security](security.md)).
  Ask vendors for zero data retention, quota, region and price in
  writing, before choosing.
- **Evals first:**
  - calibrate the judge;
  - run the whole suite with `--repeat 10`, and the leak case with
    `--repeat 200` at least: one leak in 200 already puts the bound over
    the 1.5% target, and then it takes more runs;
  - run the retrieval benchmark with the provider's embedding model
    (`--target retrieval`), then `make reembed`;
  - commit the provider's baseline ([AI engineering](ai-engineering.md)).
- **Size the rate limits from the provider's quota** (tokens per
  minute), not the api's capacity. The quota binds first (failure
  modes, bottleneck 7).

**Done when:**
- the eval gate passes against the production model;
- a budget alarm exists at the provider;
- the cost panel shows real traffic.

## Stage 4: a pilot team 📘

Access is already per team, so a pilot is a matter of who holds groups:
- give one team `team:<slug>:*` at the provider;
- leave everyone else without access.

For two to four weeks:
- **Watch the SLOs (below) daily.**
- **Collect feedback on answers.** A thumbs-up or down per answer is not
  built yet: it is the first product addition this stage asks for
  ([the PRD](../prd/0001-triage-assistant.md)).
- **Add real failures to the evals.** Every answer the pilot reports as
  wrong becomes an eval case, anonymised. Its trace says what it was
  given: note the trace id with the report (the chat's `meta` event has
  it).
- **Trace a share of requests,** not all: `OTEL_TRACES_SAMPLER=
  parentbased_traceidratio`, `OTEL_TRACES_SAMPLER_ARG=0.1` kept p95 within
  0.2 ms of no tracing; every trace doubled it. Send them to the
  platform's OpenTelemetry Collector, and restrict who can read the trace
  store: it ignores teams ([AI observability](ai-observability.md)).
- **Import the pilot team's runbooks, and label 30 of their real
  questions** for the retrieval benchmark: the one cost RFC-0001 asked
  for that is not measured yet.

**Done when:**
- the SLOs held for the whole pilot;
- the pilot team wants to keep it;
- every reported bad answer is a passing eval case.

## Stage 5: general availability, then a second host 📘

Grant access team by team. Plan the second host before the first
outage, not after it:
- [environments and shipping](environments-and-shipping.md) maps each
  container to a managed service;
- [failure modes](failure-modes.md) lists the single points of failure
  that a second host removes.

## Service level objectives 📘

SLOs turn "is it working?" into numbers with a budget. They use the
metrics that already exist ([observability](observability.md)):

| SLO | Measured as | Target (30 days) |
|---|---|---|
| **Availability** | the share of api requests not answered 5xx: `http_requests_total{status!~"5.."} / http_requests_total` | 99.5%, a budget of 3.6 hours of errors a month |
| **Read latency** | p95 of `http_request_duration_seconds` for `GET /alerts` | under 300 ms |
| **Time to first word** | p95 of `llm_time_to_first_token_seconds` | under 3 s (0.2 s measured with a local model; a hosted one is usually slower) |
| **Answers completed** | the share of model calls with outcome `ok` or `truncated`, among those not `cancelled` | 99% |
| **Answer quality** | the eval suite on the production model, per release | the gate passes; safety 100% |

**The error budget is the point.**
- While budget remains, ship.
- When it is spent, reliability work comes before features until it
  recovers.

Agree this with the product owner *before* the first incident.

The alerts today are threshold alerts (5% errors for 5 minutes). Once
the SLOs are agreed, replace them with **multi-window burn-rate
alerts**:
- page when the budget burns 14.4× too fast over 1 hour (and over 5
  minutes): 2% of the month's budget gone in an hour;
- open a ticket when it burns 1× too fast over 3 days.

This pages on what users feel, and not on blips.

## Canary releases 📘

`make deploy` is a rolling deploy: one new api starts, and then the old
one drains. All traffic moves to the new version within seconds. That
is safe for crashes and startup failures (readiness gates it), but not
for subtle regressions. Options, in order of effort:

1. **Deploy, watch, roll back.** Keep the dashboard open for 30
   minutes, then `make deploy tag=<previous>`. Rollbacks work across
   migrations (ADR-0015). This is the current practice.
2. **A canary on one host.** Run the new release as a second api service
   (`api-canary`), and weight nginx's upstream (for example 90/10 with
   `split_clients`). Compare its `llm_*` and `http_*` metrics with the
   old version's before shifting everything.
3. **On a platform:** weighted target groups (AWS ALB), revisions with
   traffic splitting (Cloud Run, Azure Container Apps), or Argo
   Rollouts on Kubernetes, with automatic analysis against the SLO
   metrics.

**Prompt and model changes get a canary of their own:**
- **Offline:** the eval suite (ADR-0016).
- **Shadow mode:** the new prompt answers real questions in the
  background, and only the old answer is shown. Compare the two
  offline, where privacy terms allow.
- **Per team:** only then turn it on for one team.

## Game days 📘

The drills (`make drills`) inject one fault at a time into a stack
nobody depends on ([failure modes](failure-modes.md)). A game day adds
the humans:
- **Schedule it.** Announce it, and have one person inject while the
  on-call engineer responds without knowing the scenario.
- **Scenarios worth rehearsing:**
  - the VM is gone: restore onto a new one from off-host backups, and
    time it;
  - the identity provider is down: signed-in users continue, new
    sign-ins fail; is the alert clear?
  - the model provider is down or rate limiting: chat errors, and
    everything else works;
  - the disk fills;
  - a bad release: roll back under load;
  - a leaked credential: rotate it, and see what breaks.
- **Record** for each:
  - the time to detect (did an alert fire, or did a person notice?);
  - the time to mitigate;
  - what users saw;
  - what the runbook got wrong.

  Fix the runbook the same day.

## Incidents 📘

- **Severity** is decided by user impact, not cause:
  - SEV1: nobody can use it;
  - SEV2: a core flow is broken for some;
  - SEV3: degraded, with a workaround.
- **Roles:** one incident lead, who decides and communicates, and the
  people doing the fixing. In a small team, one person may hold both;
  say so out loud.
- **Timeline:** write it as you go. The request id in every error
  (`make trace id=...`) and the dashboards make it reconstructible. For a
  harmful answer, the audit trail says what the model was given and who
  wrote it (`make audit`, [AI security](ai-security.md)).
- **A blameless review within a week, for every SEV1 and SEV2:**
  - what happened;
  - why the system allowed it;
  - what detection missed;
  - action items as issues, each with an owner.

  Most reviews should end in a new drill, an alert, or a gotcha line.

## Never tested: the honest list

Treat each of these as unknown until tested. Each one needs its own
run before the stage that depends on it.

| Not tested | Why it matters | How to test it |
|---|---|---|
| A cloud VM, a real domain, real Let's Encrypt certificates, and their renewal over months | the first renewal is 60 days in, when nobody is watching | stage 1; a certificate-expiry alert (an outside-in check) |
| The organisation's identity provider (Entra ID, Okta) | claims, group limits and token lifetimes differ from Keycloak's | stage 2; one real user per role |
| A hosted model provider: its latency, rate limits, outages, data terms | every chat number here is from a local GPU or the mock | stage 3: evals, a load test within quota, a provider-outage drill |
| macOS and Windows machines | the dev-environment chapter's advice for them is standard guidance, not observed | one developer on each platform through day one |
| Browsers other than Chromium | Playwright runs Chromium only | add the `firefox` and `webkit` projects to `tests/e2e/playwright.config.ts` |
| More than one host; managed Postgres; RDS Proxy; Kubernetes | pooling, failover and load balancer behaviour change | re-run `make drills` and `make load` on the new platform (environments chapter) |
| Real user traffic | capacity numbers come from synthetic load on one box | the pilot's metrics |
| Tracing at production volume, through a collector | tracing's cost was measured at 200 req/s on one process, straight to Jaeger | the pilot, sampled, through the platform's collector: watch p99 and `event_loop_lag_seconds` |
| Restoring production-sized data | restores were rehearsed on small dumps (seconds) | a restore of a full-size dump, timed |
| Months of operation | table bloat and vacuum, log and disk growth, a major Postgres upgrade (17 → 18) | a staging host kept running; an upgrade rehearsal |
| A third-party security review or penetration test | the attack table (security chapter) is self-assessed | before handling sensitive data |
| An accessibility audit | tests find controls by role, but nobody has audited with a screen reader | an audit, or axe in the e2e suite |
| Data retention and deletion (GDPR and similar) | alerts and sessions are kept forever today | a retention job, and a documented deletion path per user |
| Answer quality on real questions | the evals hold 19 hand-written cases | the pilot's reported bad answers become cases |
| Retrieval on real runbooks, with a hosted embedding model | recall was measured on 8 hand-written runbooks and 19 questions, with a local model | the pilot's runbooks and 30 labelled questions; `--target retrieval` with the provider's model |
