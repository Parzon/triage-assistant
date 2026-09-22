# ADR-0008: Metrics with Prometheus, dashboards and alerts as code, alerts delivered to the app

**Status:** accepted
**Date:** 2026-09-22

## Context

Logs answer "what happened in this request"; they do not answer "is the
service healthy, how slow is it for users, what is it costing". An AI
service adds signals a generic web service lacks: time to first token,
answer duration, tokens (cost), and answers abandoned mid-stream.

The api runs several gunicorn workers per container. Each is a separate
process with its own memory; measured on the production image with four
workers, a plain Prometheus registry reported the same counter as
15, 15, 5, 6, 14, 5 across six scrapes after 40 requests — each scrape
answered by a random worker.

## Decision

- **Instrumentation** (`app/metrics.py`): RED metrics per route template
  (`http_requests_total`, `http_request_duration_seconds`,
  `http_requests_in_progress`), LLM metrics (`llm_requests_total` by
  outcome incl. `cancelled`, `llm_time_to_first_token_seconds`,
  `llm_stream_duration_seconds`, `llm_tokens_total`,
  `llm_active_streams`), and `ratelimit_decisions_total` (incl.
  `fail_open`). Durations are measured in a pure ASGI middleware, so a
  stream's duration is the whole stream.
- **prometheus_client multiprocess mode** in the production image
  (`PROMETHEUS_MULTIPROC_DIR` on the tmpfs, emptied by gunicorn's
  `on_starting`, dead workers' gauges dropped in `child_exit`).
- **One compose profile, `observability`**: Prometheus (DNS discovery of
  every api replica, 15 d / 2 GB retention), Alertmanager, Grafana
  (datasource and dashboard provisioned from files), postgres/pgbouncer/
  valkey exporters, node-exporter, cAdvisor (filtered to this project).
- **Dashboards and alert rules live in git** and are validated in CI:
  `promtool check config`, `promtool test rules` (unit tests that feed
  synthetic series and assert when alerts fire), `amtool check-config`,
  JSON validity of dashboards.
- **Alerts are delivered to the triage assistant itself** through an
  authenticated, idempotent Alertmanager webhook — the product's own use
  case, and a working end-to-end loop (Redis stopped → alert in the app
  in about two minutes).

## Alternatives considered

- **OpenTelemetry metrics + collector.** The direction many platforms
  are heading, and traces are the next step for this service; for
  metrics alone, prometheus_client is simpler, and OTel collectors
  scrape Prometheus endpoints anyway.
- **uvicorn with one worker per container, no multiprocess mode.**
  Right on Kubernetes/ECS (scale with replicas); on a single VM it
  leaves cores idle.
- **A hosted APM (Datadog, New Relic, Grafana Cloud).** Less to run,
  recurring cost, data leaves the network; the instrumentation above
  exports to them unchanged.

## Consequences

- No per-process CPU/memory/GC metrics from the api in multiprocess mode;
  cAdvisor supplies container resources instead.
- The multiprocess directory grows with every recycled worker PID
  (counters of dead workers must be kept); deploys and restarts reset it.
- Alerts about the api itself (`ApiDown`, `ApiMissing`) cannot be
  delivered by the api: production routes them to a pager or chat.
- Detection latency is a sum of settings — scrape interval, rule `for:`,
  evaluation interval, Alertmanager `group_wait` — and is documented
  next to the alert rules.
