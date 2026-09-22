FROM python:3.12-slim

WORKDIR /api

# Deps before app code so the (slow) install layer stays cached across
# code-only rebuilds.
COPY pyproject.toml uv.lock ./
RUN pip install uv
RUN uv sync --frozen

COPY main.py ./

EXPOSE 8010

# --host 0.0.0.0: default bind is 127.0.0.1, invisible outside the container.
# No --reload: dev-only convenience, meaningless once code is baked into
# a built image.
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
