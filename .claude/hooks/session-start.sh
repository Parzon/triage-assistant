#!/bin/bash
# Claude Code on the web starts each session from a fresh clone, without the
# dev images. This installs the api's and the web's dependencies on the host,
# so unit tests, ruff, mypy, oxlint, tsc and Vitest run there:
#   cd apps/api && uv run pytest tests/unit
#   cd apps/web && npm test
# Laptops run them in the containers (make test-fast, make lint), so this
# does nothing outside a cloud session.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR/apps/api"
uv sync --frozen --quiet

cd "$CLAUDE_PROJECT_DIR/apps/web"
npm ci --no-audit --no-fund --loglevel=error
