# The TLS edge: Caddy in front of nginx (compose.prod.yaml, service `edge`,
# ADR-0012). Hardened like every other container here:
# - it runs as a non-root user. Binding 80 and 443 inside its own network
#   namespace is allowed by the net.ipv4.ip_unprivileged_port_start sysctl
#   that compose sets - not by a capability;
# - the binary's file capability is removed: under no-new-privileges the
#   kernel refuses to run a binary that asks for capabilities it cannot get;
# - /data (certificates, the ACME account key) and /config belong to that
#   user, so the named volumes created from them do too.
#
# The base image is pinned by digest as well as tag (Dependabot moves both).
FROM caddy:2.11.4-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b
RUN apk add --no-cache libcap-setcap \
 && setcap -r /usr/bin/caddy \
 && apk del libcap-setcap \
 && addgroup -S -g 10001 edge \
 && adduser -S -D -H -u 10001 -G edge edge \
 && chown -R edge:edge /data /config
COPY Caddyfile /etc/caddy/Caddyfile
USER edge
