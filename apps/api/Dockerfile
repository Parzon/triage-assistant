FROM python:3.12-slim AS base

WORKDIR /api

# Deps before app code so the (slow) install layer stays cached across
# code-only rebuilds.
COPY pyproject.toml uv.lock ./
RUN pip install uv
RUN uv sync --frozen

# ---- dev: what docker-compose uses locally (source bind-mounted, hot reload) ----
FROM base AS dev
COPY . .
EXPOSE 8010
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010", "--reload"]

# ---- production: gunicorn-managed multi-process, non-root, hardened ----
FROM base AS production
COPY main.py gunicorn.conf.py ./

# Non-root: build as root (needed for package installs), run as an
# unprivileged user - a compromised app process can't touch anything
# root-owned inside the container.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /api
USER appuser

EXPOSE 8010

# python, not curl - already in the base image, no extra package needed.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8010/health').status==200 else 1)"

# Gunicorn manages N uvicorn worker processes (see gunicorn.conf.py for
# the worker-count formula) - real multi-process production shape, not
# a single process.
CMD ["uv", "run", "gunicorn", "main:app", "-c", "gunicorn.conf.py"]
