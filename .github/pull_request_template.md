Closes #

## What and why

<!-- One paragraph: the change, and the reason for it. -->

## How I verified it

<!-- Commands and numbers, not "tested locally". E.g. `make check`,
     `make e2e`, `make drills d="db-freeze"`, a before/after latency.
     Anything you couldn't run, and why. -->

## Written with

<!-- The agent (Claude Code, Copilot, Codex, Cursor, none) and the skills it
     used. The kit's owner reads these each quarter (docs/agent-playbook.md,
     "Govern it"). -->

## Checklist

- [ ] `make check` passes locally (lint, types, tests; CI adds the scans, image checks and e2e)
- [ ] Tests cover the failure paths, not only the happy path (dependency down / hung, invalid input, forbidden)
- [ ] Another agent reviewed it in two passes, spec and standards, one area at a time (`two-pass-review`, `AGENTS.md` "Reviewing a change"); every finding fixed or answered here
- [ ] Browser- or nginx-visible change: `make prod-up && make e2e`
- [ ] Database path, pools or timeouts touched: the database drills (`make drills d="db-freeze db-hang pgbouncer-freeze"`)
- [ ] Migration: backward compatible (expand now, contract in a later release), `lock_timeout`, concurrent indexes
- [ ] New metric labels are bounded; new alerts have a promtool test
- [ ] New dependency: one line on why, and why not the standard library or an existing one
- [ ] Docs: the handbook chapter, an ADR for a decision, a runbook for a procedure, the gotcha list for a trap
- [ ] No secrets, personal data or real customer data in code, tests, fixtures or logs
