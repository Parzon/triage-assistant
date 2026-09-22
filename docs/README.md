# docs/

Four document types, each with a different job and a different lifetime.
Copy the `TEMPLATE.md` in the relevant folder to start one.

| Type | Question it answers | Audience | Lifetime |
|---|---|---|---|
| **PRD** (`prd/`) | What are we building, for whom, and why? | Stakeholders, PM, eng | Living until shipped, then archived |
| **RFC** (`rfc/`) | Should we do this, and roughly how? | Eng team, reviewers | Living during review, then archived |
| **Design Doc** (`design-docs/`) | Given we're doing it, exactly how — architecture, data model, API contracts? | Eng team | Living during build, then archived |
| **ADR** (`adr/`) | What did we decide, and why? | Anyone inheriting this repo later, incl. infra | Permanent, never edited after merge — superseded by a new ADR instead |

Rough flow for a real feature: **PRD** (what/why) → **RFC** (should we, alternatives) → **Design Doc** (the actual technical plan) → build it → **ADR** for any decision made along the way that would confuse someone later if unexplained ("why Postgres and not Mongo," "why we dropped Next.js").

Not every change needs all four — a one-line bug fix needs none of them. Use judgment: if you'd have to explain a decision in Slack more than once, it should have been an ADR.

## AI coding agents

Agent instructions live at the **repo root** (`AGENTS.md`, `CLAUDE.md`)
by convention, not here — that's where Claude Code, Codex, and other
tools look for them. `AGENTS.md` points back into this folder so an
agent picking up a task knows these doc types exist and when to
create one.
