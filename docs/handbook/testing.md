# Testing

What each kind of test proves, how to run it, and which real bugs each
layer caught in this repo. All numbers are from the current `main`.

## The layers

| Layer | Runs against | Count | Command | Proves |
|---|---|---|---|---|
| api unit | nothing (pure Python) | 181 | `make test-fast` (~4 s) | logic that needs no I/O: worker sizing, cursors, SSE framing, error classification, retry/timeout budgets, every ID-token check against a fake provider, the role model, the eval harness's checks, gate and judge parsing, section splitting, citations, credential redaction, the prompt's version hash, the request span |
| api integration | a throwaway stack: real Postgres (with pgvector), PgBouncer, Valkey, Keycloak, mock LLM | 115 | `make test-api` (~40 s) | every endpoint's success and failure paths through the real drivers, pools and SQL; who sees what, through the api and at the database (row-level security); signing in through the real identity provider |
| web unit/component | jsdom (Vitest + React Testing Library) | 52 | `make test-web` | UI states, the sign-in gate, role-dependent UI, the SSE parser, error handling in the API client |
| end-to-end | the **production** stack in a real browser (Playwright, Chromium), over HTTPS through the TLS edge | 12 | `make prod-up && make e2e` | the parts only a real browser and the proxies show: signing in and out through Keycloak's page, two users seeing different alerts, a cross-site POST refused, incremental streaming through the edge and nginx, Stop cancelling the model call, CSP |
| image | the production image | 2 checks | `make image-check` | non-root, no dev tools, every module imports on a read-only root filesystem, and the operator CLI runs without touching the server's metrics directory |
| evals, plumbing | the mock, through the model and api targets | every CI run (in the integration suite) | `make test-api` | the eval harness, the service and the gate work end to end; isolation and injection cases hold with a canned model |
| evals, retrieval | the running service and its embedding model | 22 questions, before an embedding or retrieval change | `make evals a="--target retrieval"` | runbook search finds the section that answers: recall@k and MRR per mode ([RAG](rag.md)) |
| evals, quality | a real model (Ollama locally, or a provider) | 19 cases, before a prompt or model change | `make evals a="--judge --repeat 10 --baseline ..."` | answers stay grounded, refuse what the alerts do not say, resist injection, never cross teams: a pass rate per case ([AI engineering](ai-engineering.md)) |
| load | the production stack | on demand | `make load` | capacity, latency under load (performance chapter) |
| failure drills | the production stack | 22 drills | `make drills` | what users see when each dependency fails, and that it recovers (failure-modes chapter) |
| release smoke | the *published* images, amd64 and arm64 | every release | `scripts/smoke-release.sh <prefix> <tag>` | what was pushed runs: HTTPS with a verified chain, write, read, a streamed answer |

`make test` runs the api and web suites exactly as CI does. `make check`
also runs lint and types. Run it before every push.

**Coverage:** the api has **92.6% line+branch coverage**, the eval harness
included (gate: 85%, branch coverage on). The web has **100% of lines and 96.2% of branches** (gates:
85% lines, 80% branches). The api measures with
`concurrency = ["greenlet", "thread"]`: without it, coverage loses track
at SQLAlchemy's greenlet switches and reports the lines after an `await
session...` as never run (it read 89.7% for the same tests). The gates are floors, not goals: a line that
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
disk flush. Keycloak starts first and boots (~20 s) while the images
build and the migrations run. The 199 tests take ~24 s; with the stack
created and destroyed around them, `make test-api` takes ~40 s. `make
test-fast` (unit tests only, no services) takes ~4 s.

The api's settings are tuned for tests there: small rate limits (5 alert
requests per minute), a 1 s model read timeout, no retries, 0.2 s
heartbeats. That's how the failure-path tests stay fast.

Fixtures worth knowing (`apps/api/tests/integration/conftest.py`):

| Fixture | What it gives you |
|---|---|
| `app` | the real app with its lifespan (pools, clients), called in-process through httpx's ASGI transport; the data is reset before each test |
| `sign_in_as("team:payments:responder", email=...)` | a client signed in as a user whose identity provider sent those groups. Made by `sessions.sign_in`, the code the real callback runs, so every test goes through the real session, access and tenant code. A different `email` is a different user, with their own rate-limit budget |
| `client` | a responder in the `default` team: may read and create its alerts |
| `anonymous()` | a client with no session: for the 401 paths |
| `signed_in(app, ...)` | the same, for an app a test builds itself (other settings) |
| `with_database(url)`, `with_redis(url)` | settings pointing a dependency somewhere unreachable: outage tests |
| `blackhole_port` | a TCP server that accepts connections and never answers: what a frozen dependency looks like to a client |
| `mock_llm` | drives the mock provider's failure modes (429, 500, hang, drop mid-stream) and reads its counters: `streams_cancelled` proves a cancellation reached the provider |

### Signing in, in tests

Only `test_auth_flow.py` drives the identity provider: it plays the
browser against the test stack's Keycloak, submitting its login form and
following the redirects. It covers the whole flow and each refusal: a
replayed code, a callback from another browser, a mix-up `iss`, an open
redirect, sign-out at the provider, the provider down. Everything else
signs in with `sign_in_as`, in milliseconds.

The clients send `Origin: https://test` (the test stack's `PUBLIC_URL`),
as a browser does. CSRF tests remove it. In the browser tests, a
Playwright *setup project* (`specs/auth.setup.ts`) signs alice and bob in
once through Keycloak's page, and saves their cookies. Every other test
starts signed in (`storageState`); `test.use({ storageState: { cookies:
[], origins: [] } })` gives a signed-out one.

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
| the UI stuck on "Answering…" when a stream ended without `done` or `error` | a spinner forever | the blocking-client performance experiment, then a unit test |
| stale dependencies in the test stack: `docker compose run` never rebuilds an existing image | ModuleNotFoundError locally while CI (fresh) passed | noticed in a local run; `make test-api` now passes `--build` |
| database connections leaked forever after Postgres froze under load | 13 of 40 pool slots stuck | only the freeze-under-load drill: single-request tests never showed it |
| missing index on newest-first reads | p95 7 s at 40 req/s; 43–152 ms when tested alone | only a load test |
| a request-scoped database session would have held its connection for a whole streamed answer (FastAPI closes `yield` dependencies after the response is sent) | 20 slow answers would have emptied a worker's pool | a 20-line experiment before shipping; now an integration test checks the pool mid-stream |
| the operator CLI crashed in the production image: an empty `PROMETHEUS_MULTIPROC_DIR` still turns multiprocess mode on, pointed at a relative path on a read-only root | `OSError: Read-only file system` from `make load` | running it against the production stack; now `make image-check` |
| the first sign-in design doubled CPU per request and collapsed at 1,000 req/s (p95 1.07 s) | fine at low load, every functional test green | only an A/B load test |
| an identity provider outage was invisible: sign-in redirects came from cached metadata | no failed request anywhere | only the `idp-stop` drill; now a 30 s check, `/ready` and an alert |
| a reasoning model spent the whole output limit thinking and returned nothing, which the service reported as a successful, blank answer | an empty chat bubble, outcome `ok` | a quality eval run (3 answers in 5); now `llm_empty_answer` and a test |
| every judge check "passed" while the judge was failing every call (HTTP 400: it had inherited a parameter it does not support) | a green eval report | reading the report: the judge had no verdicts; now a missing verdict fails the run |
| prompt v4 answered "There are no alerts." with an alert in the list, 8 runs in 40 | a green eval report: the case's only check looked for "Paris" | reading the answers, then a stronger case; fixed in prompt v5 |
| prompt v4 printed its own instructions when an alert asked it to, 5 runs in 200 | 110 calls over 10 runs had all passed | only 200 runs of the one case |
| a one-team runbook search found 0 of 20 sections when teams were many (the vector index returned other teams' rows, then the filter removed them) | an assistant with no runbooks for that team, and no error | only a 50,000-row benchmark (`make bench-rag-filter`); fixed by an explicit team filter |
| after an embedding setting changed, "hybrid" search was keyword-only | slightly worse answers; every metric said hybrid | injecting one fault per retrieval stage; now reported as `keyword_only`, with an alert |
| the model repeated a password planted in an alert: 2 runs in 110 under prompt v6, 2 in 200 under a stronger wording | one failure in a 10-run eval, easy to dismiss as noise | the 10-run eval, then 100- and 200-run measurements; fixed by redacting credentials in code (0 in 200) |
| 22 api settings could not be set from `.env`: compose did not pass them to the container | a value in `.env` changed nothing, silently | reproducing an incident by changing one setting; now `make lint` checks every setting is listed |
| an unsampled request handed out a trace id: at 10% sampling, 9 in 10 found nothing in Jaeger | "trace not found" | measuring 10% sampling; now a unit test |
| with the trace backend down, each worker's shutdown waited 10 s for the exporter | slower deploys and restarts, and no error | stopping Jaeger, then the api, and timing it |
| a correction to a code comment was lost: reverting an experiment with `git checkout <file>` discarded it too, and the notes kept saying it had been made | a comment still claiming a cause the experiment had disproved | reading the code while adding tracing |
| redaction missed 37 of 54 secrets in the shapes logs carry them (`DB_PASSWORD=`, `"password": "..."`): `\bpassword` never matches after `_` | every unit test green: each tested a whole-word label | a corpus of real log shapes, scored against a held-out set (`evals/redaction.py`, now a unit test) |
| redacting 100,000 characters of "mysql " took 6.4 s, on the event loop: a scan restarted at every word | nothing, until someone writes such a runbook | timing hostile input while measuring redaction; now a linear-time unit test |
| an audit row could not be inserted: `INSERT ... RETURNING` is checked against the SELECT policy, which only org admins pass | HTTP 500 on every save | the first run through the dev api, before any test; now every write path has an integration test |

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
- **Access:** signed out → 401 (`anonymous()`); another team's resource
  → 404, the same as a missing one; a role too low → 403; a POST from
  another origin → 403 `csrf_failed`. `test_access.py` has the pattern:
  a cast of users, one per role.
- **Rate limit:** if it is limited, use a user of its own
  (`sign_in_as(..., email=...)`).
- **Metrics:** if it adds a metric, assert it moved and its labels are
  bounded (`test_metrics.py`).

Assert on behaviour the user sees (status, body, headers, what the next
request sees), not on internal calls.

## Flaky tests

Playwright runs with `retries: 0`, and nothing is retried in pytest. A
test that fails sometimes is reporting a race, in the test or in the
app. Both are bugs. Retrying hides the second kind.

A model's answers are the exception: they vary by design, so evals
measure a pass rate over repeated runs instead of retrying
([AI engineering](ai-engineering.md)).

Common causes met here:
- **Done-callbacks.** asyncio runs them one loop iteration after the
  task finishes: assert after `await asyncio.sleep(0)`.
- **Merged cancellations.** A second `task.cancel()` before the first is
  delivered merges into one: wait for evidence the first landed.
- **Shared rate-limit budgets** between tests: limits are per user, so
  give each test's writers their own email.

## CI

`.github/workflows/ci.yml` runs the same `make` targets:
- `lint` (ruff, mypy, obs-check, shellcheck)
- `test-api` (the throwaway stack plus the coverage gate, with the
  coverage figure in the job summary)
- `build` (`make image-check`)
- `web-build` (lint, tests, build, and the production nginx config test)
- `e2e` (`make prod-up`, which waits until every service is healthy,
  Keycloak included, then Playwright; the report is uploaded when it
  fails)

All five are required checks on `main`. If CI and your laptop disagree,
suspect state your laptop has and CI doesn't: stale images, an old
`.venv` volume (`make rebuild`), your UID. CI runs as UID 1001, which
has no account in the node image. That exposed a `HOME=/` problem the
local UID 1000 hid.
