# ADR-0022: Scan images and commits; pin base images and scanners by digest

**Status:** accepted
**Date:** 2026-09-25

## Context

Lockfiles, SHA-pinned Actions and Dependabot kept dependencies current,
but nothing said whether what shipped had a known vulnerability, or
whether a commit carried a secret. Base images were pinned by tag, which
can be moved to other content. The first scan of the images found 20
fixable HIGH vulnerabilities, none of them in code this repo wrote.

Scanners are supply chain too: in March 2026 Trivy's own releases
(0.69.4 to 0.69.6) and 76 of the 77 tags of its GitHub Action were
replaced by code that stole CI credentials (CVE-2026-33634).

## Decision

- **Trivy** (`make scan`) scans the three production images and the web's
  runtime npm dependencies on every PR, before a release publishes
  anything, and weekly from main. `make scan-compose` scans the compose
  files' third-party images weekly (`.github/workflows/scan.yml`).
- **Policy:** a HIGH or CRITICAL finding with a fixed version blocks.
  When nothing can be moved to yet, the risk is accepted in
  `.trivyignore.yaml`, one entry per finding, with a reason and an
  expiry at most 90 days out; `scripts/check_trivyignore.py` refuses
  either missing, and the code owners approve the file's changes.
- **gitleaks** (`make secrets-scan`) scans every commit on every PR,
  through its CLI (its GitHub Action needs a licence for organisations).
- **Digests:** every Dockerfile's base image is pinned by tag and digest;
  Dependabot moves both. Compose images stay pinned by tag, move by
  Dependabot's docker-compose updates (minor and patch), and are scanned
  weekly.
- **The scanners run from images pinned by digest**, from the Makefile
  like every other tool, never as third-party Actions. The image is
  saved to a file for Trivy: the scanner gets no Docker socket.

## Alternatives considered

- **Grype, or Docker Scout.** Grype is comparable; Trivy also scans
  lockfiles, secrets in images and misconfiguration, with one database
  mirror (mirror.gcr.io) that avoids Docker Hub's limits. Scout is tied
  to a Docker account.
- **Block on every severity, or on unfixed findings too.** Unfixed
  findings leave nothing to do but wait; blocking on them teaches people
  to ignore the check.
- **`apk upgrade` / `apt-get upgrade` in the Dockerfiles** to pick up OS
  fixes before the base image is rebuilt. Builds would then depend on the
  day they ran; the digest bump does the same, reviewed.
- **Trivy's GitHub Action.** Its tags were the attack vector in March
  2026; a digest-pinned image run from the Makefile is the same command
  locally and in CI.

## Consequences

- A new vulnerability in a base image can turn CI red on a PR that did
  not cause it. The fix is usually Dependabot's digest bump; otherwise an
  entry in `.trivyignore.yaml`, which expires and forces the question
  again.
- The first scan changed the images: pip is gone from the production api
  image (`make image-check` refuses it), and the web image uses nginx's
  `-slim` variant, without optional modules or curl.
- Accepted until 2026-10-25: 17 findings in the Caddy binary of the TLS
  edge, fixed upstream but in no Caddy release yet. If none arrives, the
  edge is built with xcaddy on a patched Go.
- Weekly runs of `scan-compose` will go red on third-party images the
  team does not build (Keycloak, Grafana, cAdvisor): each is a bump or an
  accepted risk, decided by whoever owns the policy.
