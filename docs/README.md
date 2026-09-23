# docs/

- **`handbook/`**: how this service is built, tested, debugged, shipped
  and operated, with the measurements behind every choice. Start from the
  map in [`../gold_standard_development_guide.md`](../gold_standard_development_guide.md).
- **`runbooks/`**: procedures for pressure: [alerts](runbooks/alerts.md)
  (one section per alert, linked from each rule), [one VM](runbooks/demo-vm.md)
  (from the request to IT to teardown).
- **`images/`**: the dashboard screenshot and the profiling flame graph
  the chapters show.
- **[`overview.md`](overview.md)**: the project on one page, for anyone
  deciding about it.

Plus six document types for deciding what to build, and what to buy.
Each has a different job and a different lifetime. Copy the
`TEMPLATE.md` in the relevant folder to start one; the `0001-*` file
beside each template is this project's own, filled in.

| Type | Question it answers | Audience | Lifetime |
|---|---|---|---|
| **PRD** (`prd/`) | What are we building, for whom, and why? | Stakeholders, PM, eng | Living until shipped, then archived |
| **ARD** (`ard/`) | Is the whole system's architecture fit to go live? (Architecture Review Document; some organisations say Architecture Requirements Document) | Architecture board, security, platform | Per review: before going live, before a major change |
| **RFC** (`rfc/`) | Should we do this, and roughly how? | Eng team, reviewers | Living during review, then archived |
| **Design Doc** (`design-docs/`) | Given we're doing it, exactly how — architecture, data model, API contracts? | Eng team | Living during build, then archived |
| **ADR** (`adr/`) | What did we decide, and why? | Anyone inheriting this repo later, incl. infra | Permanent, never edited after merge — superseded by a new ADR instead |
| **RFQ** (`rfq/`) | What exactly are we buying, and what will it cost? (Request for Quotation, sent to vendors) | Vendors, procurement, legal, finance | Until the contract is signed |

Rough flow for a real feature: **PRD** (what/why) → **RFC** (should we, alternatives) → **Design Doc** (the actual technical plan) → build it → **ADR** for any decision made along the way that would confuse someone later if unexplained ("why Postgres and not Mongo," "why we dropped Next.js"). Before going live: an **ARD** for the review board. When the plan needs something bought (a model provider, hosting): an **RFQ**.

Not every change needs these — a one-line bug fix needs none of them. Use judgment: if you'd have to explain a decision in Slack more than once, it should have been an ADR.

## AI coding agents

Agent instructions live at the **repo root** (`AGENTS.md`, `CLAUDE.md`)
by convention, not here — that's where Claude Code, Codex, and other
tools look for them. `AGENTS.md` points back into this folder so an
agent picking up a task knows these doc types exist and when to
create one.
