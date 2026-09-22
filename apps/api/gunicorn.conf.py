"""Gunicorn settings for the production image.

Every value can be overridden by an environment variable so the same
image runs correctly on a laptop, a single VM, or one-process-per-task
platforms (ECS, Kubernetes) without a rebuild.
"""

import os
import shutil
from pathlib import Path

from app.logs import logging_config


def available_cpus(cpu_max: Path = Path("/sys/fs/cgroup/cpu.max")) -> int:
    """CPUs this container may actually use.

    os.cpu_count() reports the host's cores even inside a CPU-limited
    container. A `--cpus` / compose `cpus:` / ECS / Kubernetes CPU limit is
    the cgroup v2 quota in cpu.max: "<quota> <period>", or "max <period>"
    when unlimited. sched_getaffinity() covers `--cpuset-cpus`.
    """
    try:
        quota, period = cpu_max.read_text().split()
        if quota != "max":
            return max(1, -(-int(quota) // int(period)))  # ceiling division
    except (OSError, ValueError):
        pass
    return len(os.sched_getaffinity(0))


bind = f"0.0.0.0:{os.environ.get('PORT', '8010')}"

# Async workers: one per usable CPU. Each worker's event loop already
# serves many concurrent connections; the classic "2 x cores + 1" rule is
# for sync workers that block while waiting on I/O. On ECS/Kubernetes set
# WEB_CONCURRENCY=1 and scale with replicas instead.
workers = int(os.environ.get("WEB_CONCURRENCY") or available_cpus())
worker_class = "uvicorn_worker.UvicornWorker"

# A heartbeat, not a request deadline: an async worker is killed only when
# its event loop is blocked this long (a stuck synchronous call). Long SSE
# streams are unaffected.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))

# On SIGTERM, in-flight requests get this long to finish. It must cover the
# longest LLM stream (LLM_STREAM_TIMEOUT_S) and stay below the container
# stop grace period (compose stop_grace_period, ECS stopTimeout, k8s
# terminationGracePeriodSeconds), which otherwise SIGKILLs first.
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "120"))

# Idle keep-alive must outlive the proxy's upstream keep-alive (nginx
# keepalive_timeout 60s; an AWS ALB idle timeout likewise). Otherwise the
# proxy reuses a connection the worker has just closed: sporadic 502s.
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "75"))

# Worker recycling after N requests is a memory-leak mitigation, and it has
# a cost: a recycling worker closes the idle keep-alive connections nginx
# holds to it, and nginx does not retry a POST on another connection - the
# load test saw bursts of 502 ("Connection reset by peer" in nginx's log)
# exactly when uvicorn logged "Maximum request limit ... exceeded". Off by
# default; set GUNICORN_MAX_REQUESTS only if memory is measured to grow.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "0"))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "0"))

# Heartbeat file in RAM: /tmp can be a slow overlay filesystem in containers,
# which makes healthy workers miss heartbeats and get killed.
worker_tmp_dir = "/dev/shm"

# X-Forwarded-For is honoured only from these peers. The production compose
# file sets "*" because the api is reachable only through nginx, which
# overwrites X-Forwarded-For (see apps/web/nginx/snippets/proxy.conf).
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1,::1")

# gunicorn >= 25.1 opens a control socket for the `gunicornc` CLI (worker
# status, resize, reload) under $HOME by default - read-only in our image.
# /tmp is the tmpfs mount: `docker exec <api> gunicornc -s /tmp/gunicorn.ctl ...`
control_socket = os.environ.get("GUNICORN_CONTROL_SOCKET", "/tmp/gunicorn.ctl")

# Gunicorn's own lines (master boot, worker timeouts) in the same JSON
# format as the application's.
logconfig_dict = logging_config(os.environ.get("LOG_LEVEL", "INFO"))


# --- Prometheus multiprocess mode (see app/metrics.py) ---------------------


def on_starting(server: object) -> None:
    """Master start: empty the metrics directory. Sample files left by a
    previous run would otherwise be summed into the new counters."""
    path = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if path:
        shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path, exist_ok=True)


def child_exit(server: object, worker: object) -> None:
    """A worker died or was recycled: drop its live gauges (requests in
    progress, active streams) so a dead process stops being summed.
    Its counters stay - totals must never go backwards."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)  # type: ignore[attr-defined]
