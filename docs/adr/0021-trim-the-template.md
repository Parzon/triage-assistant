# ADR-0021: Trim the template to what a project uses

**Status:** accepted — supersedes the tools and make targets named in
ADR-0009, ADR-0011 and ADR-0012 (their measurements stand)
**Date:** 2026-09-24

## Context

The repository is a template other projects copy, and the material new
engineers study. It had grown tools that answered one question once:
five load-test tools beside k6, deep-debug targets, two host rehearsals,
and six document types. Each costs reading time, maintenance and
dependency updates, in every copy.

## Decision

Removed, with their make targets, scripts, images and docs:
- **Load testing:** Locust, JMeter, vegeta, oha, Artillery and `make
  load-compare`. k6 stays (`make load`). Supersedes the tool list in
  ADR-0009; its measurements and defaults stand.
- **Deep debugging:** py-spy and the flame graph, netshoot, tcpdump,
  strace, and the memory-growth script. The findings stay in the
  performance and debugging chapters.
- **Host rehearsals:** `make acme-test` (Pebble) and `make
  fresh-host-test` (Docker-in-Docker), and the devcontainer configs.
  Supersedes those targets in ADR-0011 and ADR-0012; what the rehearsals
  showed stays recorded there.
- **Document types:** the RFQ, ARD and design-doc folders. The PRD, the
  RFC (with RFC-0001 as the example) and the ADRs stay.

Gotchas now live in one list, in the guide: the chapters link to it.

## Alternatives considered

- **Keep everything, documented.** Every copy of the template would carry
  it, and every new engineer would read it.

## Consequences

- Git history keeps every removed file: a project that needs one restores
  it from there.
- A rehearsal that is gone is no longer re-run on each change. The ACME
  exchange and a restore onto a clean host are worth rehearsing again
  before the first real launch ([the production chapter](../handbook/production.md),
  stage 1).
