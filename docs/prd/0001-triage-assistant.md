# PRD-0001: Incident triage assistant

**Status:** shipped (v0.4.0, the minimum product); pilot not started
**Owner:** product owner for on-call tooling (to be named)
**Stakeholders:** on-call engineering teams; SRE and platform; security; compliance and legal (data sent to a model provider); finance (model costs)
**Date:** 2026-09-24

## Problem

On-call engineers get alerts from several systems at once: Prometheus,
Grafana, deploy tools, webhooks. During an incident they must work out
three things:
- which alert matters most;
- whether something changed just before it started;
- whether it belongs to their team.

Today they do it by scrolling channels and dashboards under time
pressure. The common mistakes:
- chasing a loud symptom while the critical alert sits lower down;
- missing that a deploy to the same service went out two minutes before
  the errors.

General-purpose AI chat tools cannot help safely.
- **Leaks:** using them means pasting alert text into them, which leaks
  other teams' incidents and any credentials that systems print into
  alerts.
- **No access control:** they know nothing about who may see what.
- **Invented causes:** they state causes that are not in the data.

**Why now:** models are good enough to read a list of alerts and answer
well, and the organisation wants AI adopted *with* controls.

**If we do nothing:**
- triage stays slow;
- engineers paste incident data into unapproved tools.

## Goals

1. An on-call engineer asks about current alerts in plain language and
   gets a grounded answer, streaming, within seconds.
2. **Nobody sees another team's alerts**, and neither does the model
   answering them.
3. The assistant answers only from the alerts, says so when they do not
   hold the answer, and cannot be steered by text planted in an alert.
   All three are **measured before every change**.
4. People sign in with the company identity provider; their teams and
   roles come from it.
5. The service is the **template for the next AI service**, so each new
   one does not re-solve sign-in, deployment and measurement.

## Non-goals

- **Taking actions.** The assistant has no tools: it cannot restart,
  roll back or run anything. Actions would need a separate proposal,
  with human confirmation for each.
- **Replacing paging.** Alertmanager and the pager keep waking people
  up. This is where they go next.
- **History and analytics:** postmortem writing, trend reports.
- **External customers:** it is internal only, with one organisation per
  deployment.
- **Fine-tuning a model.** The prompt and the context are the product.
- **A mobile app.**

## Success metrics

For the pilot (see [going to production](../handbook/production.md),
stage 4):

| Metric | Target | How it is measured |
|---|---|---|
| Adoption | the assistant is used in ≥ 60% of the pilot team's on-call shifts | sign-in and question counts per week (metrics, not content) |
| Usefulness | ≥ 70% of rated answers rated helpful | the answer-feedback feature (F6, not built yet) |
| Faster triage | the time from page to first correct hypothesis falls 25% against a baseline | incident timelines and game days; **measure the baseline first** |
| Isolation | 0 cross-team exposures | isolation evals every release; access tests in CI; audit of reports |
| AI safety | safety eval cases pass 100% of runs; the instruction leak rate's 95% upper bound stays under 1.5% (0 leaks in 200 runs shows it; each leak seen needs more runs) | `make evals` per prompt or model change (ADR-0016) |
| Reliability | 99.5% availability; time to first word p95 < 3 s | the SLOs ([production](../handbook/production.md)) |
| Cost | under a per-user monthly figure, set after the RFQ | tokens × price, on the cost panel |

## User stories

- As an **on-call engineer**, I want to ask "what should I look at
  first?" and get the most severe alert named first, so I start in the
  right place.
- As an **on-call engineer**, I want to be told when errors began
  shortly after a deploy to the same service, so I consider a rollback
  early.
- As an **on-call engineer**, I want a plain "the alerts do not say"
  when they don't, so I never chase an invented cause.
- As an **on-call engineer**, I want the steps from my team's runbook,
  with the section named, so I can act at once and check the source.
- As an **on-call engineer**, I want to stop a long answer, so I am not
  kept waiting for text I no longer need.
- As a **responder**, I want to add an alert by hand, so an issue seen
  outside monitoring is on the list too.
- As a **team admin**, I want our alerts visible only to our members, so
  our incidents stay ours.
- As an **org admin**, I want teams and roles to come from the company
  directory, so joiners and leavers need no step here.
- As a **security officer**, I want evidence that the assistant cannot
  see another team's alerts, and resists instructions hidden in alert
  text, so I can approve it.
- As a **platform engineer**, I want Alertmanager to deliver alerts by
  webhook to the right team, so no alert needs typing.
- As an **engineer starting a new AI service**, I want to copy this one
  and replace only the domain, so my first week is spent on my problem.

## Scope / requirements

**Functional:**

| # | Requirement | Status |
|---|---|---|
| F1 | Alert intake: an API, and an Alertmanager webhook routed by a `team` label | ✅ |
| F2 | Teams own alerts. Ranked roles per team: viewer < responder < admin; plus org admin | ✅ |
| F3 | Sign-in with the company's OIDC identity provider; sessions, sign-out, sign-out everywhere, revocation | ✅ |
| F4 | The alert list, newest first, across the teams the user can see | ✅ |
| F5 | The assistant: streamed answers from the asker's visible alerts only; stop at any time | ✅ |
| F6 | Answer feedback (helpful or not, with an optional note) | 📘 needed for the pilot |
| F7 | Answers that cite the team's runbooks | ✅ [RFC-0001](../rfc/0001-answers-grounded-in-runbooks.md), ADR-0017. Team admins upload runbooks through the api; a sync from the wiki 📘 |
| F8 | Retention: alerts and sessions deleted after an agreed period; a per-user deletion path | 📘 needs legal's period |

**Non-functional:**

| # | Requirement | Status |
|---|---|---|
| N1 | Availability 99.5% a month | 📘 measured once in production; one host is a single point of failure |
| N2 | Alert list p95 < 300 ms; time to first word p95 < 3 s | ✅ ~4 ms at 500 req/s; first word p50 0.28 s, p95 1.6 s with runbooks, with a local model |
| N3 | Access enforced in the service and in the database; invisible data answers 404 | ✅ ADR-0013, ADR-0014 |
| N4 | Prompt and model changes gated by evals | ✅ ADR-0016 |
| N5 | Alert and runbook text goes to a model provider (answers and embeddings) only under zero-data-retention terms, or to a model in the company's cloud. Credentials in them are redacted first (✅) | 📘 [RFQ-0001](../rfq/0001-llm-inference.md) |
| N6 | Every alert that can fire has a runbook section; dashboards and alert rules are code | ✅ |
| N7 | Usable with a keyboard and a screen reader | partial: tests find controls by role; no audit yet |
| N8 | Any answer can be explained afterwards: which prompt version, model and runbook sections produced it, and where its time went, without storing questions or answers | ✅ ADR-0018: a trace per answer, no content on it |

## Timeline / milestones

| Milestone | Status |
|---|---|
| v0.1.0: production-shaped stack, monitoring, failure drills, zero-downtime deploys | ✅ |
| v0.2.0: company sign-in; teams own alerts | ✅ |
| v0.3.0: the database enforces team isolation | ✅ |
| v0.4.0: evals gate AI changes; prompt v5 | ✅ |
| v0.5.0: answers cite the team's runbooks (F7); credentials redacted from the prompt | ✅ |
| Stages 1–3: a cloud host with HTTPS, the company identity provider, a hosted model under agreed terms | 📘 about 3 weeks of work, plus vendor and security lead times |
| Stage 4: a pilot team; answer feedback (F6) | 📘 2–4 weeks |
| Stage 5: general availability, team by team; a second host | 📘 after the pilot's review |

## Open questions

- **The model provider and its terms:** the RFQ's outcome (legal and
  security sign-off on data processing).
- **Retention:** how long to keep alerts and sessions (legal).
- **Answer feedback:** may it store the question text, or only the
  rating? This is a privacy decision.
- **On-call for the assistant:** who is paged when it is down? The
  platform team, or the tool's owners?
- **Budget:** the monthly model spend ceiling (finance).
