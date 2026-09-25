# ADR-0025: Personal data: retention, export and erasure

**Status:** accepted
**Date:** 2026-09-25

## Context

The service kept everything for ever: users who left, alerts from years
ago, expired sessions (deleted only when someone else signed in). GDPR
asks for a stated retention for each kind of personal data (art. 5(1)(e)),
and for a way to answer a person who asks what is held about them
(art. 15) or asks to be erased (art. 17). The audit trail (ADR-0019) is
append-only by design: the api cannot delete from it.

## Decision

- **An inventory:** `docs/privacy.md` lists what is held, where, why, who
  reads it, for how long, and how it goes. It changes with any new table,
  log field or telemetry attribute that holds personal data.
- **Retention as a job, not a trigger:** `make retention` (`python -m
  app.cli retention`) deletes expired sessions and sign-ins, users with no
  sign-in for `USER_RETENTION_DAYS`, alerts older than
  `ALERT_RETENTION_DAYS`. Scheduled by the host (cron) or the platform (a
  scheduled task), beside `make backup` and `make audit-prune`. The
  defaults (365 days) are placeholders; empty keeps for ever.
- **Dry runs by default:** the retention job and erasure run their deletes
  in one transaction and commit only with `--apply` / `--yes`, so the dry
  run reports exactly what the real one would do.
- **Export:** `make user-export email=...` prints every account with that
  email, as JSON: account, teams, session times, audit events, and a list
  of what is held elsewhere (logs, traces, backups, the providers). Never
  a session's token hash or ID token: credentials, not information.
- **Erasure:** `make user-forget email=... yes=1` deletes the accounts,
  and with them memberships and sessions. The audit trail keeps its
  events, which from then on name an id that resolves to nobody.
- **Every one is audited** as the CLI: `retention.applied` (counts),
  `user.exported`, `user.forgotten` (the id, and how many events were
  kept).

## Alternatives considered

- **Deleting audit events on erasure.** The audit trail is the record of
  who wrote what the model read; an erasable one could be erased by the
  person it would incriminate. Keeping it pseudonymous, pruned by age, is
  engineering's proposal; whether it may stay is legal's call (art.
  17(3)), and `docs/privacy.md` says so.
- **Retention inside the api** (a background task in each worker): every
  worker and replica would race to delete, and a deploy would change when
  it runs. One scheduled command is easier to see and to stop.
- **Soft deletes** (a `deleted_at` column): the data would still be there,
  for every query to remember to filter out.

## Consequences

- An inactive user who signs in again is created again from the identity
  provider, with their current teams: retention loses nothing they need.
- Erasure is incomplete until the identity provider removes the person and
  the backups rotate out (14 dumps): both said in the command's output and
  in `docs/privacy.md`.
- A new table or field with personal data needs a row in the inventory,
  and a decision on its retention.
