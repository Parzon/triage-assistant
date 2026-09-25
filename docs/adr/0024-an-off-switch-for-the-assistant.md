# ADR-0024: An off switch for the assistant

**Status:** accepted
**Date:** 2026-09-25

## Context

Some incidents are the assistant itself: the model starts giving harmful
advice (a provider-side model update, a poisoned runbook, a prompt change
that slipped past the evals), the provider has a breach or an outage, or
the bill runs away. Stopping it meant a deploy with a changed setting
(`LLM_*`), under pressure, by someone with host access, while the rest of
the service must keep working: alerts and runbooks are what people use
during the same incident.

## Decision

- **A flag in Postgres, not a setting:** one row, `assistant_switch`,
  read on every question. It flips in seconds, on every worker and replica
  at once, with no deploy and no restart. Not cached: an "off" must never
  be stale on one worker.
- **Who:** org admins, through `PUT /api/assistant` (the UI shows them the
  switch), with a reason; operators with the host, through `make
  assistant off="..."`, which needs neither the identity provider nor a
  session. Row-level security repeats the rule in the database: everyone
  reads the row, only an org admin's transaction changes it, nobody adds
  or deletes it.
- **What off means:** `/chat/stream` answers 503 `assistant_disabled`,
  with the reason and since when, before anything that calls a model (the
  question's embedding included), in both chat modes. The UI says why and
  disables the question box. Alerts, runbooks and the MCP tools keep
  working.
- **On the record:** every flip is an audit event, `assistant.disabled` or
  `assistant.enabled`, with who, when, and the hash of the reason (the
  audit trail holds no text). Refused questions are not `chat.asked`: no
  model was given anything.
- **Not an error:** refusals count in `chat_refusals_total{reason=
  "assistant_disabled"}`, and `HighErrorRate` subtracts them, so a
  deliberate off pages nobody.

## Alternatives considered

- **An environment variable** (`ASSISTANT_ENABLED=false`): a deploy per
  flip, and a window where replicas disagree.
- **A feature-flag service** (LaunchDarkly, Unleash, AWS AppConfig): the
  right tool once a platform has one. It adds a dependency the service must
  reach on every question, for one boolean.
- **Per team** (switch off one team's assistant, for a poisoned runbook):
  a question reads every team the asker belongs to, so "off for team A"
  means refusing a user in teams A and B, or answering without A's data,
  silently. Deferred until someone needs it; the table can take a team
  column and a row per team.
- **Also stopping embeddings** (runbook saves and searches call the
  embedding model): the switch is about answers. When no text may reach
  the provider at all, also unset `EMBEDDING_MODEL` (a deploy): runbook
  routes then answer 503 `runbooks_off`.

## Consequences

- One more query per question: a primary-key read, in the same
  transaction as the session check.
- The same mechanism can later pin a model or a prompt version (a column
  on the row, read at the same point) to roll back without a deploy.
- A runbook for using it: [docs/runbooks/turn-the-assistant-off.md](../runbooks/turn-the-assistant-off.md).
