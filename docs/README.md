# docs/

- **`handbook/`**: how this service is built, tested, debugged, shipped
  and operated, with the measurements behind every choice. New here:
  [`../START_HERE.md`](../START_HERE.md) first; every chapter, and when to
  read it, is in [the guide](../gold_standard_development_guide.md#the-handbook).
- **[`code-map.md`](code-map.md)**: one line per file, saying what it is
  for.
- **`runbooks/`**: procedures for pressure: [alerts](runbooks/alerts.md)
  (one section per alert, linked from each rule), [one VM](runbooks/demo-vm.md)
  (from the request to IT to teardown), [turning the assistant
  off](runbooks/turn-the-assistant-off.md) (when it is the incident).
- **`images/`**: the dashboard screenshot the chapters show.
- **[`overview.md`](overview.md)**: the project on one page, for anyone
  deciding about it.
- **[`privacy.md`](privacy.md)**: the personal data the service holds:
  where, why, who reads it, how long, how it is exported and erased.

Plus three document types for deciding what to build. Each has a
different job and a different lifetime. Copy the `TEMPLATE.md` in the
relevant folder to start one; the `0001-*` file beside each template is
this project's own, filled in.

| Type | Question it answers | Audience | Lifetime |
|---|---|---|---|
| **PRD** (`prd/`) | What are we building, for whom, and why? | Stakeholders, PM, eng | Living until shipped, then archived |
| **RFC** (`rfc/`) | Should we do this, and roughly how? | Eng team, reviewers | Living during review, then archived |
| **ADR** (`adr/`) | What did we decide, and why? | Anyone inheriting this repo later, incl. infra | Permanent, never edited after merge — superseded by a new ADR instead |

Rough flow for a real feature: **PRD** (what/why) → **RFC** (should we, alternatives, and the technical plan) → build it → **ADR** for any decision made along the way that would confuse someone later if unexplained ("why Postgres and not Mongo," "why we dropped Next.js").

Not every change needs these — a one-line bug fix needs none of them. Use judgment: if you'd have to explain a decision in Slack more than once, it should have been an ADR.

## AI coding agents

Agent instructions live at the **repo root** (`AGENTS.md`, `CLAUDE.md`)
by convention, not here — that's where Claude Code, Codex, and other
tools look for them. `AGENTS.md` points back into this folder so an
agent picking up a task knows these doc types exist and when to
create one.
