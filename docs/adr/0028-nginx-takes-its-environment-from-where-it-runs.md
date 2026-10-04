# ADR-0028: nginx takes its trusted proxies and resolver from where it runs

**Status:** accepted
**Date:** 2026-10-04

## Context

The web image's nginx config fixed two things that depend on the platform:

- `set_real_ip_from` for every private range (10/8, 172.16/12, 192.168/16).
  With `real_ip_recursive on`, nginx skips trusted addresses from the right
  of `X-Forwarded-For`. Behind a load balancer that *appends* to the header,
  a client whose own address is private (a company network, a VPN, another
  service) was itself trusted and skipped, so the left-most value, which the
  client wrote, became its address - and its rate-limit key. Safe behind the
  TLS edge, which overwrites the header; not in general (ADR-0012 assumed
  the edge).
- `resolver 127.0.0.11`, Docker's embedded DNS, and `server api:8010`.
  Kubernetes has neither that resolver nor a bare `api` name (nginx's
  resolver ignores search domains), so the image could not run there
  unchanged.

## Decision

A start-up script in the image (`/docker-entrypoint.d/25-runtime-conf.sh`)
writes three files into `/tmp/nginx` (the root filesystem is read-only), and
`default.conf` includes them:

- `resolver`: the nameservers in `/etc/resolv.conf`.
- `set_real_ip_from`: `REAL_IP_FROM` (space-separated CIDRs) when set;
  otherwise the container's own networks, where the TLS edge runs.
- the `api_backend` upstream: `API_UPSTREAM`, default `api:8010`.

## Alternatives considered

- **A fixed subnet for an edge-only network**, trusted by CIDR: exact, but
  every host needs a subnet that collides with nothing, and the edge's
  config changes with it.
- **nginx's `envsubst` templates**: they render whole files, and every `$`
  in the config is nginx's own variable; a script writing three small files
  is easier to read and to test.

## Consequences

- Measured on the built image: `REAL_IP_FROM` outside the client's network,
  a forged `X-Forwarded-For: 6.6.6.6` is ignored (nginx logs the real
  address); inside it, believed. On Docker's default bridge with another
  nameserver, the resolver follows `/etc/resolv.conf`.
- Behind a cloud load balancer, `REAL_IP_FROM` must be set to its subnets
  (the ECS stack sets the VPC range); left empty, nginx sees every request
  as coming from the load balancer, which is safe but shares one rate-limit
  key. `.env.example` says so.
- CI's nginx config test runs the script first.
