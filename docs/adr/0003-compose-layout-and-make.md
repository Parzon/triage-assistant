# ADR-0003: One base compose file, two environment overlays, one Makefile

**Status:** accepted
**Date:** 2026-09-22

## Context

A single `docker-compose.yml` held the dev setup (bind mounts, dev
targets, ports on every interface). The production shape existed only as
two `docker build --target production` commands in a doc, so it was never
run as a whole — and the production-only bugs found in the 2026-09-22
audit (nginx buffering the SSE stream, a global rate limit behind the
proxy) lived exactly there.

## Decision

- `compose.yaml` — what every environment shares: services, images or
  build contexts, env contract, healthchecks, `depends_on`, restart and
  log-rotation policy. No ports, no mounts.
- `compose.override.yaml` — development. Compose merges it automatically
  into any plain `docker compose ...` command: dev build targets, source
  bind mounts, ports on `127.0.0.1`.
- `compose.prod.yaml` — production shape, used explicitly
  (`-f compose.yaml -f compose.prod.yaml`): production targets, image
  names/tags, only nginx published, read-only root filesystems,
  `cap_drop: [ALL]`, `no-new-privileges`, CPU/memory limits.
- `Makefile` — the single entry point for commands; each target is a
  visible one-line wrapper around the real `docker compose` call.

## Alternatives considered

- **Two complete files (dev, prod).** No merge rules to learn, but the
  shared 80% drifts apart silently.
- **Profiles in one file.** Profiles switch services on/off; they cannot
  change a service's build target, mounts or ports per environment.
- **A task runner (just, Taskfile) instead of make.** Nicer syntax, one
  more thing to install on every laptop; make is already everywhere.

## Consequences

Compose merge rules apply (verified with `docker compose config`):
mappings such as `environment` merge key by key (the overlay wins), while
`ports` lists are concatenated — an overlay adding `9010:8010` to a base
with `8010:8010` publishes both. Removing an inherited entry needs the
`!reset` YAML tag (Compose 2.24+). The base therefore declares no ports
at all. When in doubt, `make config` / `make config ENV=prod` prints the
merged result.
