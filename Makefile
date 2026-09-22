# Every command a developer needs, in one place: `make` lists them.
# Targets are thin wrappers - read a recipe to see the real docker compose
# command, and run that directly whenever you prefer.
# Works with the GNU make 3.81 that macOS ships (no newer features used).

SHELL := bash
.DEFAULT_GOAL := help

DEV  := docker compose
PROD := docker compose -p triage-assistant-prod -f compose.yaml -f compose.prod.yaml
TEST := docker compose -p triage-assistant-test -f compose.yaml -f compose.override.yaml -f compose.test.yaml
# Run as your UID so files written into the repo stay yours. HOME=/tmp: a UID
# that has no account in the image (CI runners are 1001; only 1000 happens to
# match the node image's user) gets HOME=/, and tools that write there fail.
AS_ME := --user "$$(id -u):$$(id -g)" -e HOME=/tmp
S    ?=

.PHONY: help setup up rebuild down nuke ps logs sh psql redis-cli config \
        migrate migration mock obs-up obs-down obs-check lint fmt typecheck test test-api test-web test-fast e2e check \
        image-check deps-api deps-web prod-build prod-up prod-down prod-ps prod-logs fix-perms

help: ## List all targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# --- Dev stack -------------------------------------------------------------------

setup: ## First run: create .env from .env.example, build the dev images
	@test -f .env || { cp .env.example .env; echo "created .env from .env.example"; }
	$(DEV) build

up: ## Start the dev stack in the background (hot reload)
	$(DEV) up -d

rebuild: ## Rebuild after a dependency change (also refreshes .venv/node_modules volumes)
	$(DEV) up -d --build --renew-anon-volumes

down: ## Stop the dev stack (volumes, i.e. the database, are kept)
	$(DEV) down

nuke: ## Stop the dev stack AND delete its volumes (wipes the local database)
	$(DEV) down --volumes --remove-orphans

ps: ## Container status and health
	$(DEV) ps

logs: ## Follow logs: all services, or S=api
	$(DEV) logs -f --tail=100 $(S)

sh: ## Shell in a running container: make sh S=web (default api)
	$(DEV) exec $(or $(S),api) sh

psql: ## psql into the dev database (direct, not through pgbouncer)
	$(DEV) exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

redis-cli: ## valkey-cli into the rate-limit store
	$(DEV) exec redis valkey-cli

config: ## Print the merged compose config (make config ENV=prod for production)
	@if [ "$(ENV)" = "prod" ]; then $(PROD) config; else $(DEV) config; fi

# --- Database -------------------------------------------------------------------

migrate: ## Apply migrations to the dev database (also runs on every `make up`)
	$(DEV) run --rm migrate

migration: ## New migration from model changes: make migration m="add alert source index"
	@test -n "$(m)" || { echo 'usage: make migration m="<what changes>"'; exit 2; }
	$(DEV) run --rm $(AS_ME) migrate alembic revision --autogenerate -m "$(m)"

# --- Observability --------------------------------------------------------------
# Profiles from .env plus "observability". Not `--profile observability`: a
# --profile flag REPLACES the profiles in .env (the mock LLM would drop out).
WITH_OBS := COMPOSE_PROFILES="$$(sed -n 's/^COMPOSE_PROFILES=//p' .env),observability"

obs-up: ## Start Prometheus, Alertmanager, Grafana + exporters next to the dev stack
	$(WITH_OBS) $(DEV) up -d
	@echo "Grafana http://localhost:$${GRAFANA_PORT:-3000}  Prometheus http://localhost:$${PROMETHEUS_PORT:-9090}"

obs-down: ## Stop the observability containers (dev stack keeps running)
	$(WITH_OBS) $(DEV) stop prometheus alertmanager grafana postgres-exporter pgbouncer-exporter redis-exporter node-exporter cadvisor

OBS := $(CURDIR)/infra/observability
obs-check: ## Validate Prometheus config, unit-test alert rules, validate Alertmanager config
	docker run --rm --entrypoint promtool -v "$(OBS)/prometheus:/etc/prometheus:ro" prom/prometheus:v3.14.0 check config /etc/prometheus/prometheus.yml
	docker run --rm --entrypoint promtool -v "$(OBS)/prometheus:/p:ro" -w /p prom/prometheus:v3.14.0 test rules alerts.test.yml
	docker run --rm --entrypoint amtool -v "$(OBS)/alertmanager:/c:ro" prom/alertmanager:v0.34.1 check-config /c/alertmanager.yml
	@python3 -c 'import json, glob; [json.load(open(f)) for f in glob.glob("$(OBS)/grafana/dashboards/*.json")]; print("dashboards: valid JSON")'

# --- Mock LLM ----------------------------------------------------------------------
# The mock is not published on the host; this talks to it from inside the
# network (through the api container, which has Python).

mock: ## Mock LLM: show config+stats; change: c='{"fail_mode":"http_429"}' / c='{"tokens_per_s":5}'; c=reset
	@$(DEV) exec -T api python -c 'import sys, urllib.request as u; \
	  c = sys.argv[1]; base = "http://mock-llm:8020/_admin/"; \
	  post = lambda p, d: u.urlopen(u.Request(base + p, data=d.encode(), headers={"content-type": "application/json"})).read().decode(); \
	  print(post("reset", "{}") if c == "reset" else post("config", c) if c else \
	        "config " + u.urlopen(base + "config").read().decode() + "\nstats  " + u.urlopen(base + "stats").read().decode())' '$(c)'

# --- Code quality ---------------------------------------------------------------

lint: ## ruff (lint + format check) for the api, oxlint for the web
	$(DEV) run --rm --no-deps api ruff check .
	$(DEV) run --rm --no-deps api ruff format --check .
	$(DEV) run --rm --no-deps web npm run lint

fmt: ## Auto-format the api with ruff (files stay owned by you)
	$(DEV) run --rm --no-deps --user "$$(id -u):$$(id -g)" api ruff format .
	$(DEV) run --rm --no-deps --user "$$(id -u):$$(id -g)" api ruff check --fix .

typecheck: ## mypy (strict) on the api, tsc on the web
	$(DEV) run --rm --no-deps api mypy
	$(DEV) run --rm --no-deps web npx tsc -b

# --- Tests -------------------------------------------------------------------

test: test-api test-web ## Every test suite (api + web), as CI runs them

# --build: `run` never rebuilds an existing image, and the test project has
# its own images - without it, tests silently run against stale dependencies.
test-api: ## api suite + coverage gate, in a throwaway stack (real Postgres/PgBouncer/Valkey/mock LLM)
	@$(TEST) run --build --rm migrate sh -c 'alembic upgrade head && alembic check' \
	  && $(TEST) run --build --rm $(AS_ME) api pytest --cov --cov-report=term --cov-report=xml:coverage.xml; \
	  status=$$?; $(TEST) down --volumes --remove-orphans >/dev/null 2>&1; exit $$status

test-web: ## web unit/component tests + coverage gate (vitest)
	$(DEV) run --rm --no-deps $(AS_ME) web npm test

test-fast: ## api unit tests only: no database, seconds
	$(DEV) run --rm --no-deps $(AS_ME) api pytest tests/unit -q

# The browser joins the production stack's network: it reaches the site as
# http://web:8080 and can drive the mock LLM's admin API.
e2e: ## Browser tests (Playwright) against the running production stack: make prod-up first
	docker run --rm --network triage-assistant-prod_default --shm-size=1g $(AS_ME) \
	  -e npm_config_cache=/tmp/npm \
	  -e E2E_BASE_URL=http://web:8080 -e MOCK_ADMIN_URL=http://mock-llm:8020/_admin \
	  -v "$(CURDIR)/tests/e2e:/e2e" -w /e2e mcr.microsoft.com/playwright:v1.63.0-noble \
	  sh -c 'npm ci --no-audit --no-fund --loglevel=error && npx playwright test'

check: lint typecheck test ## Everything CI checks, before you push

# The production api image, checked the way CI checks it (CI calls this target).
IMG := triage-assistant-api:check
image-check: ## Build the production api image; assert non-root, no dev tools, every module imports
	docker build -q --target production -t $(IMG) apps/api >/dev/null
	test "$$(docker run --rm --entrypoint id $(IMG) -u)" = "10001"
	@if docker run --rm --entrypoint sh $(IMG) -c 'ls /api/.venv/bin' | grep -qxE 'ruff|pytest|uv'; then \
	  echo "dev tooling found in the production image"; exit 1; fi
	@# Tests run with dev dependencies installed, so an import that only resolves
	@# through a test tool passes CI and crashes production. Read-only rootfs +
	@# tmpfs /tmp, exactly as compose.prod.yaml runs it.
	docker run --rm --read-only --tmpfs /tmp --entrypoint python $(IMG) -c "import app.main, app.triage, app.llm, app.routes.chat, app.metrics"
	@echo "production image: non-root, no dev tools, all modules import"

# --- Dependencies --------------------------------------------------------------
# Lockfiles are updated inside the container (same uv/npm as CI), as your
# user so the files stay yours, then the image and its dependency volume
# are rebuilt. Plain `up --build` would keep the old .venv/node_modules.
# Package specs travel in an env var: unquoted on the host shell, a spec
# like httpx>=0.28 would be read as "redirect output to a file named =0.28".

deps-api: ## Add a Python dependency: make deps-api p=httpx   (dev-only: p="--dev pytest")
	@test -n "$(p)" || { echo 'usage: make deps-api p=<package>'; exit 2; }
	$(DEV) run --rm --no-deps $(AS_ME) -e UV_CACHE_DIR=/tmp/uv -e PKGS='$(p)' api sh -c 'set -f; uv add --no-sync $$PKGS'
	$(DEV) up -d --build --renew-anon-volumes api

deps-web: ## Add an npm dependency: make deps-web p=zod   (dev-only: p="-D vitest")
	@test -n "$(p)" || { echo 'usage: make deps-web p=<package>'; exit 2; }
	@# Plain `docker run`, not the compose service: npm also rewrites a hidden lockfile
	@# inside node_modules, and the service's node_modules volume is root-owned.
	docker run --rm $(AS_ME) -v "$(CURDIR)/apps/web:/app" -w /app -e npm_config_cache=/tmp/npm \
	  -e PKGS='$(p)' node:$$(cat apps/web/.nvmrc)-slim sh -c 'set -f; npm install --package-lock-only --no-audit --no-fund $$PKGS'
	$(DEV) up -d --build --renew-anon-volumes web

# --- Production-shaped stack (locally, or on the demo VM) ----------------------

prod-build: ## Build the production images
	$(PROD) build

prod-up: ## Build and start the production stack (nginx on HTTP_PORT)
	$(PROD) up -d --build

prod-down: ## Stop the production stack (volumes kept)
	$(PROD) down

prod-ps: ## Production container status and health
	$(PROD) ps

prod-logs: ## Follow production logs: all, or S=api
	$(PROD) logs -f --tail=100 $(S)

# --- Housekeeping ------------------------------------------------------------

fix-perms: ## Linux: hand files created by root in containers back to your user
	docker run --rm -v "$(CURDIR):/w" alpine chown -R "$$(id -u):$$(id -g)" /w
