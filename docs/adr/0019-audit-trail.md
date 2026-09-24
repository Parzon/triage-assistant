# ADR-0019: An append-only audit trail of what the assistant reads, and who wrote it

**Status:** accepted
**Date:** 2026-09-24

## Context

The assistant's answers are built from text other people write: alerts
(any responder, and Alertmanager), runbooks (team admins). A wrong or
planted step in a runbook reaches every answer that retrieves it, and the
answer reads with full authority.

After such an answer, two questions: **what was the model given, and who
wrote it?** Nothing answered the second. Runbooks and alerts stored no
author, and access logs name a route, not the runbook written. Traces
(ADR-0018) answer the first, but for a sample, for days, and without the
author.

## Decision

1. **One table, `audit_events`**, one row per:
   - `runbook.saved`, `runbook.deleted`, `alert.created` (by a user or
     the webhook), `alert.deleted`: who wrote the text the model reads,
     and its hash;
   - `chat.asked`: who asked, the prompt's version and hash, the model,
     the alert ids and runbook sections in the context, each with its
     runbook's version hash.
2. **Ids and hashes, never text.** No question, answer, alert or runbook
   text, as on spans and in logs. A section's version hash leads to the
   save that wrote it.
3. **Written in the transaction of the change.** A change whose audit
   write fails does not happen. A chat's event is committed before the
   answer starts: no answer without its record.
4. **Append-only for the api.** The migration revokes `UPDATE`, `DELETE`
   and `TRUNCATE` from every non-owner role. Row-level security lets org
   admins read, and lets a row name as its actor only the user the
   transaction acts for.
5. **Read by org admins** (`GET /audit`) and operators (`make audit`).
   **Pruned by the schema owner** (`make audit-prune days=N`), on the
   organisation's retention policy.

## Alternatives considered

- **Author columns on `runbooks` and `alerts`** (`updated_by`). This answers
  "who wrote the current version" only: a replaced version, and a deleted
  runbook, lose their author, and nothing records what a chat was given.
- **Audit events as log lines.** Logs are kept for days, sit in a store
  with its own access rules, and anyone with shell access can edit them.
  An event also cannot share the transaction of the change it records.
- **Traces as the record.** Traces are meant to be sampled, are kept for
  days, and an unsampled request has none.
- **Storing questions and answers** would answer "what did the assistant
  say", at the price of one more copy of the most sensitive text the
  service handles, with its own retention and deletion duties. Deferred:
  an organisation that must keep answers adds a store for them on purpose.
- **Tamper-evidence (a hash chain, or a copy in write-once storage).**
  Only the database owner can rewrite the table now. Guarding against the
  owner too means a copy outside the database. It is the next step if a
  regulator asks for it.

## Consequences

- An incident review can go from a complaint to the author of the step,
  and to everyone who was given it, in two queries
  (`labs/ai-security`, exercise 2).
- Every chat adds one INSERT to the transaction that reads its context:
  measured on the dev stack, through PgBouncer, 0.5 ms at the median
  (0.66 → 1.15 ms) and 0.6 ms at p95, over 500 transactions each. Every
  audited write adds one INSERT.
- Audit rows are inserted without `RETURNING`: Postgres checks a returned
  row against the SELECT policy, which only org admins pass. `db.add()`
  of an `AuditEvent` fails; `app/audit.py`'s `record()` is the way in.
- Tests cannot delete audit rows between runs. Each test finds its own by
  action and target, or by a user it created.
- The table grows with use (one row per question). Retention is an
  operator's decision, not the api's.
- A new way to change what the assistant reads (a wiki sync, a tool) must
  record its event in the same transaction.
