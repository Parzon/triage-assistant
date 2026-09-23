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
        debug-up debug-down netshoot tcpdump strace trace gunicorn db-activity db-locks db-top-queries redis-slowlog \
        backup restore acme-test fresh-host-test drills image-check session revoke seed load load-tool load-compare py-spy-dump py-spy-top py-spy-record \
        deps-api deps-web hooks prod-build prod-up deploy prod-down prod-ps prod-logs fix-perms

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

# --- Code quality ---------------------------------------------------------------

lint: shellcheck ## ruff (lint + format check) for the api, oxlint for the web, shellcheck for scripts/
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

# --- Tests -------------------------------------------------------------------

test: test-api test-web ## Every test suite (api + web), as CI runs them

# --build: `run` never rebuilds an existing image, and the test project has
# its own images - without it, tests silently run against stale dependencies.
# Keycloak starts first and boots (~20s) while images build and migrations
# run; the sign-in tests wait for it (tests/integration/test_auth_flow.py).
test-api: ## api suite + coverage gate, in a throwaway stack (real Postgres/PgBouncer/Valkey/Keycloak/mock LLM)
	@$(TEST) up -d keycloak \
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
image-check: ## Build the production api image; assert non-root, no dev tools, every module imports
	docker build -q --target production -t $(IMG) apps/api >/dev/null
	test "$$(docker run --rm --entrypoint id $(IMG) -u)" = "10001"
	@if docker run --rm --entrypoint sh $(IMG) -c 'ls /api/.venv/bin' | grep -qxE 'ruff|pytest|uv'; then \
	  echo "dev tooling found in the production image"; exit 1; fi
	@# Tests run with dev dependencies installed, so an import that only resolves
	@# through a test tool passes CI and crashes production. Read-only rootfs +
	@# tmpfs /tmp, exactly as compose.prod.yaml runs it.
	docker run --rm --read-only --tmpfs /tmp --entrypoint python $(IMG) -c "import app.main, app.triage, app.llm, app.routes.chat, app.metrics"
	@# The operator CLI runs next to the server (make session): it must never
	@# write into the server's metrics directory - here one it could not write.
	docker run --rm --read-only --tmpfs /tmp -e PROMETHEUS_MULTIPROC_DIR=/not-writable \
	  --entrypoint python $(IMG) -m app.cli --help >/dev/null
	@echo "production image: non-root, no dev tools, all modules import, the CLI runs"

# --- Debugging toolkit ------------------------------------------------------------
# ENV=prod points a target at the production-shaped stack instead of dev.
STACK = $(if $(filter prod,$(ENV)),$(PROD),$(DEV))
# The running api container, looked up through compose: after a rolling
# deploy (make deploy) it is api-2, not api-1 - never hardcode the name.
API_C = $(shell $(STACK) ps -q api | head -1)
PROD_API = $(shell $(PROD) ps -q api | head -1)
NETSHOOT := nicolaka/netshoot:v0.14

# `-f` disables the automatic compose.override.yaml merge: list it explicitly.
debug-up: ## api under debugpy on 127.0.0.1:5678 (VS Code: "Attach to api"), asyncio debug mode on
	$(DEV) -f compose.yaml -f compose.override.yaml -f compose.debug.yaml up -d api
	@echo "debugpy listening on 127.0.0.1:5678 - attach from VS Code (Run and Debug)"

debug-down: ## Back to the normal hot-reload api
	$(DEV) up -d api

netshoot: ## Shell inside the api container's network namespace (curl, dig, ss, tcpdump...)
	docker run --rm -it --network container:$(API_C) $(NETSHOOT)

SECS ?= 20
tcpdump: ## Capture api traffic for SECS seconds -> .captures/api.pcap (open in Wireshark)
	@mkdir -p .captures && chmod 777 .captures
	docker run --rm --network container:$(API_C) --cap-add NET_ADMIN --cap-add NET_RAW \
	  -v "$(CURDIR)/.captures:/cap" $(NETSHOOT) timeout $(SECS) tcpdump -i eth0 -s 0 -w /cap/api.pcap 'tcp port 8010' || true
	@echo "wrote .captures/api.pcap"

strace: ## Syscall summary of one api worker for SECS seconds (what is it asking the kernel for?)
	@pid=$$(docker top $(API_C) -o pid,args | awk '/uvicorn|gunicorn/ {p=$$1} END {print p}'); \
	  echo "tracing host pid $$pid"; \
	  docker run --rm --pid=host --cap-add SYS_PTRACE $(NETSHOOT) timeout $(SECS) strace -c -f -p $$pid || true

trace: ## Every log line of one request across nginx and the api: make trace id=<X-Request-ID>
	@test -n "$(id)" || { echo 'usage: make trace id=<request id>'; exit 2; }
	@$(STACK) logs --no-log-prefix --no-color web api 2>/dev/null | grep -F '$(id)' | jq -Rc 'fromjson? // .'

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

acme-test: ## Rehearse automatic HTTPS certificates: the edge image gets one over ACME from Pebble (Let's Encrypt's test CA)
	@scripts/acme-test.sh

fresh-host-test: ## The committed tree on a clean Docker host (DinD): prod-up + every user path [DUMP=backups/x.dump]
	@scripts/fresh-host-test.sh

drills: ## Failure drills on the prod stack, one fault at a time: make drills [d="redis-hang db-stop"]
	@scripts/failure-drills.sh "$(d)"

# --- Sessions for scripts -------------------------------------------------------
# A signed-in user without the identity provider (app/cli.py), for curl, load
# tests and drills. groups= are claim values, as the provider would send them.

session: ## Print a session cookie ("name=value"): make session [email=you@example.com] [groups="team:default:admin org:admin"] [ENV=prod]
	@docker exec $(API_C) python -m app.cli session --email $(or $(email),script@example.com) \
	  $(foreach g,$(or $(groups),team:default:viewer),--group $(g)) --hours $(or $(hours),4)

revoke: ## End every session of a user now (after removing their access at the provider): make revoke email=... [ENV=prod]
	@test -n "$(email)" || { echo 'usage: make revoke email=<address> [ENV=prod]'; exit 2; }
	@docker exec $(API_C) python -m app.cli revoke --email "$(email)"

# --- Performance lab ------------------------------------------------------------
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

# One reference implementation per tool, same scenario (tests/load/<tool>/).
load-compare: ## Same scenario through k6, vegeta, oha, Locust, Artillery, JMeter; one table (RATE=200 DURATION=30)
	RATE=$(or $(RATE),200) DURATION=$(or $(DURATION),30) scripts/load-compare.sh

USERS ?= 50
CLASS ?= AlertReader
load-tool: ## Run one tool's reference script: make load-tool TOOL=locust CLASS=ChatUser USERS=50
	@session="$$($(LOAD_SESSION))"; origin="$$($(LOAD_ORIGIN))"; \
	case "$(TOOL)" in \
	  locust) docker run --rm --network triage-assistant-prod_default -v "$(CURDIR)/tests/load:/load:ro" \
	    -e SESSION_COOKIE="$$session" -e ORIGIN="$$origin" \
	    locustio/locust:2.46.6 -f /load/locust/locustfile.py $(CLASS) --headless -u $(USERS) -r $(USERS) \
	    -t $(or $(DURATION),30s) --host http://web:8080 --only-summary ;; \
	  artillery) docker run --rm --network triage-assistant-prod_default -e ARTILLERY_DISABLE_TELEMETRY=true \
	    -e SESSION_COOKIE="$$session" \
	    -v "$(CURDIR)/tests/load:/load:ro" artilleryio/artillery:2.0.34 run /load/artillery/alerts.yml ;; \
	  jmeter) docker build -q -t triage-assistant-jmeter tools/load/jmeter >/dev/null && \
	    docker run --rm --network triage-assistant-prod_default -v "$(CURDIR)/tests/load:/load:ro" \
	    triage-assistant-jmeter -n -t /load/jmeter/alerts.jmx -Jcookie="$$session" ;; \
	  *) echo "usage: make load-tool TOOL=locust|artillery|jmeter  (k6: make load; all: make load-compare)"; exit 2 ;; \
	esac

# py-spy joins the api container's PID namespace with CAP_SYS_PTRACE; the
# api itself keeps cap_drop ALL. C= picks the container (dev: C=triage-assistant-api-1).
C ?= $(PROD_API)
PYSPY = docker run --rm --pid=container:$(C) --cap-add SYS_PTRACE
.py-spy-image:
	@docker build -q -t triage-assistant-py-spy tools/py-spy >/dev/null

py-spy-dump: .py-spy-image ## Stack of every api process right now: what is it doing, or stuck on?
	$(PYSPY) triage-assistant-py-spy dump --pid 1 --subprocesses

py-spy-top: .py-spy-image ## Live top-style view of where the api spends time (Ctrl-C to stop)
	$(PYSPY) -it triage-assistant-py-spy top --pid 1 --subprocesses

py-spy-record: .py-spy-image ## 30s flame graph -> docs/images/api-flame.svg (run load meanwhile)
	$(PYSPY) -v "$(CURDIR)/docs/images:/out" --entrypoint sh triage-assistant-py-spy -c \
	  'py-spy record --pid 1 --subprocesses --duration $${SECONDS_:-30} -o /out/api-flame.svg && chown $(shell id -u):$(shell id -g) /out/api-flame.svg'

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
