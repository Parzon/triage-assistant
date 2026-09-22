# Every command a developer needs, in one place: `make` lists them.
# Targets are thin wrappers - read a recipe to see the real docker compose
# command, and run that directly whenever you prefer.
# Works with the GNU make 3.81 that macOS ships (no newer features used).

SHELL := bash
.DEFAULT_GOAL := help

DEV  := docker compose
PROD := docker compose -p triage-assistant-prod -f compose.yaml -f compose.prod.yaml
S    ?=

.PHONY: help setup up rebuild down nuke ps logs sh psql redis-cli config \
        lint fmt deps-api deps-web prod-build prod-up prod-down prod-ps prod-logs \
        fix-perms

help: ## List all targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

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

# --- Code quality ---------------------------------------------------------------

lint: ## ruff (lint + format check) for the api, oxlint for the web
	$(DEV) run --rm --no-deps api ruff check .
	$(DEV) run --rm --no-deps api ruff format --check .
	$(DEV) run --rm --no-deps web npm run lint

fmt: ## Auto-format the api with ruff (files stay owned by you)
	$(DEV) run --rm --no-deps --user "$$(id -u):$$(id -g)" api ruff format .
	$(DEV) run --rm --no-deps --user "$$(id -u):$$(id -g)" api ruff check --fix .

# --- Dependencies --------------------------------------------------------------
# Lockfiles are updated inside the container (same uv/npm as CI), as your
# user so the files stay yours, then the image and its dependency volume
# are rebuilt. Plain `up --build` would keep the old .venv/node_modules.

deps-api: ## Add a Python dependency: make deps-api p=httpx   (dev-only: p="--dev pytest")
	@test -n "$(p)" || { echo 'usage: make deps-api p=<package>'; exit 2; }
	$(DEV) run --rm --no-deps --user "$$(id -u):$$(id -g)" -e UV_CACHE_DIR=/tmp/uv api uv add --no-sync $(p)
	$(DEV) up -d --build --renew-anon-volumes api

deps-web: ## Add an npm dependency: make deps-web p=zod   (dev-only: p="-D vitest")
	@test -n "$(p)" || { echo 'usage: make deps-web p=<package>'; exit 2; }
	$(DEV) run --rm --no-deps --user "$$(id -u):$$(id -g)" -e npm_config_cache=/tmp/npm web npm install --package-lock-only --no-audit --no-fund $(p)
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
