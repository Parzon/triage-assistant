# ADR-0011: Releases are version tags that publish images; one host deploys by rolling

**Status:** accepted
**Date:** 2026-09-23

## Context

The stack has to reach machines other than laptops: a demo or staging VM
now, and a managed platform later. Three questions needed answers:

- **Where do images come from?** Building on the target host makes every
  host a build machine. It then needs Docker Hub, PyPI and npm, and two
  hosts can end up running different bytes under the same version.
- **What does a deploy do to users?** The failure drills measured plain
  `docker compose up -d` on one host: it stops the old api first. That
  meant 502s for 6.7 s, and for as long as the longest streamed answer
  still in flight (up to 2 minutes).
- **How do you know a host can be rebuilt from nothing?** Until now, only
  CI's fresh runner came close, and a runner has far more installed than
  a new VM.

## Decision

1. **A release is a `vX.Y.Z` tag on a commit on main.**
   `.github/workflows/release.yml` checks the tag is on main, runs the
   image checks, and builds both production images. It publishes them to
   GHCR as `X.Y.Z`, `X.Y` and `sha-<commit>`, with build provenance and an
   SBOM, and creates the GitHub release notes. The same build runs without
   pushing on any PR that changes the workflow or a Dockerfile. Hosts pull
   images; they don't build them. Building on the host (`make prod-up`)
   stays available for a demo without registry access.
2. **One host deploys by rolling.** `make deploy tag=X.Y.Z`:
   - runs migrations;
   - starts the new api next to the old one and waits for it to be
     healthy and resolved by nginx;
   - drains the old one;
   - replaces nginx last.

   If the new api is not healthy within 90 s, it's removed, and the old
   one never stopped serving. The tag is written to `.env`, so every
   later compose command runs the same images.
3. **Migrations are backward compatible** (expand, then contract), because
   the old api serves while they run. Rollback is deploying the previous
   tag.
4. **Rebuilding from nothing is tested.** `make fresh-host-test` puts the
   committed tree on a clean Docker-in-Docker host with only
   `.env.example`, brings the stack up, and checks every user path. Given
   `DUMP=`, it also restores a backup there.

## Alternatives considered

- **Docker Hub or a cloud registry (ECR, ACR).** GHCR needs no extra
  account: the workflow's `GITHUB_TOKEN` can push, and packages link to
  the repository. A cloud registry is the natural move once the platform
  is chosen; only `IMAGE_PREFIX` changes.
- **`latest` tags.** Unversioned: two pulls of `latest` can be different
  code, and a rollback has no name to roll back to.
- **Build on the host from a git ref.** It needs build tooling and
  internet access on every host, and produces different bytes per host.
- **Docker Swarm, or `docker rollout`** (a plugin doing what
  `deploy.sh` does). Swarm is a new orchestrator for one host. The plugin
  is one more thing to install on every host. The script is 90 lines,
  and its behaviour is measured by the `rollout` drill.
- **A second nginx behind a load balancer** removes the last gap (~0.3 s
  of refused connections while nginx itself is replaced). On one VM there
  is nothing in front to balance; in the cloud that load balancer exists
  anyway.

## Consequences

Measured (issue #14):
- A plain recreate refused new requests for 6.7 s. `make deploy`: zero
  failed or slow requests during the api swap, with every in-flight
  stream finished, and one ~0.3 s gap while nginx is replaced. A broken
  release was rolled back automatically with zero failed requests.
- A clean host went from checkout to serving in 41 s (`make
  fresh-host-test`). A 2 M-row dump restored onto it in 11 s.

Costs:
- The api's container name changes with each rolling deploy (`api-2`,
  `api-3`, ...). Tooling looks the container up through compose
  (`docker compose ps -q api`) and never hardcodes a name.
- Until the first tag is pushed, GHCR publishing is verified only by the
  PR build and by actionlint.
- Packages start private: a host needs `docker login ghcr.io` with a
  `read:packages` token, or the package made public.
