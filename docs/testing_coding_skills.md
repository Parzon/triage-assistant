# Testing coding skills, plugins and tools

Which agent skills, plugins, MCP servers and tools help work on this
repository, backend and frontend, and how we know. Each entry says what
it is, how we tested it, what happened, whether to use it, and how.
Tested in October 2026 on this repository and on two bugs from its
history. This is the evidence. For daily use, read
[Coding with agents](coding-with-agents.md): which tool, when, how and why,
in one page.

**The rule we work under:** no external plugins. A skill is a Markdown
prompt: read it, and if it earns its place, copy it into
`.claude/skills/<name>/SKILL.md` (with its licence). Nothing with an
installer (`npx ...`), a hook, an MCP server that calls out, or a request
to star a repository. What earned its place is now in the repository:
three skills in `.claude/skills/`, the Semgrep rules in `tools/semgrep/`
(run by `make lint` and CI), and a session-start hook in `.claude/`.

## At a glance

| Tool | Kind | Side | Verdict | Why |
|---|---|---|---|---|
| Area reviews (`AGENTS.md`, "Reviewing a change") | prompt | both | **Use on every PR** | found the shipped bug in 12 of 12 reviews of its area; blind slicing kept the bug in one area 11 of 12 times |
| `ponytail` | skill (prompt, MIT), in `.claude/skills/` | both | **Use for scoped bug fixes:** `/ponytail` | as correct, 2-4 times faster, a third of the diff; skipped the doc updates |
| "Work carefully" rules in `AGENTS.md` | prompt | both | **Don't add** | no measurable gain |
| `/simplify` | built into Claude Code | both | Use after a fix | 3 changes on #110, 2 kept |
| `/code-review` | built into Claude Code | both | **Use on risky files**, from the branch or with absolute paths | found the shipped bug in 6 of 6 runs, with a probe, but in lists of 15 (rank 2-9) |
| `/security-review` | built into Claude Code | backend | Use from a session on the branch; set `origin/HEAD` | never loaded in our test setup; its method by hand found the bug 4 of 4; drops findings our own rules require |
| paperthin `hate` | skill (prompt, MIT), in `.claude/skills/` | decisions | **Use before committing to a plan:** `/hate` | found the flaw in our own experiment |
| paperthin `mandela` | skill (prompt, MIT), in `.claude/skills/` | evals, experiments | **Use before trusting a measurement:** `/mandela` | six leaks in our own experiment; we agree with all six |
| paperthin, the other 26 skills | external skills | - | Not now | untested; its installer and hooks are not allowed |
| FastAPI's own skill | ships inside `fastapi` | backend | Reference only | the api already follows it, except where we chose otherwise |
| Semgrep with this repository's rules | scanner, in `make lint` and CI | backend | **In use:** `make semgrep` | 4 rules caught both shipped bug classes and one live bug |
| Semgrep's generic rule packs | scanner | both | Don't | 103 findings, none real |
| Trivy, gitleaks | scanners, in CI | both | Keep | found real Caddy CVEs this month; no secret in any commit |
| ruff, mypy, pytest | lint, types, tests, in CI | backend | Keep | the fixes' safety net (300 unit tests) |
| oxlint, tsc, Vitest | lint, types, tests, in CI | frontend | Keep | clean; 62 tests, 99% of statements |
| Playwright | browser tests | frontend | Keep in CI; smoke locally | drove the app in this sandbox; found the tab title "app" |
| This repository's MCP server | MCP, local | backend | Use for questions about alerts | handshake, two read-only tools, unknown tools refused |
| AWS's MCP server | MCP, remote | infra | Not for app work | needs AWS credentials and calls AWS |
| n8n's MCP server and skills | MCP | - | Not relevant | needs a running n8n; this project has none |
| `/run` | built into Claude Code | frontend | Use on a laptop | needs the dev stack's images |
| A session-start hook | Claude Code setting, in `.claude/` | both | **In use** | cloud sessions start without dependencies |
| `/init`, `/fewer-permission-prompts`, `/loop`, `claude-api` | built into Claude Code | - | Not needed or optional | see below |
| Docker compose and buildx plugins | Docker CLI plugins | both | Required | the stack and the releases run on them |
| `docker rollout` | Docker CLI plugin | ops | Not used | `deploy.sh` does it (ADR-0011) |
| The recommended VS Code extensions | editor | both | Recommended | `.vscode/extensions.json` |

## How we tested

- **Two real bugs from this repository's history**, both fixed in #87:
  - **The token leak:** alerts were cut to 300 characters before redaction, so the start of a token across the cut reached the model (it came in with #49).
  - **The first sign-in:** "keys never fetched" was `0.0` on a clock that counts from boot, so sign-in failed for a minute after a host booted (#40).
- **Fresh copies:** agents worked on copies of the repository at the commit in question, with no git history, so the shipped fix could not be looked up.
- **Fix tasks:** graded by tests the agents never saw, by whether the agent's own test fails on the old code, and by the unit suite, ruff and mypy.
- **Reviews:** graded by hand: is the shipped bug found, at what rank, and shown with a probe?
- **Repetition:** three runs per arm unless a section says otherwise, all on the same model.

**Limits.** We audited our own method with paperthin's `mandela` and `hate` (below), and kept what they found:
- one person chose the bugs, wrote the issue text knowing the cause, wrote the prompts and the hidden tests, and graded the reviews;
- every agent also had the current `AGENTS.md` in its context;
- two bugs, three runs each.

So read a difference smaller than about two times as noise, and a result
on these two bugs as a result on these two bugs.

## Reviewing a change

### Area reviews: the prompt in `AGENTS.md`

**Tested:** 18 reviews of the two PRs that shipped the bugs, not told a
bug existed, plus 12 blind splits of those PRs into areas.

| Review of | Found the shipped bug | In the top two | Shown with a probe | Cost per review |
|---|---|---|---|---|
| The area holding the bug (260-600 lines) | 12 of 12 | 9 of 12 | 12 of 12 | about 120,000 tokens, 7 min |
| The whole PR (6,500-8,500 diff lines) | 6 of 6 | 0 of 6 (3 called it minor) | 4 of 6 | about 250,000 tokens, 12 min |

The whole-PR reviews found what crosses areas instead: keyword search
stemming words twice (also fixed in #87), and a deploy that never
started the new identity provider.

**The objection, and its test.** `hate` pointed out that the areas above
were cut by someone who knew where the bug was. So agents split the two
PRs blind, from the rule's text alone, with no review:
- **The runbooks PR (#49):** all 6 splits put the cut-then-redact line in a "credential redaction" area of 184-233 lines.
- **The sign-in PR (#40):** 5 of 6 put both halves of the clock bug in one area of 547-597 lines. One split `oidc.py` across two areas, with the `0.0` in one and the comparison in the next.
- **The examples in `AGENTS.md`:** with the rule's example areas removed, the result was the same, 6 of 6.
- **Cost:** a blind split of a 5,000-6,000-line PR took about 210,000 tokens and 6 minutes. A person who knows the change splits it faster.

**Use it:**
- **Small PRs:** under a few hundred lines, one review is enough.
- **Large PRs:** split by the promises the change makes. Review each area with the `AGENTS.md` prompt, then read the whole diff once for what crosses areas.
- **Findings:** fix or answer each one in the PR. The PR template has the line.

### `/code-review` (built into Claude Code)

**Tested:** 7 runs on the same areas as the area reviews (#49's
`redact.py` and `triage.py`, #40's `oidc.py`). Each ran on a copy with
the PR as the uncommitted diff.

- **Where it looks:** the skill hands the review to an agent of its own, which starts in the session's repository.
  - **Relative paths fail:** the first run passed them and reviewed today's code in that repository, where the bug is long fixed.
  - **Absolute paths work:** given absolute paths into the copy, every run reviewed the PR's code.
- **Results, 6 runs with absolute paths:**
  - **Found:** the shipped bug in 6 of 6, each time shown with a probe.
  - **Rank:** each run returned 15 findings, with the bug at 2, 3 and 4 (redaction) and 4, 5 and 9 (sign-in). That puts it in the top two in 1 of 6, against 9 of 12 for the area-review prompt, whose lists ran to 4-9 findings.
- **Other findings were real too:**
  - On `oidc.py` all three runs found the non-ASCII nonce crash that #110 fixes, and two of three found the provider watcher dying on an unexpected error, which #110 also fixes.
  - It also found ones still open: `make revoke` matching on an email the provider can change, signing keys that never expire, and an endpoint with a query string breaking sign-in.
- **What leaks in:** its agent inherits the session's repository and instructions. In 1 of 6 runs it read the session's later code, where the bug is fixed, which spoils a test like ours. In real use, from a session on the branch under review, that is just the right code.
- **Cost:** 8-9 minutes, and about 65,000-75,000 tokens in the calling agent plus its review agent's own, which we couldn't count.

**Use it:**
- **When:** for breadth on a file where a mistake is expensive (sign-in, redaction, access, migrations), from a session on the branch.
- **How:** run `/code-review`, or name the area's files.
- **Read the whole list:** the shipped bugs sat between 2nd and 9th of 15.
- **For ranking:** the area-review prompt in `AGENTS.md` puts the worst problem first more often.

### `/security-review` (built into Claude Code)

**How it works:** when it loads, it takes the diff with
`git diff origin/HEAD...`. That runs in the folder the session started
in, on committed changes only.

**Tested:** 4 times on the runbooks PR. It did not load once:
- **Pilot:** our copy had no `origin` remote.
- **The other three:** the diff step ran in the session's own folder, not the copy under review. That folder has no `origin/HEAD` in this cloud checkout, and at one point wasn't a git repository at all.

Each time, the agent followed the skill's method by hand: gather the diff,
review the security areas, filter by confidence, and drop the excluded
kinds. That found the redaction bug in all 4 runs, ranked fourth of four,
second of two, inside the top finding, and second of two.
On the sign-in PR (1 run) it didn't report the clock bug. That is
an availability bug, which the skill leaves out by design. It did report
`make revoke` matching users on an email the identity provider can
change, which a whole-PR review had also found.

**Its rules and ours disagree.** Its exclusions drop denial of service.
They dropped the quadratic regular expressions in `redact.py`, which
`AGENTS.md` makes a requirement (bounded scans, and a test that hostile
input stays linear). So `/security-review` alone would pass a change our
own rules block.

**Use it:**
- **From the right place:** run it from a session opened on the repository, on the branch, with the change committed.
- **`origin/HEAD`:** check it exists (`git symbolic-ref refs/remotes/origin/HEAD`). If not, set it with `git remote set-head origin --auto`.
- **Coverage:** for access, sign-in, redaction and anything an outsider can send. Read its report next to `AGENTS.md`'s security rules; it doesn't know them.

### `/simplify` (built into Claude Code)

**Tested:** on PR #110 (the redaction and sign-in fixes, 111 lines), in a copy.

**What it did:**
- **Proposed three changes:**
  - name the alert line's prefix instead of a bare `+ 2`;
  - reuse the alert test helper instead of eight hand-written lines;
  - move a constant-time comparison helper from `sessions.py` into `oidc.py` and share it.
- **Listed what it considered and rejected,** each with a reason.
- **Kept everything passing:** tests, ruff and mypy.
- **Two of its changes went into #110.** We left the third out: it moved a function across modules to remove two lines.
- **Ran as one pass:** run inside an agent, it can't start its own four reviewers.

**Use it:** after a fix works, from your own session, on your own diff.
Take what reads better; leave changes that widen the PR.

## Ways of working

### `ponytail` (github.com/DietrichGebert/ponytail, MIT)

A prompt: the smallest complete change, reuse before writing, and a short
reply naming what was skipped.

**Tested:** the two fix tasks, 3 runs each, with the skill invoked, against
the 3 runs each without it.

| | Without | With `ponytail` |
|---|---|---|
| Right cause, hidden tests pass, own test fails on the old code | 6 of 6 | 6 of 6 |
| Time (token leak, first sign-in) | 397 s, 199 s | 95 s, 86 s |
| Tokens | 127,000, 99,000 | 76,000, 78,000 |
| Lines changed in code + tests | 39, 45 | 14, 15 |
| Doc lines changed | 19 (redaction's cost, in the security chapter), 0 | 0, 0 |
| Reply length (words) | 636, 408 | 345, 292 |

**What it gave up.** On the token leak, the runs without it updated the
security chapter: redacting whole alerts changes the cost the chapter
quotes. All three runs without it reported that an alert's source
reached the model unredacted; #110 fixes that. Of the `ponytail` runs,
one reported it. All three named a smaller gap in runbook sections, and
none updated a document.

**Use it:**
- **In the repository:** `.claude/skills/ponytail/`, with its licence. Upstream runs it on every coding task; here it runs only when asked (`disable-model-invocation: true`).
- **Scoped fixes:** run `/ponytail` for a bug fix or a small change with a clear end.
- **Bigger changes:** leave it off when the change moves behaviour the handbook documents, or when you want the neighbouring problems found.
- **Then check:** `git grep` the docs for what changed.

### "Work carefully" rules in `AGENTS.md`

**Tested:** a 20-line block was added to `AGENTS.md` and compared with none, 3 runs per arm:
- **What the block said:** trace the callers, start with a failing test, try the edges, keep comments true, report what you didn't check.
- **Fixes:** 12 of 12 were correct either way, and the block cost up to 2% more tokens.
- **Reviews:** with the block, 4 of 6 ranked the bug in their top two; without it, 5 of 6. The block also made reviews take 19% longer.

**Verdict:** don't add rules like these. The agent already works that
way. `AGENTS.md` is for what it can't know: this repository's commands,
rules and traps.

### paperthin (github.com/lilmgenius/paperthin, MIT): `hate`, `mandela`

28 skills, all Markdown. We tested the two that fit this team's work,
as plain prompts: the agent read the skill and followed it, with nothing
installed.

- **`hate`:** attack a plan and return the one objection that could sink it, plus the cheapest test of it.
  - **On our "review each area" rule:** its objection was that the areas had been cut knowing where the bugs were.
  - **Its test:** split blind. We ran that test (above) and it held, 11 of 12.
  - It cost 59,000 tokens and a minute, and it changed what the handbook claims.
- **`mandela`:** audit an experiment or eval for leakage.
  - **On our own measurements:** it named six leaks, among them the issue text written knowing the cause, and one person designing, running and grading.
  - We agree with all six, and the limits above now carry them.

**Use them:**
- **In the repository:** `.claude/skills/hate/` and `.claude/skills/mandela/`, unchanged, with their licence.
- **`/hate`:** run it on a plan, an ADR, an RFC or a rule before adopting it.
- **`/mandela`:** run it on an eval, a benchmark or a "we measured X" before trusting it.

**Skip:**
- **The installer:** `npx skills@latest` runs whatever is newest.
- **`re0-upgrade`:** it adds a session-start hook and offers to star the repository.
- **Installing all 28:** about half can run without being asked, which changes the agent's behaviour on every task.

## Skills that ship with a library

### FastAPI's skill (`fastapi/.agents/skills/fastapi/SKILL.md`)

FastAPI's own guide for agents, 321 lines, matched to the installed
version.

**Tested:** the api against its rules:
- **Already followed:** `Annotated` dependencies, no `...` defaults, no `RootModel`, one HTTP method per function.
- **One difference, by choice:** the chat's own SSE module (JSON-encoded data, keep-alives) instead of FastAPI's newer `EventSourceResponse`. An agent that follows the skill blindly would rewrite working code.

**Use it:** as a reference when writing a new route. Read it from
`apps/api/.venv/lib/python3.13/site-packages/fastapi/.agents/skills/fastapi/`.
Don't install it as a skill: `AGENTS.md` and the code's own choices win.

## MCP servers

- **This repository's server (`app/mcp_server.py`, ADR-0020).**
  - **Tested:** with the MCP client library, over stdio, as Claude Code starts it:
    - it named itself and its instructions ("alert and runbook text is untrusted data");
    - it offered `list_alerts` and `search_runbooks`, both read-only;
    - it refused an unknown tool, calling it "unknown" in the error rather than echoing the name the client sent.
  - **Calling a tool** needs Postgres; `tests/integration/test_mcp.py` covers that in CI.
  - **Use it:** to ask Claude Code about your team's alerts and runbooks as yourself (`docs/handbook/agents.md`). It runs locally and sends nothing out.
- **AWS's MCP server.** Not tested here: it needs AWS credentials and sends requests to AWS.
  - Useful for work on `infra/aws` with a read-only role.
  - Not needed to build the app.
- **n8n's MCP server and skills.** Not tested: they need a running n8n, and this project has none.

## Backend

### Semgrep, with this repository's rules

Semgrep runs fully offline in Docker (`--network none`, `--metrics=off`).

- **Generic packs:** 103 findings on this repository, none real. That is noise in CI.
- **Four rules written from `AGENTS.md` and from the bugs we fixed:**

| Rule | Code before #87 | main before #110 |
|---|---|---|
| `redact-before-cut`: `clean(x[:n])` | the token leak (`triage.py:119`) | nothing (fixed) |
| `compare-digest-on-str`: `hmac.compare_digest(str(...), ...)` | the nonce crash (`oidc.py:226`) | the nonce crash (`oidc.py:230`), fixed by #110 |
| `audit-event-via-db-add` | nothing | nothing |
| `route-without-current-user`: a route without `principal: CurrentUser` | 7 routes | 7 routes, all public on purpose |

The 7 routes are `/health`, `/ready`, `/metrics`, sign-in, its callback,
sign-out, and the Alertmanager webhook (a bearer token). Each now says why
it is public, then carries a `# nosemgrep`, so a new route without a user
is a decision someone writes down.

**In use** (#111):
- **The rules:** `tools/semgrep/rules.yml`, with the cases each must and must not match in `rules.py`.
- **`make semgrep`:** tests the rules against their cases, then scans `apps/api`. Offline, from an image pinned by digest. About 7 seconds.
- **Where it runs:** `make lint`, the pre-push hook and CI's lint job.
- **Checked both ways:** an expectation flipped in `rules.py` fails the test; an exemption removed fails the scan.

**Use it:**
- **Rules:** keep only rules that encode a rule this repository already has, or a bug it already had. A new bug class can add one, with a case it must match and one it must not.
- **Exemptions:** a comment with the reason, then `# nosemgrep: <id>` on the line above the code.

### Trivy and gitleaks (in CI, ADR-0022)

- **Trivy:** this month the weekly scan failed on the web and edge images. Bumping both base images fixed most of the findings. Three CVEs in Caddy have no fixed release yet, so they are accepted in `.trivyignore.yaml`, with an expiry.
- **gitleaks:** finds no secret in any commit. The made-up passwords in tests pass it.

Keep both as they are.

### ruff, mypy (strict), pytest

The safety net under every change above:
- **ruff:** lint and format.
- **mypy:** strict types.
- **pytest:** 300 unit tests in seconds; integration tests against Postgres in CI.

Without Docker, the unit tests run with `uv run pytest tests/unit` in
`apps/api`, with no settings in the environment: `tests/conftest.py` gives
the ones they need.

## Frontend

- **oxlint 1.85, tsc, Vitest with React Testing Library.**
  - **Results:** run on main: no lint finding, no type error, 62 tests passing, 99% of statements covered.
  - **Node:** the repository pins Node 24 (`apps/web/.nvmrc`); they also pass on Node 22.
- **Playwright.**
  - **End-to-end suite:** needs the production-shaped stack (`make prod-up && make e2e`); CI runs it on every PR.
  - **Local smoke:** we drove the dev server with Playwright and the preinstalled Chromium. Point `executablePath` at `/opt/pw-browsers/chromium` when the Playwright version expects a newer browser.
    - **With the api down,** the page says "Loading…" and then, after its retries, "Could not reach the service: Bad Gateway".
    - **The tab was titled "app"** (`apps/web/index.html`). Fixed; the e2e suite now checks the title.
- **`/run` (built into Claude Code).** It starts the app to see a change working.
  - **On a laptop:** it works with `make up`.
  - **In a cloud session:** the images can't be built here, so use the Vite dev server and Playwright instead.

## The environment

- **A session-start hook (made with the `session-start-hook` skill).**
  - **The problem:** cloud sessions start without `uv sync` or `npm ci`, so for these tests we had to write a runner that sets the settings by hand.
  - **In use** (#111): `.claude/settings.json` runs `.claude/hooks/session-start.sh`. In a cloud session it installs both apps' dependencies, in about 4 seconds; elsewhere it does nothing.
  - **What it doesn't do:** export placeholder settings. Compose reads `LLM_*`, `PUBLIC_URL` and `OIDC_*` from the shell, so they would override `.env`. The unit tests get theirs from `tests/conftest.py` instead.
- **`/init`:** not needed; `CLAUDE.md` and `AGENTS.md` already say more than it would write.
- **`/fewer-permission-prompts`:** for people running Claude Code locally who tire of approving read-only commands. Optional.
- **`/loop`:** repeats a prompt on a timer, for example watching CI. Optional.
- **`claude-api`:** not relevant; the assistant talks to any OpenAI-compatible model.
- **Docker's compose plugin (v2+) and buildx.**
  - **Compose:** runs every stack. Here it is v5.1.
  - **Buildx:** builds the release images for amd64 and arm64.
  - Both are required.
- **`docker rollout`:** not installed and not needed; `deploy.sh` does a rolling deploy itself (ADR-0011).
- **VS Code:** the recommended extensions are Python, debugpy, Ruff, Docker and EditorConfig. `make debug-up` attaches the debugger (`docs/handbook/operations.md`). We couldn't test them here; nothing in this report argues against them.

## Which to use, by task

| Task | Backend | Frontend |
|---|---|---|
| Fix a reported bug | Hand the agent the symptom, not your guess. `ponytail` for a scoped fix. Check that its new test fails on the old code. | The same, plus Vitest for the component |
| Review a PR | One review per area with the `AGENTS.md` prompt, then the whole diff once. `/code-review` on the risky files. `/security-review` from a session on the branch, for access, redaction or sign-in. `make semgrep` (in `make lint`). | The same areas. Playwright's e2e in CI |
| Polish a working diff | `/simplify` | `/simplify` |
| Decide something | `hate` on the plan or ADR | `hate` |
| Trust a number | `mandela` on the eval or benchmark | `mandela` |
| Ask about live alerts from your editor | This repository's MCP server | - |
| Check before pushing | `make check` (ruff, Semgrep, mypy, pytest); `make secrets-scan` | oxlint, tsc, Vitest (in `make check`) |

The same choices, as one workflow from issue to merge, and how each layer
keeps an agent inside the architecture: [Coding with agents](coding-with-agents.md).
