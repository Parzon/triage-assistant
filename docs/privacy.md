# Privacy: what this service holds about people

For whoever answers a data protection officer, a lawyer or a person
asking what is held about them. It lists what the service keeps, where,
why, who can read it, for how long, and how it is deleted. ✅ = built and
tested here; 📘 = decided outside engineering, with the questions to ask.

The operator commands below run on the production host (add `ENV=prod`)
and are audited as the CLI. The retention job and erasure are dry runs
unless told otherwise (ADR-0025).

## What it holds

| Data | Where | Why | Who can read it | Kept | Deleted by |
|---|---|---|---|---|---|
| **Accounts:** name, email, the identity provider's issuer and subject, the org admin flag, first and last sign-in | Postgres, `users` | who is signed in; who wrote what | the person (`/me`); org admins, through the audit trail | until `USER_RETENTION_DAYS` (365) without a sign-in | `make retention`; `make user-forget` |
| **Team memberships** and roles | `memberships` | access control | team admins (their team's), org admins | replaced from the identity provider at each sign-in | with the account |
| **Sessions:** a token's hash, the ID token (for sign-out), times | `sessions` | staying signed in | nobody, through the api | 12 hours at most; expired ones deleted | sign-out, `make revoke`, `make retention`, with the account |
| **Sign-ins in progress** | `login_requests` | the sign-in flow | nobody | 10 minutes | each sign-in; `make retention` |
| **Alerts:** free text, which can name people or customers | `alerts` | what the assistant answers about | the owning team's members; org admins | `ALERT_RETENTION_DAYS` (365) | `make retention`; team admins |
| **Runbooks,** their sections and embeddings | `runbooks`, `runbook_chunks` | the steps the assistant cites | the owning team's members | until a team admin deletes them | team admins |
| **Questions and answers** | **not stored** | | | | |
| **The audit trail:** who did what, by user id; what each question was given, by ids and hashes; never text | `audit_events` | accountability: who wrote what the model reads, what it was given | org admins | until pruned | the schema owner only: `make audit-prune days=N` |
| **The off switch:** the reason it is off, who switched it (user id) | `assistant_switch` | tell askers why | everyone signed in (the reason) | until the next switch | the next switch |
| **Access logs:** user id (api), client IP address (nginx), request id, path, status | container logs | debugging | whoever has the host | 3 files of 10 MB per container | log rotation |
| **Traces:** user id, ids, counts, timings; no content (production refuses `TRACE_CONTENT`) | Jaeger, in memory | where a request's time went | whoever can open Jaeger (bound to 127.0.0.1) | at most 20,000 traces; gone on restart | rotation |
| **Rate-limit counters:** user id or IP address in the key | Valkey | limits | nobody | 60 seconds | expiry |
| **Backups:** every database row above | `backups/` on the host | recovery | whoever has the host | the newest 14 dumps | rotation (`KEEP`) |
| **Metrics** | Prometheus | | no personal data: labels are bounded | 15 days | |

## What leaves the service

- **To the model provider:** each question, with the asker's visible
  alerts and runbook sections. Credentials are redacted first
  (`app/redact.py`); names, customers and anything else in the text are
  not. From then on the provider's terms apply.
- **To the identity provider:** the sign-in. It is the source of truth for
  accounts and teams.
- **To telemetry backends,** when configured: traces (ids, never content)
  and logs.

## A person's rights ✅

- **Access** (GDPR art. 15): `make user-export email=alice@example.com`
  prints, as JSON, every account with that email (one per identity
  provider): teams, sessions (times only; never a token), audit events,
  and a list of what is held elsewhere. Recorded as `user.exported`.
- **Erasure** (art. 17): `make user-forget email=alice@example.com` shows
  what would go; with `yes=1` it erases the accounts, their memberships
  and sessions, and records `user.forgotten`. Then:
  - remove them at the identity provider too, or their next sign-in
    creates them again;
  - the audit trail keeps their events. They name a user id that no
    longer resolves to anyone: pseudonymous, not anonymous. Whether they
    may stay (art. 17(3): legal claims, security) is legal's decision 📘.
    Write it down; `make audit-prune` removes them by age;
  - backups still hold the rows until they rotate out (14 dumps).
- **Rectification:** name and email come from the identity provider at
  each sign-in. Correct them there.

## Retention ✅

`make retention` shows what is past its retention; `make retention
apply=1` deletes it:
- expired sessions and sign-ins in progress;
- users who have not signed in for `USER_RETENTION_DAYS`;
- alerts older than `ALERT_RETENTION_DAYS`.

The defaults (365 days each) are placeholders for your policy. An empty
value keeps that kind for ever. The audit trail is pruned apart, by its
owner. Schedule both on the host ([the VM runbook](runbooks/demo-vm.md),
section 7):

```
17 3 * * * cd /srv/triage-assistant && make retention apply=1 ENV=prod
27 3 * * * cd /srv/triage-assistant && make audit-prune days=400 ENV=prod
```

## Decided outside engineering 📘

Questions to settle before real users, with the privacy officer, legal and
HR:
- **The legal basis** for each use: operating the service, and the audit
  trail's record of what each employee asked.
- **A data-processing agreement with the model provider:** no training on
  your data, how long it keeps prompts, where it processes them. On AWS
  Bedrock, check whether the model is served through a cross-region or
  global inference profile: a global one can process EU prompts outside
  the EU.
- **A data protection impact assessment** (art. 35) before launch.
- **The works council.** The audit trail can show what an employee asked
  and when: in the Netherlands, a system able to monitor staff typically
  needs the works council's consent (WOR art. 27). Ask early: it takes
  time.
- **Breach notification within 72 hours** (art. 33). The audit trail
  and access control are in place; knowing in time needs paging, which is
  not wired yet (Alertmanager routes to the app only).
- **The EU AI Act.** Since 2 August 2026 people must be told they are
  dealing with an AI system (art. 50): the chat says so above the
  question box. Whether a use case is high-risk (the obligations for
  those apply from December 2027) is a per-product assessment.
