# ADR-0002: Valkey instead of Redis for the rate-limit store

**Status:** accepted
**Date:** 2026-09-22

## Context

The stack used `redis:7-alpine`, which today resolves to Redis 7.4.11.
Redis changed licence from BSD-3 to RSALv2/SSPLv1 with 7.4 (2024) and
added AGPLv3 as a third option with 8.0 (2025). None of those is a
problem for simply running Redis inside an application, but source-
available and AGPL licences are routinely blocked by enterprise legal
review, and the choice was made by accident (a floating tag), not
deliberately.

Valkey is the Linux Foundation fork of Redis 7.2.4, BSD-3 licensed,
wire-compatible, and the engine AWS ElastiCache and MemoryDB now default
to — which is where this service's cache lands when infra takes over.

## Decision

Use `valkey/valkey:8.1-alpine`. The compose service stays named `redis`
and the env var stays `REDIS_URL`: the name describes the protocol and the
role, and the client library (`redis-py`) is unchanged. The image ships
`redis-cli`/`redis-server` symlinks, so existing commands keep working.

## Alternatives considered

- **Redis 8 (AGPLv3 option).** Fine technically; adds a licence review for
  every consumer of the template.
- **Pin Redis 7.2.x (last BSD release).** Frozen, receives no fixes.
- **KeyDB / Dragonfly.** Compatible, but smaller communities and not the
  managed default on AWS.

## Consequences

No application change. Anyone reading "Redis" in docs or code should read
"the Redis protocol". If a project ever needs a Redis-8-only feature, that
project writes a new ADR superseding this one.
