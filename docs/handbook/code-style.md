# Code style and engineering standards

How code is written here, and why. The linters enforce what they can
(`make lint`, `make typecheck`). This chapter covers what they cannot:
- where logic goes;
- when to use a class;
- what a comment is for;
- how people and AI coding agents work together on the code.

Each rule points at the code that follows it.

## Functions or classes

**Decisions are pure functions; I/O stays at the edges.** A function
that decides something takes plain values and returns plain values.
Routes and clients do the reading and writing, then call it.

| Decision | Pure function | Called from |
|---|---|---|
| what a user's groups grant | `access_from_claims` (`app/access.py`) | sign-in |
| what Postgres's row-level security sees | `tenant_settings` (`app/access.py`) | every authenticated request |
| what the model is shown | `build_messages` (`app/triage.py`) | the chat route, the evals |
| whether an answer passes | `score` (`evals/checks.py`) | the eval runner |

These need no database, network or mocks to test. That is why 181 unit
tests run in 3 seconds, and why the access model is tested case by case
(`tests/unit/test_access.py`).

**A class when something has state and a lifecycle.** For example,
something that holds a connection or a cache, and must be opened and
closed:

| Class | Its state | Its lifecycle |
|---|---|---|
| `OIDCClient` | the provider's metadata and signing keys, refetched at a limited rate | created at startup, closed at shutdown |
| `OpenAICompatibleClient` | an HTTP connection pool | `aclose()` |
| `Judge` | how often it was asked, which verdicts are missing | one eval run |
| `ApiTarget` | the run id, the case counter | one eval run |

Not a class: a namespace for functions (a module is one), or a value
with no behaviour (a dataclass is one).

**Values are frozen dataclasses:** `Principal`, `TeamAccess`, `Usage`,
`Finish`, `Case`, `Answer`. Frozen means a principal cannot be widened
by accident halfway through a request.

**Untrusted data meets Pydantic at the boundary:** request and response
bodies (`app/schemas.py`) and settings (`app/config.py`). Inside, the
types are already checked.

**Seams are Protocols.** `LLMClient` and `Target` say what a
collaborator must do, not what it must inherit from. Tests pass fakes
that merely have the right methods (`FakeLLM`, `FakeProvider`,
`ScriptedLLM`).

**Closed sets are enums or `Literal`s.** Roles are an `IntEnum`, because
their order *is* their meaning: `Role.RESPONDER >= Role.VIEWER`. String
sets the type checker should police are `Literal`s (`RoleName`).

## Errors

- **A failure has a stable code and a human message.**
  - `ApiError(403, "csrf_failed", "...")`;
  - `LLMTimeout.code == "llm_timeout"`;
  - OIDC errors: `login_failed`, `idp_unavailable`.

  Clients, metrics and alerts key on the code. The message can change
  freely.
- **One error shape** for every response:
  `{"error": {"code", "message", "request_id"}}` (`app/errors.py`). An
  unexpected exception becomes a generic 500, logged with its
  traceback, never returned.
- **Catch what you can handle, where you can handle it.** A dependency
  that is down maps to a 503 in one place (`DATABASE_UNAVAILABLE`), not
  in every route.
- **Fail loudly at startup** rather than oddly at runtime. Settings are
  validated with bounds, and an empty `OIDC_CLIENT_SECRET` refuses to
  start.

## Async

- **Never block the event loop.** One synchronous call made `/health`
  take 4.8 s under load. ruff's `ASYNC` rules catch the obvious cases,
  and `event_loop_lag_seconds` catches the rest.
- **Every wait is bounded:** connect, read, pool checkout, the whole
  stream (ADR-0010).
- **Background work is supervised.** `watch_db_pool` and
  `watch_identity_provider` start with the app and stop with it.
  Nothing is started with a bare `create_task` and forgotten.
- **Cancellation is normal.** A user closing the tab cancels the stream.
  `finally` blocks close the provider connection, which stops the
  billing (`answer_events`).

## Types, names, comments

- **mypy strict** on `app/` and `evals/`. `Any` only at JSON
  boundaries. Every `# type: ignore[...]` names its error code, and
  says why when the reason is not obvious.
- **Names say what, fully:** `newest_alerts`, `require_role`,
  `session_cookie`. Name modules by responsibility (`access.py`,
  `queries.py`), never by kind (`utils.py`, `helpers.py`).
- **Configuration lives in `app/config.py`**, typed and bounded, with a
  comment giving the measurement behind each default. Nothing else
  reads `os.environ`. The one exception is the operator CLI, which
  must adjust its environment before importing the app.
- **Comments say why, not what.** The best ones record the measurement
  or incident that forced the code:
  - "measured: a request-scoped session held its connection for the
    whole stream";
  - "gpt-oss spent all 800 tokens thinking in 3 answers of 5".

  A comment that restates the code gets deleted. So does commented-out
  code: git remembers it.
- **Docstrings** on modules, classes and public functions say what the
  thing guarantees. `app/access.py` opens with the whole access model.

## Tests

- **Named for the behaviour:**
  - `test_a_case_passes_only_if_every_repeat_does`;
  - `test_the_assistant_only_sees_the_askers_alerts`.

  A failing test name should read as the bug report.
- **Assert what the user sees:** the status, the error code, the rows
  returned. Not which internal function was called.
- **Fakes over mocks.** A fake is a small working implementation
  (`FakeLLM` streams scripted text). A mock records calls, which ties the
  test to the implementation.
- **Real services in integration tests:** Postgres, PgBouncer, Valkey
  and Keycloak in a throwaway stack. The failures that mattered here
  lived in the drivers and the pooler (the testing chapter's "Which
  layer caught which bug").

## TypeScript and React

- **Function components and hooks.** The only class is `ApiError`, an
  `Error` subclass, which is what classes are for in TypeScript.
- **Server state in TanStack Query, local state in `useState`.** There is
  no global store: almost everything on screen is server state.
- **One typed API client** (`src/lib/api.ts`). Components never call
  `fetch`. Failures arrive as `ApiError`, with a code and the request id
  the user can report.
- **Accessible by construction.** Tests find elements by role and label
  (`getByRole('button', { name: 'Ask' })`), as a screen reader would. A
  control that a test cannot find that way, a person may not either.
- **Strict TypeScript, no `any`.**

## SQL and migrations

- **Parameters are always bound.** String-built SQL is a lint error
  (ruff `S608`).
- **Indexes are chosen from the queries.** A missing index gave p95 7 s
  at 40 req/s; alone, the same query took 43–152 ms.
- **Migrations are generated, then read.** Expand/contract: the running
  release must work on the new schema (ADR-0015).
- **Team data gets row-level security in the same migration** as its
  table (ADR-0014, AGENTS.md).

## Shell

- `set -euo pipefail`, and shellcheck-clean (`make lint`).
- **Quote every expansion.** Test with `if`, because `! cmd` is exempt
  from `set -e`.
- **Scripts developers run must work on bash 3.2** (macOS): no `mapfile`,
  no associative arrays. Host-only scripts (`deploy.sh`) may use bash
  4.
- **`sed -i.bak`** works with both GNU and BSD sed; a bare `sed -i` does
  not.

## Commits and pull requests

The daily-work chapter has the workflow:
- Conventional Commits;
- a branch per issue;
- squash merge;
- `make check` before pushing.

A good PR description says:
- what changed, and why;
- **how it was verified**, with the commands and the numbers;
- one line per new dependency;
- what the reviewer should read first.

A claim with no command behind it is a hope.

## Rules for AI engineering teams

### Building AI features

1. **The prompt is code.** It is reviewed, versioned (the history in
   the comment above `SYSTEM_PROMPT`), and changed only with eval runs
   before and after (ADR-0016).
2. **The model sees only what the asker may see.** Its context comes
   from the same visibility query as the UI, never a wider one
   (ADR-0013).
3. **Model output is untrusted.** Render it as text; never execute it,
   and never feed it to a tool without a human confirming.
4. **The model has no tools until each action has a confirmation step**
   and a least-privilege account behind it.
5. **Measure answer quality, with a calibrated judge,** and gate
   releases on safety cases. A demo that looks good is not a
   measurement ([AI engineering](ai-engineering.md)).
6. **Every model call is bounded:** rate limits, output tokens, stream
   duration. The cost is on a dashboard.
7. **Log metadata, not content:** lengths, timings, outcomes, token
   counts. Never the question or the answer.

### Working with AI coding agents

1. **`AGENTS.md` is the contract.** It gives commands, conventions and
   prohibitions, and agents such as Claude Code and Codex read it. When
   an agent gets something wrong twice, the fix belongs in `AGENTS.md`.
2. **An agent runs `make check` before saying it is done**, and reports
   what it ran, with the output.
3. **Review agent code like a capable newcomer's code:**
   - read the tests first;
   - check the failure paths and the timeouts;
   - check that nothing was weakened to make a test pass.
4. **Never let an agent delete or loosen a test, an eval case or a
   calibration label** to get green. A failing check is information.
5. **Small PRs, one concern each.** Agents produce plausible code fast.
   A large diff hides the one wrong line.
6. **No secrets in prompts or chats.** A key pasted into a conversation
   is burned: rotate it. Agents read `.env.example`, never `.env`.
7. **A human merges, and owns what was merged.** An agent's confidence
   is not verification. The commands and numbers in the PR are.
8. **Destructive commands need a human:** deleting volumes, force
   pushes, dropping data, anything against a shared environment.
