FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

# A dev/test tool, not a shipped artifact: one stage is enough.
FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project
COPY mock_llm.py ./
USER 10001:10001
EXPOSE 8020
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8020/health', timeout=2).status == 200 else 1)"]
# One process on purpose: the runtime-tunable behaviour lives in memory.
CMD ["uvicorn", "mock_llm:app", "--host", "0.0.0.0", "--port", "8020", "--no-access-log"]
