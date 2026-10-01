# ADR-0026: Report third-party image findings; bump base images weekly

**Status:** accepted
**Date:** 2026-10-01
**Supersedes:** in part, ADR-0022 (the weekly scan of the compose files'
images, and how base-image fixes arrive)

## Context

ADR-0022 made the weekly scan of the compose files' third-party images
fail on a fixable HIGH or CRITICAL finding, expecting each to be bumped or
accepted. Its first run (2026-09-28) found 336, in 12 of the 14 images,
none of which this repo builds (Keycloak, Grafana, Ollama, pgvector, the
exporters); most are fixed in libraries those projects have not yet
released with. Nothing the repo could do but wait, so the run would be
red every Monday: an email nobody acts on.

Dependabot went from weekly to monthly on 2026-09-30, to cut the number of
PRs. Base images are where OS-level fixes arrive, and security updates do
not cover them: a rebuilt base is the fix for an entry in
`.trivyignore.yaml`. Monthly, a rebuild could wait weeks for its PR while
the entry's expiry turned CI red.

## Decision

- **The compose images' scan reports and does not fail** (the job
  continues on error). Its log is read when those images are bumped.
- **This repo's own images still fail on a fixable finding**, on every PR,
  before a release, and weekly from main: ADR-0022 unchanged.
- **Dependabot proposes base-image updates weekly**; everything else stays
  monthly.

## Alternatives considered

- **Accept every finding in `.trivyignore.yaml`.** Hundreds of entries,
  each needing a reason and an expiry, for code the repo cannot change.
- **Fail only on CRITICAL.** Still red every week (Keycloak, pgvector).
- **Drop the compose scan.** A team that runs these images still needs to
  know what is in them before it upgrades.

## Consequences

- A vulnerable third-party image no longer turns anything red: only a
  person reading the report notices it.
- The api image's acceptances expire on 2026-10-14; the weekly base-image
  PR should arrive before then.
