# Testing

What each kind of test proves, how to run it, and which real bugs each
layer caught in this repo. All numbers are from the current `main`.

## The layers

| Layer | Runs against | Count | Command | Proves |
|---|---|---|---|---|
| api unit | nothing (pure Python) | part of 99 | `make test-fast` (~4 s) | logic that needs no I/O: worker sizing, cursors, SSE framing, error classification, retry/timeout budgets |
| api integration | a throwaway stack: real Postgres, PgBouncer, Valkey, mock LLM | part of 99 | `make test-api` (~28 s) | every endpoint's success and failure paths through the real drivers, pools and SQL |
| web unit/component | jsdom (Vitest + React Testing Library) | 25 | `make test-web` | UI states, the SSE parser, error handling in the API client |
| end-to-end | the **production** stack in a real browser (Playwright, Chromium), over HTTPS through the TLS edge | 5 | `make prod-up && make e2e` | the parts only a real browser and the proxies show: incremental streaming through the edge and nginx, Stop cancelling the model call, CSP |
| image | the production image | 1 check | `make image-check` | non-root, no dev tools, every module imports on a read-only root filesystem |
| load | the production stack | on demand | `make load`, `make load-compare` | capacity, latency under load (performance chapter) |
| failure drills | the production stack | 21 drills | `make drills` | what users see when each dependency fails, and that it recovers (failure-modes chapter) |
| fresh host | a clean Docker host (DinD) | 1 run | `make fresh-host-test` | the committed tree comes up from `.env.example` alone, serving HTTPS |
| release smoke | the *published* images, amd64 and arm64 | every release | `scripts/smoke-release.sh <prefix> <tag>` | what was pushed runs: HTTPS with a verified chain, write, read, a streamed answer |
| ACME rehearsal | the edge image against Pebble (Let's Encrypt's test CA) | on demand | `make acme-test` | automatic certificates: obtained over HTTP-01, served, kept across a restart |

`make test` runs the api and web suites exactly as CI does. `make check`
also runs lint and types. Run it before every push.

**Coverage:** the api has **95.9% line+branch coverage** (gate: 85%, branch
coverage on). The web has **99.1% of lines and 93.8% of branches** (gates:
85% lines, 80% branches). The gates are floors, not goals: a line that
ran is not a behaviour that was checked. Coverage finds code nothing
exercises; only assertions find wrong behaviour.

## How the integration suite gets real services

`make test-api` starts a separate compose project (`triage-assistant-test`,
`compose.test.yaml`): its own containers, network and volumes, so tests
never see dev data, and `down --volumes` deletes everything afterwards.
Before pytest runs:
- `alembic upgrade head` builds the schema from the migrations.
- `alembic check` fails if the models and the migrations disagree
  (someone changed a model and forgot the migration).

The test database runs with `fsync=off` and `synchronous_commit=off`.
Durability is useless for throwaway data, and every commit skips the
disk flush. The 99 tests take ~10 s; with the stack created and
destroyed around them, `make test-api` takes ~28 s. `make test-fast`
(unit tests only, no services) takes ~4 s.

The api's settings are tuned for tests there: small rate limits (5 alert
requests per minute), a 1 s model read timeout, no retries, 0.2 s
heartbeats. That's how the failure-path tests stay fast.

Fixtures worth knowing (`apps/api/tests/integration/conftest.py`):

| Fixture | What it gives you |
|---|---|
| `app`, `client` | the real app with its lifespan (pools, clients), called in-process through httpx's ASGI transport |
| `client_for("192.0.2.7")` | a client with its own `X-Forwarded-For`, so rate-limit tests don't share one budget |
| `with_database(url)`, `with_redis(url)` | settings pointing a dependency somewhere unreachable: outage tests |
| `blackhole_port` | a TCP server that accepts connections and never answers: what a frozen dependency looks like to a client |
| `mock_llm` | drives the mock provider's failure modes (429, 500, hang, drop mid-stream) and reads its counters: `streams_cancelled` proves a cancellation reached the provider |

## What not to mock

- **The database.** Tests run real SQL through asyncpg, SQLAlchemy and
  PgBouncer. A mocked session would have missed both big database bugs
  below.
- **The model provider's SDK.** The mock is an HTTP server speaking the
  OpenAI API (`tools/mock-llm`), so tests exercise the real SDK, its
  retries, its streaming and its exceptions. A Python mock of the SDK
  would test our assumptions about it, and one of those assumptions was
  wrong (below).
- **Time, when the test is about time.** Heartbeat and timeout tests use
  small real durations (0.2 s), not a fake clock. The bugs being hunted
  live in the interaction with the event loop.

Mock only what you cannot run: a third-party service without a sandbox,
or a failure you cannot produce any other way.

## Which layer caught which bug

Real incidents in this repo, and the layer that found them:

| Bug | What it looked like | Caught by |
|---|---|---|
| PgBouncer could not authenticate to Postgres (md5 vs SCRAM) | every query failed with "wrong password type"; PgBouncer's healthcheck stayed green | the first integration test that ran a query |
| a migration ran, logged success, and was rolled back | "relation alerts does not exist" on the first request | integration test, then the request id led to the traceback |
| the production image could not start: `import httpx`, but the openai SDK uses `httpx2`; httpx was a *dev* dependency, so dev and tests passed | api crash-looping in the prod stack | running the production image; since then `make image-check` in CI |
| the metrics directory existed only under gunicorn | any other process importing the app crashed | CI's image check (a local prod stack never showed it) |
| Stop re-submitted the question: React reused one `<button>` for Ask/Stop, and its type flipped during the click | two POSTs per Stop | **Playwright only**: the jsdom unit test passed before and after the fix |
| the UI stuck on "Answering…" when a stream ended without `done` or `error` | a spinner forever | the blocking-client performance lab, then a unit test |
| stale dependencies in the test stack: `docker compose run` never rebuilds an existing image | ModuleNotFoundError locally while CI (fresh) passed | noticed in a local run; `make test-api` now passes `--build` |
| database connections leaked forever after Postgres froze under load | 13 of 40 pool slots stuck | only the freeze-under-load drill: single-request tests never showed it |
| missing index on newest-first reads | p95 7 s at 40 req/s; 43–152 ms when tested alone | only a load test |

Each layer earns its place by finding a class of bug the layer below
could not.

## Writing tests for a new endpoint

The minimum (AGENTS.md):
- **Success path:** the status code, the body shape, and what was
  stored.
- **Validation:** a 422 with the field that failed.
- **Not found / conflict:** where the endpoint can return them.
- **Dependency down:** `with_database` / `with_redis` pointing nowhere,
  or `blackhole_port` for a hang. Assert the status (503) and the error
  code, not just "not 200".
- **Rate limit:** if it is limited, use `client_for` with a fresh IP.
- **Metrics:** if it adds a metric, assert it moved and its labels are
  bounded (`test_metrics.py`).

Assert on behaviour the user sees (status, body, headers, what the next
request sees), not on internal calls.

## Flaky tests

Playwright runs with `retries: 0`, and nothing is retried in pytest. A
test that fails sometimes is reporting a race, in the test or in the
app. Both are bugs. Retrying hides the second kind.

Common causes met here:
- **Done-callbacks.** asyncio runs them one loop iteration after the
  task finishes: assert after `await asyncio.sleep(0)`.
- **Merged cancellations.** A second `task.cancel()` before the first is
  delivered merges into one: wait for evidence the first landed.
- **Shared rate-limit budgets** between tests: use `client_for`.

## CI

`.github/workflows/ci.yml` runs the same `make` targets:
- `lint` (ruff, mypy, obs-check, shellcheck)
- `test-api` (the throwaway stack plus the coverage gate, with the
  coverage figure in the job summary)
- `build` (`make image-check`)
- `web-build` (lint, tests, build, and the production nginx config test)
- `e2e` (`make prod-up`, then Playwright; the report is uploaded when it
  fails)

All five are required checks on `main`. If CI and your laptop disagree,
suspect state your laptop has and CI doesn't: stale images, an old
`.venv` volume (`make rebuild`), your UID. CI runs as UID 1001, which
has no account in the node image. That exposed a `HOME=/` problem the
local UID 1000 hid.
