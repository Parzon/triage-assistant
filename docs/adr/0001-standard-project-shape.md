# ADR-0001: Standard shape for AI-idea-to-production projects

**Status:** accepted
**Date:** 2026-09-22

## Context

This team's actual job is proving AI agent capabilities, not running
infrastructure. Projects that prove out get handed to infra engineers
to scale/deploy (AWS). For that handoff to be fast and repeatable
across many different AI projects — not just this one — the parts of
a repo that are *not* the specific AI logic should look identical
project to project. An infra engineer inheriting project #7 from this
team should already know exactly where everything is, with zero
ramp-up on layout, regardless of what the AI logic actually does.

## Decision

Every project this team builds follows this shape:

```
apps/api/        FastAPI backend. AI/agent-specific logic lives in its
                  own clearly-named module so infra never has to touch
                  it to deploy/scale the service around it.
apps/web/         React + Vite + TypeScript frontend, same structure
                  every time.
infra/            Terraform/deployment config — intentionally empty
                  until a project graduates past prototype; infra owns
                  this directory once they take over.
docs/adr/         Decisions, one file each, never edited after merge.
docker-compose.yml   Full local stack. Same service-naming convention
                  every project (api, web, db, redis, ...).
.github/workflows/ci.yml   Same lint -> build -> test shape every time.
AGENTS.md / CLAUDE.md      Same structure every time — only the
                  "what this project does" section changes.
```

Config is environment-variable-driven everywhere (12-factor), so infra
can swap the secrets source (local `.env` -> AWS Secrets Manager)
without touching any code.

**Streaming is treated as scaffold, not project-specific logic.** The
mechanism for streaming a response from backend to frontend (FastAPI
`StreamingResponse` -> `fetch` + `ReadableStream` on the client) is
built once, correctly, here — the same wiring works whether the
generator behind it is a placeholder or a real LLM call. Swapping in
real model output later should never require touching the streaming
plumbing itself.

## Alternatives considered

- **Streamlit/Gradio for the frontend.** Faster to build a pure demo,
  Python-only, built-in streaming. Rejected: doesn't compose into a
  real deployable artifact — infra can't take a Streamlit app and
  "scale it," it has to be rewritten. Right tool for a one-off demo,
  wrong tool for a template other projects inherit.
- **Next.js instead of plain React+Vite.** Rejected: buys SSR/SEO/file
  routing this project doesn't need (internal tool, not a public site)
  — complexity with no corresponding benefit, the exact mistake the
  complexity-dial principle warns against.

## Consequences

An AI engineer on this team starts a new project by copying this
scaffold and only touching `apps/api`'s business logic and `apps/web`'s
UI. Handoff to infra means handing over a repo shape they've already
seen, not a new thing to learn.
