ARG PYTHON_VERSION=3.13

# uv comes from its official image at a pinned version. `pip install uv`
# installed whatever was newest at build time (builds not reproducible)
# and left pip + uv (80MB) inside the runtime image.
FROM ghcr.io/astral-sh/uv:0.12.17 AS uv

# ---- deps: virtualenv with runtime dependencies only ----------------------
FROM python:${PYTHON_VERSION}-slim AS deps
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /api
# Only the manifests are mounted, so this layer is rebuilt only when
# dependencies change. The cache mount keeps downloaded wheels between
# builds without baking them into a layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project

# ---- dev: + dev tools; compose bind-mounts the source over /api ----------
FROM deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project
# PYTHONPYCACHEPREFIX keeps .pyc files out of the bind-mounted source tree
# (otherwise they land on the host owned by root).
# PYTHONFAULTHANDLER: a crash inside a C extension prints the Python
# traceback of every thread instead of dying silently with exit 139.
ENV PATH="/api/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONPYCACHEPREFIX=/tmp/pycache
COPY . .
EXPOSE 8010
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8010", "--reload"]

# ---- production -------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS production
# Numeric UID: Kubernetes' runAsNonRoot can only verify a numeric USER.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /api
# Same path as in the deps stage: a venv's scripts hard-code its location.
COPY --from=deps /api/.venv /api/.venv
# Code stays owned by root and read-only to the app user, so a compromised
# process cannot rewrite it.
COPY alembic.ini gunicorn.conf.py ./
COPY migrations/ ./migrations/
COPY app/ ./app/
# PROMETHEUS_MULTIPROC_DIR: one metrics file per worker, summed on scrape
# (app/metrics.py). Must be set before start, never set to "" (the client
# checks only that the variable exists). /tmp is a tmpfs in compose.prod.yaml.
# PYTHONBREAKPOINT=0: a breakpoint() left in the code is a no-op here. With
# the default (pdb) it would stop a worker waiting for keyboard input that
# never comes - the worker hangs until gunicorn kills it.
ENV PATH="/api/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONBREAKPOINT=0 \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus
USER 10001:10001
EXPOSE 8010
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=2).status == 200 else 1)"]
CMD ["gunicorn", "app.asgi:app", "-c", "gunicorn.conf.py"]
