# The TLS edge: Caddy in front of nginx (compose.prod.yaml, service `edge`,
# ADR-0012). Hardened like every other container here:
# - it runs as a non-root user. Binding 80 and 443 inside its own network
#   namespace is allowed by the net.ipv4.ip_unprivileged_port_start sysctl
#   that compose sets - not by a capability;
# - the binary carries no file capability (XCADDY_SETCAP=0; the builder
#   image turns it on): under no-new-privileges the kernel refuses to run a
#   binary that asks for capabilities it cannot get;
# - /data (certificates, the ACME account key) and /config belong to that
#   user, so the named volumes created from them do too.
#
# Caddy is built here from its release tag with the builder image's Go,
# rather than taken from the runtime image: a Go security fix reaches the
# builder image days before Caddy publishes a release built with it
# (CVE-2026-111943, crypto/x509, fixed in Go 1.26.10). Keep both tags on the
# same Caddy version. Both images are pinned by digest as well as tag
# (Dependabot moves both).
FROM caddy:2.11.8-builder-alpine@sha256:e42a64b3bc70aa3a1387bde85b930e507a378fecae5852d5bfde9dcd37d0b999 AS build
RUN XCADDY_SETCAP=0 xcaddy build v2.11.7 --output /usr/bin/caddy

FROM caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee
COPY --from=build /usr/bin/caddy /usr/bin/caddy
RUN addgroup -S -g 10001 edge \
 && adduser -S -D -H -u 10001 -G edge edge \
 && chown -R edge:edge /data /config
COPY Caddyfile /etc/caddy/Caddyfile
USER edge
