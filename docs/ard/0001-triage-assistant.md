# ARD-0001: triage-assistant

**Status:** in review
**Authors:** the service's engineers
**Reviewers:** architecture review board; security; platform
**Date:** 2026-09-24
**Related:** [PRD-0001](../prd/0001-triage-assistant.md), [ADR-0001 to ADR-0017](../adr/), [RFC-0001](../rfc/0001-answers-grounded-in-runbooks.md), [RFQ-0001](../rfq/0001-llm-inference.md)

## 1. Context and scope

An internal service for on-call engineers.
- **Intake:** alerts arrive from Alertmanager's webhook, or by hand.
- **Ownership:** teams own the alerts.
- **The assistant:** an AI model answers questions about the alerts the
  asker may see, streaming, and gives the steps from the asker's team
  runbooks, citing each section.
- **Sign-in:** the company's OIDC identity provider; roles come from its
  groups claim.

```
 on-call engineers ──HTTPS──► triage-assistant ──► model provider (OpenAI-compatible API: answers, embeddings)
 Alertmanager ─────webhook──►        │         ──► identity provider (OIDC)
                                     └──► its own Postgres (alerts, runbooks and their vectors, teams, users, sessions)
```

**Out of scope:**
- actions: the model has no tools;
- paging, which stays with Alertmanager;
- external customers;
- fine-tuned models.

## 2. Quality attributes

| Attribute | Target | Measured by | Evidence today |
|---|---|---|---|
| Availability | 99.5% a month | SLO on `http_requests_total` | 📘 one host; zero-downtime deploys measured (155,659 requests, 0 failed) |
| Latency | alert list p95 < 300 ms; time to first word p95 < 3 s | `http_request_duration_seconds`, `llm_time_to_first_token_seconds` | ✅ ~4 ms at 500 req/s; first word p50 0.28 s, p95 1.6 s with runbooks, with a local model |
| Capacity | a pilot team, then all teams | load tests (k6 and five other tools) | ✅ ~1,000 reads/s or 500 concurrent streams on 2 CPUs |
| Isolation | no cross-team visibility, for users or the model | tests at the api and at the database; isolation evals | ✅ 404 for invisible data; row-level security; evals 3/3 |
| AI quality and safety | safety cases 100%; quality ≥ 80%; a leak rate < 1.5% (95% bound) | `make evals` with a calibrated judge (ADR-0016) | ✅ prompt v6: every case 10/10 but one 9/10 (not significant); 2 leaks in 600 (bound 1.05%) |
| Recoverability | RPO ≤ 24 h; RTO < 1 h | daily dumps, restore rehearsals | ✅ restore in 6 s, healthy in 13 s (demo data); 📘 production-size data |
| Security | no known attack left without a tested control | the security chapter's attack table | ✅ 18 attacks, each with a control; 17 with a test (a leaked backup is covered by hashing, untested) |
| Operability | every alert has a runbook; dashboards and rules are code | `make obs-check` in CI | ✅ |

## 3. Architecture

One container stack, identical in every environment (ADR-0001,
ADR-0003):

| Container | Technology | Role | ADR |
|---|---|---|---|
| edge | Caddy | TLS, automatic certificates, security headers, holds requests while nginx restarts | 0012 |
| web | nginx | the built UI; proxies `/api` without buffering streams | 0007 |
| api | FastAPI, gunicorn and Uvicorn workers | sign-in, access checks, the alert API, the assistant | 0005, 0006, 0013 |
| pgbouncer | PgBouncer (transaction pooling) | many client connections over few server connections | 0005, 0010 |
| db | PostgreSQL 17 with pgvector | the data, with row-level security; runbook search (full text and vectors) | 0014, 0017 |
| redis | Valkey | rate-limit counters (disposable; fails open) | 0002, 0004 |
| monitoring | Prometheus, Alertmanager, Grafana, exporters | metrics, alerts, dashboards | 0008 |

**The request paths:**
- **sign-in:** OIDC authorization code with PKCE, the api as a
  confidential client;
- **reads:** session → principal → the visibility query;
- **the assistant:** visible alerts, and the visible runbook sections
  retrieved for the question → prompt (credentials redacted) → streamed
  answer, with its citations;
- **the webhook:** a bearer token → a row in the labelled team.

The guide's "system on one page" has the details
([gold_standard_development_guide.md](../../gold_standard_development_guide.md#the-system-on-one-page)).

## 4. Data

| Data | Classification | Where | Leaves the service? | Kept |
|---|---|---|---|---|
| Alert text | internal; may contain hostnames, and credentials that systems print | Postgres | **yes: to the model provider**, the asker's visible alerts only, known credential formats redacted | forever today (F8 📘) |
| Runbooks | internal; name systems and procedures, may contain credentials | Postgres, with one vector per section | **yes: to the model provider**: every section to the embedding model when saved; the sections retrieved for a question to the chat model. Known credential formats redacted | until a team admin deletes them |
| Questions and answers | internal | not stored; not logged | questions go to the model provider (chat and embeddings), known credential formats redacted | only for the answer's duration |
| Users: id, email, name, groups | personal data | Postgres | no | forever today (F8 📘) |
| Sessions | security-sensitive | Postgres, as a SHA-256 of the token only | no | 12 h absolute, 2 h idle |
| Logs | operational | stdout | to the log platform | the platform's retention |

- **Backups:** a daily `pg_dump` (the newest 14 are kept) and a daily
  disk snapshot (7 days), copied off the host. The recovery point is
  therefore ≤ 24 h. 📘 Point-in-time recovery (WAL archiving, or a
  managed database) would cut it to minutes.
- **Restore:** one transaction, all or nothing. It was rehearsed on a
  clean host (`DUMP=... make fresh-host-test`).
- **Data sent to the model provider** is the main data-protection
  question. The requirement is zero data retention and no training
  ([RFQ-0001](../rfq/0001-llm-inference.md)). The alternative is a
  model inside the company's cloud account.

## 5. Security

- **Identity:** the company's OIDC provider. The api is a confidential
  client:
  - PKCE;
  - `state` bound to the browser;
  - the ID token is fully checked (signature, `iss`, `aud`, `azp`,
    `exp`, `nonce`, the algorithm allow-list).

  No token ever reaches the browser. The session cookie is `HttpOnly`,
  `Secure` and `__Host-` prefixed (ADR-0013).
- **Authorization:**
  - ranked roles per team, from the provider's groups claim;
  - invisible data is a 404;
  - the same rule is enforced again by Postgres row-level security,
    fed per transaction (ADR-0014).
- **CSRF:** `Origin` must equal the public URL on every state-changing
  request; `SameSite=Lax` is a second layer.
- **Secrets:**
  - they come from the environment only;
  - generated at bootstrap;
  - secret scanning with push protection;
  - none in images or logs.
- **Exposure:**
  - only the edge is published (443, and 80 for redirects and
    certificates);
  - everything else is on the internal network or loopback;
  - every container is non-root, with a read-only root filesystem, no
    capabilities, and CPU and memory limits.
- **Threat model:** 18 attacks, each mapped to a control, and 17 to a
  test ([security](../handbook/security.md#each-attack-and-what-stops-it)).
  It is self-assessed: an external review is an open condition.

## 6. AI components

| Question | Answer |
|---|---|
| Model | any OpenAI-compatible endpoint (ADR-0006); measured with gpt-oss:20b (local); production: the RFQ's outcome |
| Embedding model | the same endpoint (ADR-0017); measured with nomic-embed-text (local). Changing it needs `make reembed` |
| What reaches it | the fixed instructions; up to 20 of the asker's visible alerts (300 characters each); up to 4 sections of the asker's visible runbooks; the question. Known credential formats are redacted from all of it |
| What it can do | produce text. **No tools**, no actions, no access to anything else |
| How output is shown | as text, never HTML or markdown execution |
| How quality is measured | 19 versioned eval cases (grounding, refusal, injection, isolation, runbooks). A judge from another model family, calibrated on 34 labelled answers (102 of 102 verdicts agree). A retrieval benchmark (recall@k, MRR). A gate before every prompt or model change, with a statistical regression test (ADR-0016) |
| Prompt injection | alert and runbook text are untrusted. Injection cases are safety-gated (100%). v6 leaked its instructions 2 times in 600 (95% bound 1.05%); v5, 0 in 200; v4, 5 in 200. The prompt holds no secrets; the architecture bounds the impact |
| Failure behaviour | provider errors, timeouts, rate limits and empty or cut-off answers each reach the user as a specific message. The rest of the service is unaffected. Each is measured (`llm_requests_total{outcome}`) |
| Cost control | per-user rate limits, an output limit, a stream time cap, a cost panel |

## 7. Operations

- **Deploy:** by release tag, rolling, readiness-gated. 0 failed
  requests of 155,659 during a deploy. **Rollback** is the previous tag,
  safe across expand/contract migrations (ADR-0011, ADR-0015).
- **Monitoring:** RED metrics, event-loop lag, pools, and model outcomes
  and cost. The alert rules have unit tests and a runbook section each.
- **Failure modes:**
  - 22 drills, each with what users saw
    ([failure modes](../handbook/failure-modes.md));
  - the single points of failure on one host are listed there, with
    what removes each.
- **On call:** to be named (PRD open question). Alertmanager's receiver
  must point at a pager before the pilot.

## 8. Compliance

- **Licences:** the service's own dependencies and images are permissive
  (MIT, BSD, Apache 2.0, ISC, the PostgreSQL licence). Two exceptions to
  note:
  - **Grafana** is AGPL-3.0. Running it unmodified, internally, creates
    no obligation. Modifying it and offering it to others over a network
    would.
  - **The eval judge gemma3** is under Google's Gemma terms of use, not
    an open-source licence. It is used only as an offline grader here;
    review its terms before any production use.

  Development tools are not shipped: k6 (AGPL-3.0), and Docker Desktop
  (paid for larger companies).
- **Data protection:**
  - personal data is limited to the user's id, email and name;
  - the model provider's data processing needs a DPA and zero retention;
  - retention periods are open (F8).

## 9. Risks

| Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|
| The model gives a wrong or manipulated answer during an incident | medium | medium | the eval gate; answers are advice, with no tools; "the alerts do not say" is measured | service owners |
| Alert or runbook text containing a credential reaches the provider | medium | high | zero-retention terms; known secret formats redacted before any model call (✅); an unknown format passes | service owners + security |
| Runbook search silently degrades (the embedding model changed or down) | medium | medium | keyword search answers alone; the embedding key makes stale vectors visible; `RetrievalDegraded` alert | service owners |
| A runbook gives wrong or dangerous steps, and the assistant repeats them | medium | high | every step cites its section, with its date; runbooks are the teams' own; the evals check the steps against the runbook | teams + service owners |
| The single host fails | low | high (the tool is down, paging is unaffected) | daily off-host backups; the rebuild is scripted; a second host at stage 5 | platform |
| Model cost grows with adoption | medium | medium | rate limits; a cost panel; a budget alarm; RFQ pricing | product owner + finance |
| The provider changes the model behind a name | medium | medium | pin dated versions; re-run the evals on change notices | service owners |
| Template copies drift from the platform rules | medium | medium | `scripts/new-project.sh`, the documented rules, ADRs for deviations | engineering leads |

## 10. Decisions and deviations

- **Decisions:** ADR-0001 to ADR-0017.
- **Deviations from common standards:**
  - **No Kubernetes.** Compose on a VM is enough at the measured
    capacity; the platform mapping is documented (infrastructure Q&A).
  - **An in-house eval harness** rather than a framework (ADR-0016).
  - **A bundled identity provider in development only.** Production
    uses the company's.

## 11. Open issues

- The model provider and its terms (RFQ-0001).
- Retention periods (F8).
- On-call ownership, and the pager receiver.
- An external security review.
- An outside-in uptime check.

## 12. Review outcome

Proposed: **approved with conditions**, for a pilot team only. Before
the pilot starts:
1. Stages 1–3 of [going to production](../handbook/production.md) are
   done, each with its exit evidence.
2. A model provider is contracted under zero-data-retention terms, and
   its eval baseline is committed.
3. Alertmanager routes to a pager with a named on-call owner.
4. Retention periods are agreed and implemented (F8), or explicitly
   deferred by legal.

Before general availability: an external security review, and a second
host or a managed platform.
