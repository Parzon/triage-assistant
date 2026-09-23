# ADR-0012: HTTPS terminates at a Caddy edge in front of nginx

**Status:** accepted
**Date:** 2026-09-23

## Context

The production-shaped stack spoke plain HTTP. Nothing real can run like
that:
- Session cookies must be `Secure`.
- Passwords and tokens cross the network.
- Browsers mark HTTP pages "not secure".

Every deployment needs TLS. How it's terminated depends on where the
stack runs: a single VM has nobody else to do it, while a cloud load
balancer does it with a managed certificate.

## Decision

1. **A Caddy edge (`tools/edge`) is part of the production shape**:
   compose profile `edge`, on by default. It is the only published
   service:
   - HTTPS on 443, and HTTP on 80 redirecting to HTTPS;
   - HTTP/3 advertised;
   - nginx drops to the host's loopback, for drills, load tests and
     debugging.
2. **Certificates are automatic:**
   - `SITE_ADDRESS` set to a domain whose DNS points at the host:
     Let's Encrypt over ACME, renewed by Caddy, stored in the `edge-data`
     volume.
   - `localhost` (the default): Caddy's local CA, with 12-hour leaf
     certificates it renews itself.
   - `ACME_CA` selects another ACME directory: Let's Encrypt staging for
     a first setup, Pebble for tests.
3. **Behind a cloud load balancer, the edge is left out:** drop `edge`
   from `COMPOSE_PROFILES` and publish nginx (`HTTP_BIND=0.0.0.0`,
   `HTTP_PORT=80`). Same images, no overlay file.
4. **Hardened like every other container:**
   - It runs as a non-root user; binding 80/443 inside its own network
     namespace comes from the `net.ipv4.ip_unprivileged_port_start`
     sysctl, not from a capability.
   - `cap_drop: ALL`, `no-new-privileges`, a read-only root filesystem.
   - The official image's file capability on the binary is removed: under
     `no-new-privileges` the kernel refuses to run a binary that asks for
     capabilities it cannot get.
5. **Real client addresses survive both proxies:**
   - Caddy overwrites whatever `X-Forwarded-For` a client sends.
   - nginx trusts `X-Forwarded-For` only from private ranges (`realip`).
   - The api sees the real client, so rate limits stay per user.
6. **The edge absorbs nginx restarts:**
   - `lb_try_duration 5s` retries a request that could not reach nginx,
     which is what closes the last gap of a rolling deploy.
   - When nginx stays down, the edge answers in the api's JSON error
     format.
7. **HSTS is 0 by default** and set to a year only for a real domain.
   HSTS on `localhost` would force HTTPS on every local port, the Vite dev
   server included.

## Alternatives considered

- **nginx + certbot.** Two more moving parts: a renewal job, and a reload
  hook that must run after every renewal. Caddy renews without either.
- **TLS only at a cloud load balancer.** That's right in the cloud, and
  the edge profile turns off for it. But a single VM, a demo or a pilot
  would then have no HTTPS at all.
- **The official Caddy image as is.** It runs as root, and its binary's
  file capability conflicts with `no-new-privileges`.

## Consequences

Measured:
- **TLS:** TLS 1.3 and HTTP/2, with the chain verified against the local
  CA.
- **Streaming:** SSE stays incremental through edge → nginx → api (50
  events over 1.3 s, first token at 0.34 s).
- **Client addresses:** a forged `X-Forwarded-For` never reaches the api.
- **Browser tests:** all 5 pass over HTTPS.
- **Automatic certificates:** `make acme-test`:
  - the hardened edge image obtains a certificate over ACME HTTP-01 from
    Pebble (Let's Encrypt's test CA) in ~8 s;
  - it serves the certificate with a verified chain;
  - it doesn't ask again after a restart.
- **Deploys:** a rolling deploy through the edge failed zero requests in
  three runs. The worst case was one request held 2.0 s during the nginx
  swap.

Costs:
- The edge is a new single point of failure on a single VM. Stopping it
  refuses every connection, just as stopping nginx did before.
- Replacing the edge drops connections for a moment. `make deploy` does
  it only when the edge image's content changed.
- With host ports remapped (8081/8443 on a box where 80 is taken),
  Caddy's HTTP redirect and its `alt-svc` header name 443. On a VM using
  80/443 they are right.
- Let's Encrypt itself is not exercised here: that needs a public domain
  and open ports. `make acme-test` covers the protocol and the image;
  DNS and reachability are the part left.
