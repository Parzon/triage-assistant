Closes #

## What and why

<!-- One paragraph: the change, and the reason for it. -->

## How I verified it

<!-- Commands and numbers, not "tested locally". E.g. `make check`,
     `make e2e`, `make drills d="db-freeze"`, a before/after latency. -->

## Checklist

- [ ] `make check` passes locally (the same checks CI runs)
- [ ] Tests cover the failure paths, not only the happy path (dependency down / hung, invalid input, forbidden)
- [ ] Browser- or nginx-visible change: `make prod-up && make e2e`
- [ ] Database path, pools or timeouts touched: the database drills (`make drills d="db-freeze db-hang pgbouncer-freeze"`)
- [ ] Migration: backward compatible (expand now, contract in a later release), `lock_timeout`, concurrent indexes
- [ ] New metric labels are bounded; new alerts have a promtool test
- [ ] New dependency: one line on why, and why not the standard library or an existing one
- [ ] Docs: the handbook chapter, an ADR for a decision, a runbook for a procedure, the gotcha list for a trap
- [ ] No secrets, personal data or real customer data in code, tests, fixtures or logs
