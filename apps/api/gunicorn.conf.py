"""Gunicorn settings for the production image.

Every value can be overridden by an environment variable so the same
image runs correctly on a laptop, a single VM, or one-process-per-task
platforms (ECS, Kubernetes) without a rebuild.
"""

import os
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

# Recycle workers to cap slow memory growth; jitter stops them all
# restarting at once. Recycling waits for in-flight requests
# (graceful_timeout), so streams are not cut.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "10000"))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "1000"))

# Heartbeat file in RAM: /tmp can be a slow overlay filesystem in containers,
# which makes healthy workers miss heartbeats and get killed.
worker_tmp_dir = "/dev/shm"

# X-Forwarded-For is honoured only from these peers. The production compose
# file sets "*" because the api is reachable only through nginx, which
# overwrites X-Forwarded-For (see apps/web/nginx/snippets/proxy.conf).
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1,::1")

# Gunicorn's own lines (master boot, worker timeouts) in the same JSON
# format as the application's.
logconfig_dict = logging_config(os.environ.get("LOG_LEVEL", "INFO"))
