# ADR-0014: Postgres enforces team isolation (row-level security)

**Status:** accepted
**Date:** 2026-09-23

## Context

Since ADR-0013 the app filters every read by the caller's teams and
checks every write's role. That is one line of code per query. The next
endpoint, a refactor, or a reporting query could forget it, and the
result would not be an error: another team's alerts, silently, in a
response or in the assistant's prompt.

Every read and write passes through the database. Enforcing the rule
there turns a forgotten filter from a leak into an empty result.

## Decision

1. **Row-level security on `alerts`**, one policy per command
   (migration `ce83ff21ab01`):

   | Command | Allowed when |
   |---|---|
   | SELECT | org admin, the Alertmanager service, or the row's team is in `app.read_team_ids` |
   | INSERT | org admin, the service, or the team is in `app.write_team_ids` (responder and above) |
   | DELETE | org admin, or the team is in `app.admin_team_ids` |
   | UPDATE | never: no policy (the app does not update alerts) |

2. **The policies read what the app says about the caller.** In every
   transaction, the app runs `set_config(..., true)`: transaction-local,
   so through PgBouncer's transaction pooling it can never reach the next
   client (ADR-0013). A session-level `SET` would.
3. **No context, no rows.** A query that does not say who is asking sees
   nothing and changes nothing.
4. **The app role is subject to the policies; the owner is not.**
   Migrations, `make seed` and backups run as the owner. No `FORCE ROW
   LEVEL SECURITY`: the owner can disable RLS anyway, so forcing it would
   only complicate backups.
5. **Settings are read once per query.** Each policy reads its setting
   through a scalar subquery, which Postgres evaluates once (an
   InitPlan). Called directly, it would be evaluated for every row.
6. **Expand, then contract, in separate releases.**
   - v0.2.0 (expand) started sending the context, and gave
     `alerts.team_id` a default so v0.1.0 could keep inserting during
     its rollout.
   - v0.3.0 (contract) enables the policies and drops the default.
   - Run against v0.1.0, the contract migration would hide every alert
     from it: releases cannot be skipped.
7. **Only `alerts` holds team data.** `teams`, `users`, `memberships`,
   `sessions` and `login_requests` have no policies. Authentication must
   read a session before it knows who is asking, and those tables are
   reached only through `app/sessions.py`.

## Alternatives considered

- **Filtering in the app only** (v0.2.0). One forgotten filter leaks, and
  nothing notices.
- **A schema or a database per team.** Isolation is stronger, but an org
  admin's view becomes a union across N schemas. Every migration runs N
  times, and pools multiply. Right for separate customers who must never
  share a database (hostile multi-tenancy). Heavy for teams inside one
  organisation.
- **A Postgres role per team, `SET LOCAL ROLE` per transaction.** Roles
  multiply with the teams, and the database then has to mirror the
  identity provider's groups. The settings carry the same information
  without new roles.
- **Security-barrier views per team.** More objects, the same session
  variables, and easy to bypass by querying the table.

## Consequences

Measured:
- **Correctness.** Nine database-level tests (`test_row_level_security.py`)
  bypass the app and query as its role:
  - nothing is visible without context;
  - a viewer sees only their team, even when the app asks for every
    team;
  - an insert into a team without the write role is refused by Postgres;
  - a responder's DELETE and an org admin's UPDATE affect 0 rows;
  - an empty setting means no access, not an error.

  The other 190 tests pass unchanged under the policies.
- **Cost.**
  - A page of a two-team read still uses the team index: 0.53 ms, with
    the policy as a filter on the 100 rows fetched.
  - A count over 1 M rows (666 k visible): 44 ms with the settings read
    once, against 100 ms read per row.

Costs and rules:
- **Anyone querying as the app role must set the context first.**
  Otherwise every count is 0. The owner (`make psql`) is not affected.
  - In a `psql` session connected *directly* to Postgres as the app
    role: `SELECT set_config('app.org_admin', 'on', false)`.
  - Never session-level through PgBouncer: the next client on that
    server connection would inherit it.
  - The test suite's data reset declares itself an org admin, in its
    transaction, for the same reason.
- **Reporting or analytics connections** need their own role with
  `BYPASSRLS`, or an explicit org-admin context: a visible, reviewable
  choice.
- **A new table with team data needs its policies and its tests in the
  same migration** (the daily-work chapter has the recipe).
- **A refusal by Postgres surfaces as a 500.** The app's own checks
  answer first (404, 403); reaching a policy means the app has a bug,
  and the traceback names the policy.
