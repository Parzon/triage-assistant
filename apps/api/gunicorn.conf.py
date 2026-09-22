import multiprocessing
import os

# Real multi-process production shape: gunicorn forks N worker processes,
# each running a full uvicorn (ASGI) event loop. Worker count formula
# (2 x cores + 1) is gunicorn's own long-standing default recommendation
# — override via WEB_CONCURRENCY for a box with different constraints
# (e.g. a small container with a CPU limit lower than the host's core
# count) rather than hardcoding a number that's only right for one machine.
bind = f"0.0.0.0:{os.environ.get('PORT', '8010')}"
workers = int(os.environ.get("WEB_CONCURRENCY", multiprocessing.cpu_count() * 2 + 1))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
