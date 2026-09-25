# ADR-0023: Production is safe by default, and refuses unsafe settings

**Status:** accepted. Supersedes ADR-0004 for one scope: the chat's rate
limit fails closed in production (every other route still fails open).
**Date:** 2026-09-25

## Context

The defaults suited a laptop: the API docs on, every request traced, and a
chat that kept answering, without limits, when Valkey was down. Making
them right for production was a checklist in the security chapter. So was
connecting the organisation's identity provider, replacing the mock model,
setting a real address and real secrets, and keeping question text off
traces. A checklist is read once, by whoever deploys first; a copy of this
template on a new team's server would start with every one of those
mistakes, and say nothing.

## Decision

- **Defaults by `APP_ENV`.** With `APP_ENV=prod`: `DOCS_ENABLED` false,
  traces sampled at a tenth (`parentbased_traceidratio`, 0.1), and
  `CHAT_RATE_LIMIT_FAIL_CLOSED` true. Set explicitly, any of them wins, in
  any environment. The defaults live in `app/config.py`, not in compose,
  so they hold on any platform (ECS, Kubernetes) that runs the image.
- **Refusal at startup.** In production the settings are checked, and the
  process does not start when one fails: `localhost_url`,
  `insecure_cookies`, `mock_model`, `demo_identity_provider`,
  `example_secret`, `trace_content`. The error names each check and how to
  fix it. It never prints the values: settings errors now hide their
  input, which also used to print the database password on a malformed
  URL.
- **Waivers by name.** A deployment that means one of them lists it in
  `PROD_CHECKS_WAIVED`; the api logs every waiver at startup, and a
  misspelled name is refused (it would waive nothing). `.env.example`
  waives `localhost_url`, `mock_model` and `demo_identity_provider`: the
  production-shaped stack on a laptop and in CI runs with those on
  purpose. Never the secrets, cookies or trace content.
- **Secrets generated.** `make setup` and `make .env` create `.env` with a
  random value for every `change-me` one; CI and the release smoke test
  use them.
- **The chat fails closed in production.** Each question is a model call:
  an unlimited chat is an unlimited bill, while an unlimited alert list
  costs nothing. Refused questions answer 503 `rate_limiter_unavailable`
  before any model call, and page through a new alert,
  `RateLimiterFailingClosed` (the existing `RateLimiterFailingOpen` never
  fires on chat-only traffic).

## Alternatives considered

- **A separate environment for the production-shaped stack** (`APP_ENV=
  prodlike`). The e2e tests would then stop exercising production's own
  defaults. Named waivers keep the production code path and say exactly
  what differs.
- **One switch (`ALLOW_UNSAFE=true`).** It would waive the secrets check
  with the demo ones; a stale switch on a server would hide all six.
- **Checks in `scripts/deploy.sh`.** Only a VM runs it. The api is the one
  thing every platform runs.
- **Keep fail-open for the chat too** (ADR-0004's reasoning:
  availability first). Right for reads; for the one route that spends
  money per request, availability without limits is the incident.

## Consequences

- A clone whose `.env` still has `change-me` secrets cannot `make prod-up`
  until it generates them (`make .env` on a new file). The production
  stack's database was initialised with the old passwords: recreate its
  volume (`docker compose -p triage-assistant-prod down --volumes`).
- A demo server on the mock model waives `mock_model` and
  `demo_identity_provider` in its `.env`, visibly (the VM runbook).
- The security chapter's checklist keeps only what code cannot check: a
  data-processing agreement, a DPIA, backups off the host, paging, a
  penetration test.
- With Valkey down in production, users cannot ask the assistant until it
  is back, or until `CHAT_RATE_LIMIT_FAIL_CLOSED=false` is deployed.
