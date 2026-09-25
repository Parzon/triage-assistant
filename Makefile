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
# The dev images' non-root user gets your UID/GID (apps/api/Dockerfile, dev).
export DEV_UID := $(shell id -u)
export DEV_GID := $(shell id -g)
S    ?=

.PHONY: help setup up rebuild down nuke ps logs sh psql redis-cli config \
        migrate migration mock obs-up obs-down obs-check dashboard lint shellcheck fmt typecheck test test-api test-web test-fast e2e check \
        debug-up debug-down trace gunicorn db-activity db-locks db-top-queries redis-slowlog \
        backup restore drills image-check scan scan-compose secrets-scan session revoke reembed seed load \
        deps-api deps-web hooks prod-build prod-up deploy prod-down prod-ps prod-logs fix-perms ollama-pull evals bench-rag-filter

help: ## List all targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# --- Dev stack -------------------------------------------------------------------

setup: .env ## First run: create .env from .env.example (secrets generated), build the dev images
	$(DEV) build

# Made once, never overwritten. Every change-me value gets a random one:
# production refuses the template's secrets (ADR-0023).
.env:
	@awk 'BEGIN { FS = OFS = "=" } /^[A-Z0-9_]+=change-me/ { c = "openssl rand -hex 24"; c | getline $$2; close(c) } 1' .env.example > .env
	@chmod 600 .env
	@echo "created .env from .env.example, with generated secrets"

up: ## Start the dev stack in the background (hot reload)
	$(DEV) up -d

rebuild: ## Rebuild after a dependency change (also refreshes .venv/node_modules volumes)
	$(DEV) up -d --build --renew-anon-volumes

down: ## Stop the dev stack (volumes, i.e. the database, are kept)
	$(DEV) down

nuke: ## Stop the dev stack AND delete its volumes (wipes the local database)
	$(DEV) down --volumes --remove-orphans

ps: ## Container status and health [ENV=prod]
	$(STACK) ps

logs: ## Follow logs: all services, or S=api [ENV=prod]
	$(STACK) logs -f --tail=100 $(S)

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
# Traces go to Jaeger while it runs: the api is recreated with its OTLP
# endpoint set (unless .env or the shell sets another), and obs-down recreates
# it without, so it never exports to a stopped Jaeger.
WITH_OBS := COMPOSE_PROFILES="$$(sed -n 's/^COMPOSE_PROFILES=//p' .env),observability" \
	OTEL_EXPORTER_OTLP_ENDPOINT="$${OTEL_EXPORTER_OTLP_ENDPOINT:-http://jaeger:4318}"

obs-up: ## Start Prometheus, Alertmanager, Grafana, Jaeger + exporters next to the dev stack; traces on
	$(WITH_OBS) $(DEV) up -d
	@echo "Grafana http://localhost:$${GRAFANA_PORT:-3000}  Prometheus http://localhost:$${PROMETHEUS_PORT:-9090}  Jaeger http://localhost:$${JAEGER_PORT:-16686}"

obs-down: ## Stop the observability containers (dev stack keeps running; traces off)
	$(WITH_OBS) $(DEV) stop prometheus alertmanager grafana jaeger postgres-exporter pgbouncer-exporter redis-exporter node-exporter cadvisor
	$(DEV) up -d api

OBS := $(CURDIR)/infra/observability
obs-check: ## Validate Prometheus config, unit-test alert rules, validate Alertmanager and Jaeger configs
	docker run --rm --entrypoint promtool -v "$(OBS)/prometheus:/etc/prometheus:ro" prom/prometheus:v3.14.0 check config /etc/prometheus/prometheus.yml
	docker run --rm --entrypoint promtool -v "$(OBS)/prometheus:/p:ro" -w /p prom/prometheus:v3.14.0 test rules alerts.test.yml
	docker run --rm --entrypoint amtool -v "$(OBS)/alertmanager:/c:ro" prom/alertmanager:v0.34.1 check-config /c/alertmanager.yml
	docker run --rm -v "$(OBS)/jaeger:/etc/jaeger:ro" jaegertracing/jaeger:2.21.0 validate --config /etc/jaeger/config.yaml
	@python3 -c 'import json, glob; [json.load(open(f)) for f in glob.glob("$(OBS)/grafana/dashboards/*.json")]; print("dashboards: valid JSON")'
	@python3 $(OBS)/grafana/build_dashboard.py | diff -q - $(OBS)/grafana/dashboards/service.json >/dev/null \
	  || { echo "service.json is not what build_dashboard.py generates: run make dashboard"; exit 1; }
	@echo "dashboards: service.json matches its generator"

dashboard: ## Regenerate the Grafana dashboard from infra/observability/grafana/build_dashboard.py
	python3 $(OBS)/grafana/build_dashboard.py > $(OBS)/grafana/dashboards/service.json

# --- Mock LLM ----------------------------------------------------------------------
# The mock is not published on the host; this talks to it from inside the
# network (through the api container, which has Python).

mock: ## Mock LLM: show config+stats; change: c='{"fail_mode":"http_429"}' / c='{"tokens_per_s":5}'; c=reset
	@$(DEV) exec -T api python -c 'import sys, urllib.request as u; \
	  c = sys.argv[1]; base = "http://mock-llm:8020/_admin/"; \
	  post = lambda p, d: u.urlopen(u.Request(base + p, data=d.encode(), headers={"content-type": "application/json"})).read().decode(); \
	  print(post("reset", "{}") if c == "reset" else post("config", c) if c else \
	        "config " + u.urlopen(base + "config").read().decode() + "\nstats  " + u.urlopen(base + "stats").read().decode())' '$(c)'

# --- A real model on this machine (Ollama, profile "ollama") ----------------------

ollama-pull: ## Download a model into the local Ollama: make ollama-pull m=llama3.1:8b (starts the service)
	@test -n "$(m)" || { echo 'usage: make ollama-pull m=<model>'; exit 2; }
	$(DEV) --profile ollama up -d --wait ollama
	$(DEV) --profile ollama exec ollama ollama pull $(m)

# --- Runbook search under a team filter (docs/handbook/rag.md) --------------------

bench-rag-filter: ## Vector search behind a team filter, four ways, on 50,000 synthetic sections (dev db; ~3 min)
	$(DEV) exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' < scripts/sql/bench-rag-filter-seed.sql
	$(DEV) exec -T db sh -c 'export TEAM_ID=$$(psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -tAc "SELECT id FROM teams WHERE slug = '"'"'bench-t42'"'"'"); \
	  PGPASSWORD="$$APP_DB_PASSWORD" psql -q -h localhost -U "$$APP_DB_USER" -d "$$POSTGRES_DB"' < scripts/sql/bench-rag-filter-query.sql
	$(DEV) exec -T db sh -c 'psql -q -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' < scripts/sql/bench-rag-filter-cleanup.sql

# --- Evals (apps/api/evals; docs/handbook/ai-engineering.md) -------------------------
# In the running dev api: the service's own settings, prompt and model client.
# CI runs plumbing mode against the mock (tests/integration/test_evals.py).

evals: ## Evals against LLM_*: make evals [a="--target api --judge --judge-model gemma3:27b --repeat 3"]; a="--calibrate-judge ..."
	$(DEV) exec -T api python -m evals $(a)

# --- Code quality ---------------------------------------------------------------

lint: shellcheck ## ruff (lint + format check) for the api, oxlint for the web, shellcheck for scripts/, every setting reachable
	@python3 scripts/check_settings.py
	$(DEV) run --rm --no-deps api ruff check .
	$(DEV) run --rm --no-deps api ruff format --check .
	$(DEV) run --rm --no-deps web npm run lint

shellcheck: ## shellcheck every script in scripts/ (the deploy and restore paths run from these)
	docker run --rm -v "$(CURDIR):/mnt:ro" -w /mnt koalaman/shellcheck:v0.11.0 -S warning scripts/*.sh scripts/lib/*.sh scripts/git-hooks/*

fmt: ## Auto-format the api with ruff (files stay owned by you)
	$(DEV) run --rm --no-deps --user "$$(id -u):$$(id -g)" api ruff format .
	$(DEV) run --rm --no-deps --user "$$(id -u):$$(id -g)" api ruff check --fix .

typecheck: ## mypy (strict) on the api, tsc on the web
	$(DEV) run --rm --no-deps api mypy
	$(DEV) run --rm --no-deps web npx tsc -b

# --- Supply chain: known vulnerabilities, leaked secrets (ADR-0022) -----------
# The scanners run from images pinned by digest. In March 2026 Trivy's own
# releases (0.69.4 to 0.69.6) and its GitHub Action's tags were replaced by
# code that stole CI credentials: a scanner is supply chain too.
TRIVY := docker run --rm -v "$(CURDIR):/src:ro" -v triage-assistant-trivy-cache:/root/.cache/trivy \
  aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969
GITLEAKS := docker run --rm $(AS_ME) -v "$(CURDIR):/repo:ro" \
  ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f
# What fails a build or a release: HIGH or CRITICAL, with a fixed version to
# move to. An accepted risk goes in .trivyignore.yaml with a reason and an
# expiry date (scripts/check_trivyignore.py refuses one without either).
SCAN := --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --no-progress --table-mode detailed \
  --ignorefile /src/.trivyignore.yaml

# Built here and saved to a file for Trivy: no Docker socket in the scanner.
scan: ## Scan the production images and the web's dependencies; fail on fixable HIGH/CRITICAL (Trivy)
	@python3 scripts/check_trivyignore.py
	docker build -q --target production -t triage-assistant-api:scan apps/api >/dev/null
	docker build -q --target production -t triage-assistant-web:scan apps/web >/dev/null
	docker build -q -t triage-assistant-edge:scan tools/edge >/dev/null
	@mkdir -p .scan; trap 'rm -rf .scan' EXIT; status=0; \
	  for image in api web edge; do echo "== triage-assistant-$$image"; \
	    docker save -o .scan/$$image.tar triage-assistant-$$image:scan \
	    && $(TRIVY) image $(SCAN) --input /src/.scan/$$image.tar || status=1; done; \
	  echo "== apps/web/package-lock.json (runtime dependencies)"; \
	  $(TRIVY) fs $(SCAN) --scanners vuln /src/apps/web || status=1; exit $$status

scan-compose: ## Scan the third-party images the compose files run (Postgres, PgBouncer, Valkey, Keycloak, monitoring)
	@python3 scripts/check_trivyignore.py
	@status=0; for image in $$($(PROD) --profile '*' config --images | grep -v '^triage-assistant' | sort -u); do \
	  echo "== $$image"; $(TRIVY) image $(SCAN) $$image || status=1; done; exit $$status

secrets-scan: ## Leaked secrets in every commit (gitleaks); fake credentials on purpose: .gitleaks.toml
	$(GITLEAKS) git --no-banner --redact --config /repo/.gitleaks.toml --gitleaks-ignore-path /repo/.gitleaksignore /repo

# --- Tests -------------------------------------------------------------------

test: test-api test-web ## Every test suite (api + web), as CI runs them

# --build: `run` never rebuilds an existing image, and the test project has
# its own images - without it, tests silently run against stale dependencies.
# Keycloak starts first and boots (~20s) while images build and migrations
# run; the sign-in tests wait for it (tests/integration/test_auth_flow.py).
# `run --build` rebuilds the service it runs, not its dependencies: the mock
# is built explicitly, or a change to it is tested against a stale image.
test-api: ## api suite + coverage gate, in a throwaway stack (real Postgres/PgBouncer/Valkey/Keycloak/mock LLM)
	@$(TEST) up -d keycloak \
	  && $(TEST) build -q mock-llm \
	  && $(TEST) run --build --rm migrate sh -c 'alembic upgrade head && alembic check' \
	  && $(TEST) run --build --rm $(AS_ME) api pytest --cov --cov-report=term --cov-report=xml:coverage.xml; \
	  status=$$?; $(TEST) down --volumes --remove-orphans >/dev/null 2>&1; exit $$status

test-web: ## web unit/component tests + coverage gate (vitest)
	$(DEV) run --rm --no-deps $(AS_ME) web npm test

test-fast: ## api unit tests only: no database, seconds
	$(DEV) run --rm --no-deps $(AS_ME) api pytest tests/unit -q

# The browser joins the production stack's network: it reaches the site as
# http://web:8080 and can drive the mock LLM's admin API.
# The browser runs on the host network and opens https://localhost:<edge
# port>, like a user: through the TLS edge, nginx, the api. The edge's local
# CA is not in the browser's trust store, so certificate errors are ignored
# (E2E_IGNORE_HTTPS_ERRORS) - for this local CA only. The mock's admin API is
# reached at its container IP. Docker Desktop: enable host networking
# (Settings > Resources > Network), or rely on CI.
EDGE_HTTPS_PORT ?= $(or $(shell sed -n 's/^EDGE_HTTPS_PORT=//p' .env 2>/dev/null),443)
# The tests sign in as the bundled identity provider's demo users. The
# password travels in the environment (-e NAME, no value), never on a command
# line that make echoes into terminals and CI logs.
e2e: export DEMO_USER_PASSWORD ?= $(shell sed -n 's/^DEMO_USER_PASSWORD=//p' .env 2>/dev/null)
e2e: ## Browser tests (Playwright) through the TLS edge of the running production stack: make prod-up first
	docker run --rm --network host --shm-size=1g $(AS_ME) \
	  -e npm_config_cache=/tmp/npm -e DEMO_USER_PASSWORD \
	  -e E2E_BASE_URL=https://localhost:$(EDGE_HTTPS_PORT) -e E2E_IGNORE_HTTPS_ERRORS=1 \
	  -e MOCK_ADMIN_URL=http://$$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' $$($(PROD) ps -q mock-llm)):8020/_admin \
	  -v "$(CURDIR)/tests/e2e:/e2e" -w /e2e mcr.microsoft.com/playwright:v1.63.0-noble \
	  sh -c 'npm ci --no-audit --no-fund --loglevel=error && npx playwright test'

check: lint typecheck test ## Everything CI checks, before you push

# The production api image, checked the way CI checks it (CI calls this target).
IMG := triage-assistant-api:check
image-check: ## Build the production api image; assert non-root, no dev tools or pip, every module imports
	docker build -q --target production -t $(IMG) apps/api >/dev/null
	test "$$(docker run --rm --entrypoint id $(IMG) -u)" = "10001"
	@if docker run --rm --entrypoint sh $(IMG) -c 'ls /api/.venv/bin' | grep -qxE 'ruff|pytest|uv'; then \
	  echo "dev tooling found in the production image"; exit 1; fi
	@if docker run --rm --entrypoint sh $(IMG) -c 'ls -d /usr/local/lib/python3*/site-packages/pip' >/dev/null 2>&1; then \
	  echo "pip found in the production image"; exit 1; fi
	@# Tests run with dev dependencies installed, so an import that only resolves
	@# through a test tool passes CI and crashes production. Read-only rootfs +
	@# tmpfs /tmp, exactly as compose.prod.yaml runs it.
	docker run --rm --read-only --tmpfs /tmp --entrypoint python $(IMG) -c "import app.main, app.triage, app.llm, app.routes.chat, app.metrics"
	@# The operator CLI runs next to the server (make session): it must never
	@# write into the server's metrics directory - here one it could not write.
	docker run --rm --read-only --tmpfs /tmp -e PROMETHEUS_MULTIPROC_DIR=/not-writable \
	  --entrypoint python $(IMG) -m app.cli --help >/dev/null
	@echo "production image: non-root, no dev tools or pip, all modules import, the CLI runs"

# --- Debugging toolkit ------------------------------------------------------------
# ENV=prod points a target at the production-shaped stack instead of dev.
STACK = $(if $(filter prod,$(ENV)),$(PROD),$(DEV))
# The running api container, looked up through compose: after a rolling
# deploy (make deploy) it is api-2, not api-1 - never hardcode the name.
API_C = $(shell $(STACK) ps -q api | head -1)
PROD_API = $(shell $(PROD) ps -q api | head -1)

# `-f` disables the automatic compose.override.yaml merge: list it explicitly.
debug-up: ## api under debugpy on 127.0.0.1:5678 (VS Code: "Attach to api"), asyncio debug mode on
	$(DEV) -f compose.yaml -f compose.override.yaml -f compose.debug.yaml up -d api
	@echo "debugpy listening on 127.0.0.1:5678 - attach from VS Code (Run and Debug)"

debug-down: ## Back to the normal hot-reload api
	$(DEV) up -d api

trace: ## Every log line of one request across nginx and the api, then its trace in Jaeger: make trace id=<X-Request-ID>
	@test -n "$(id)" || { echo 'usage: make trace id=<request id>'; exit 2; }
	@lines=$$($(STACK) logs --no-log-prefix --no-color web api 2>/dev/null | grep -F '$(id)'); \
	  echo "$$lines" | jq -Rc 'fromjson? // .'; \
	  echo "$$lines" | jq -Rr 'fromjson? | .trace_id? // empty' | sort -u \
	  | sed "s#^#trace: http://localhost:$${JAEGER_PORT:-16686}/trace/#"

gunicorn: ## gunicorn control socket (prod image): make gunicorn c="show workers" | "show stats" | "worker add 1"
	docker exec $(PROD_API) gunicornc -s /tmp/gunicorn.ctl -c "$(or $(c),show workers)"

db-activity: ## Postgres: every connection and what it is doing now [ENV=prod]
	$(STACK) exec -T db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' < scripts/sql/activity.sql

db-locks: ## Postgres: blocked queries and who blocks them [ENV=prod]
	$(STACK) exec -T db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' < scripts/sql/locks.sql

db-top-queries: ## Postgres: most expensive queries (pg_stat_statements) [ENV=prod]
	$(STACK) exec -T db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' < scripts/sql/top-queries.sql

redis-slowlog: ## Valkey: slowest recent commands and latency [ENV=prod]
	$(STACK) exec -T redis sh -c 'valkey-cli SLOWLOG GET 10; valkey-cli INFO commandstats | head -12'

backup: ## Dump the database to backups/ (keeps the newest 14) [ENV=prod]
	@STACK="$(STACK)" NAME=$(or $(ENV),dev) scripts/backup.sh

restore: ## Replace the database with a dump: make restore file=backups/<dump> [ENV=prod]
	@test -n "$(file)" || { echo 'usage: make restore file=backups/<dump>'; exit 2; }
	@STACK="$(STACK)" scripts/restore.sh "$(file)"

drills: ## Failure drills on the prod stack, one fault at a time: make drills [d="redis-hang db-stop"]
	@scripts/failure-drills.sh "$(d)"

# --- Sessions for scripts -------------------------------------------------------
# A signed-in user without the identity provider (app/cli.py), for curl, load
# tests and drills. groups= are claim values, as the provider would send them.

session: ## Print a session cookie ("name=value"): make session [email=you@example.com] [groups="team:default:admin org:admin"] [ENV=prod]
	@docker exec $(API_C) python -m app.cli session --email $(or $(email),script@example.com) \
	  $(foreach g,$(or $(groups),team:default:viewer),--group $(g)) --hours $(or $(hours),4)

reembed: ## After changing EMBEDDING_* (but the query prefix): embed the runbooks again [ENV=prod]
	@docker exec $(API_C) python -m app.cli reembed

revoke: ## End every session of a user now (after removing their access at the provider): make revoke email=... [ENV=prod]
	@test -n "$(email)" || { echo 'usage: make revoke email=<address> [ENV=prod]'; exit 2; }
	@docker exec $(API_C) python -m app.cli revoke --email "$(email)"

# --- Audit trail (app/audit.py, ADR-0019) --------------------------------------
# The api may add audit events, never change or delete them: pruning runs as
# the schema owner, in the database container.

audit: ## The audit trail, newest first: make audit [a="--action runbook.saved --target 17 --hours 24"] [ENV=prod]
	@docker exec $(API_C) python -m app.cli audit $(a)

audit-prune: ## Delete audit events older than days= (no default: your retention policy decides) [ENV=prod]
	@case "$(days)" in ''|*[!0-9]*) echo 'usage: make audit-prune days=<whole days to keep> [ENV=prod]'; exit 2;; esac
	@$(STACK) exec -T db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c \
	  "DELETE FROM audit_events WHERE created_at < now() - make_interval(days => $(days))"'

# --- Load tests ------------------------------------------------------------------
# Load tests run against the production-shaped stack (make prod-up), through
# nginx, from a container on its network. Raise the rate limits for them:
#   ALERTS_RATE_LIMIT=1000000 CHAT_RATE_LIMIT=1000000 make prod-up

seed: ## Insert N synthetic alerts: make seed n=1000000 [ENV=prod]
	@test -n "$(n)" || { echo 'usage: make seed n=<rows> [ENV=prod]'; exit 2; }
	$(if $(filter prod,$(ENV)),$(PROD),$(DEV)) exec -T db sh -c 'psql -q -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v n=$(n)' < scripts/seed-alerts.sql

# Load tests run as a signed-in user: a session minted by app.cli (make
# session) for a member of two seeded teams, and the site's origin for POSTs.
LOAD_SESSION = docker exec $(PROD_API) python -m app.cli session \
  --email load@example.com --group team:payments:viewer --group team:platform:viewer --hours 4
LOAD_ORIGIN = docker exec $(PROD_API) printenv PUBLIC_URL

s ?= alerts-read
load: ## k6 scenario through nginx on the prod stack: make load s=chat VUS=100 (alerts-read|chat|health)
	docker run --rm --network triage-assistant-prod_default $(AS_ME) -v "$(CURDIR)/tests/load:/scripts:ro" \
	  -e SESSION_COOKIE="$$($(LOAD_SESSION))" -e ORIGIN="$$($(LOAD_ORIGIN))" \
	  -e BASE_URL=http://web:8080 -e RATE=$(RATE) -e VUS=$(VUS) -e DURATION=$(DURATION) \
	  -e K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090/api/v1/write \
	  -e 'K6_PROMETHEUS_RW_TREND_STATS=p(50),p(95),p(99),max' \
	  grafana/k6:2.3.0 run --no-usage-report \
	  $$(docker ps -q -f name='^triage-assistant-prod-prometheus-1$$' | grep -q . && echo -o experimental-prometheus-rw) \
	  /scripts/k6/$(s).js

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

# --wait: returns once every service is healthy (the one-shot migrate: exited
# 0), so the next command - e2e in CI, a smoke check - never races a service
# that is still starting (Keycloak takes ~20s).
prod-up: ## Build and start the production stack; returns when it is healthy (HTTPS on EDGE_HTTPS_PORT)
	@# A host that runs released images names them with a registry prefix in
	@# .env: building the checkout would label local code with a release's name.
	@prefix=$${IMAGE_PREFIX:-$$(sed -n 's/^IMAGE_PREFIX=//p' .env 2>/dev/null)}; case "$$prefix" in */*) \
	  echo "this host runs released images ($$prefix): deploy with make deploy tag=X.Y.Z"; \
	  echo "to run this checkout instead: IMAGE_PREFIX=triage-assistant IMAGE_TAG=local make prod-up"; \
	  exit 2;; esac
	$(PROD) up -d --build --wait --wait-timeout 300

deploy: ## Roll a release onto this host without refusing requests: make deploy tag=1.4.0 (PULL=0: local images)
	@test -n "$(tag)" || { echo 'usage: make deploy tag=<image tag>'; exit 2; }
	scripts/deploy.sh "$(tag)"

prod-down: ## Stop the production stack (volumes kept)
	$(PROD) down

prod-ps: ## Production container status and health
	$(PROD) ps

prod-logs: ## Follow production logs: all, or S=api
	$(PROD) logs -f --tail=100 $(S)

# --- Housekeeping ------------------------------------------------------------

hooks: ## Run lint, types and unit tests before every git push (per clone)
	git config core.hooksPath scripts/git-hooks
	@echo "installed: scripts/git-hooks/pre-push (skip once with git push --no-verify)"

fix-perms: ## Linux: hand files created by root in containers back to your user
	docker run --rm -v "$(CURDIR):/w" alpine chown -R "$$(id -u):$$(id -g)" /w
