# Coding with agents

How to get clean code from an AI coding agent on this repository, and keep
it inside the architecture: which tools to use, when, how, and why. Each
"why" was measured here. The runs, the numbers and their limits are in
[testing_coding_skills.md](testing_coding_skills.md). Just the steps, for
any agent, on one page: [the agent playbook](agent-playbook.md).

## In one minute

- **`AGENTS.md` is the contract.** Every agent reads it. It holds what an
  agent cannot know by itself: this repository's commands, rules and traps.
- **The checks are the guard rails.** Tests, types, lint and the
  repository's own Semgrep rules fail the build, whichever agent or person
  wrote the code. `make check` is green before anything is called done.
- **Review one area at a time.** One review per area the change's promises
  rest on, then the whole diff once. Every review of the right area found
  each bug that once shipped here (12 of 12). Reviews of the whole PR found
  it too, but never ranked it in their top two (0 of 6).
- **Every mistake that gets through becomes a check.** Write a test, and a
  Semgrep rule when a code pattern shows the mistake. A rule that review
  keeps missing becomes a line in `AGENTS.md`.
- **Eight skills.** Five load by themselves at their moment: `debug`,
  `write-tests`, `full-stack-change`, `prove-it`, `two-pass-review`. Three
  wait to be asked: `/ponytail` for a scoped fix, `/hate` before committing
  to a plan, `/mandela` before trusting a number.
- **One kit, any team.** [The agent playbook](agent-playbook.md) is the
  framework: rules, skills, gates, steps, and how it is adopted and
  governed. `make agent-kit to=../other-repo` copies it.
- **Nothing installed, nothing paid.** A skill is Markdown copied into the
  repository. No plugin installers, no hooks from outside, no MCP servers
  that call out.

## What to use, at a glance

Everything here is free. The skills are Markdown in `.claude/skills/`:
three MIT-licensed copies, and five written here from ideas in MIT and
Apache-2.0 packs. The checks and scanners are open source, and the
scanners run from Docker images pinned by digest: Semgrep and gitleaks with
no network, Trivy fetching only its vulnerability database. The slash
commands come with Claude Code. Details and the evidence for each are
further down.

| Tool | Where it comes from | Cost | Use it |
|---|---|---|---|
| `AGENTS.md` | this repository (`CLAUDE.md` imports it) | free | always: every agent reads it |
| The area-review prompt | `AGENTS.md`, "Reviewing a change" | free | every PR: one review per area, then the whole diff once |
| `make check` | ruff, mypy, pytest (MIT); oxlint, Vitest (MIT); tsc (Apache-2.0) | free, open source | before calling a change done |
| `make semgrep` | Semgrep Community Edition (LGPL-2.1) with this repository's rules, offline | free, open source | runs in `make lint` and CI; add a rule when a bug class gets through |
| `make secrets-scan`, `make scan` | gitleaks (MIT), Trivy (Apache-2.0) | free, open source | CI runs both; locally before a release |
| `/ponytail` | `.claude/skills/ponytail/` (MIT) | free | a bug fix or a small change with a clear end |
| `/hate` | `.claude/skills/hate/` (MIT) | free | before committing to a plan, an RFC, an ADR or a new rule |
| `/mandela` | `.claude/skills/mandela/` (MIT) | free | before trusting an eval, a benchmark or a "we measured X" |
| `debug`, `write-tests`, `full-stack-change`, `prove-it`, `two-pass-review` | `.claude/skills/`, written here | free | they load by themselves: a bug, tests, an endpoint, schema or UI, a "done", a review |
| `make agent-kit` | `scripts/agent_kit.py`, standard library only | free | copying the kit into another repository; `make lint` checks every skill loads |
| `/simplify` | built into Claude Code | comes with it | after a fix works, before review |
| `/code-review` | built into Claude Code | comes with it | breadth on risky files: sign-in, redaction, access, migrations |
| `/security-review` | built into Claude Code | comes with it | access, sign-in, redaction; read it next to `AGENTS.md`'s security rules |
| This repository's MCP server | `app/mcp_server.py` | free | asking your editor about your team's alerts and runbooks |
| Playwright | Apache-2.0 | free, open source | a change you need to see in a browser; CI runs the e2e suite |
| The session-start hook | `.claude/hooks/session-start.sh` | free | runs by itself in Claude Code cloud sessions |

Tried and left out: Semgrep's generic rule packs (103 findings, none
real), "be careful" rules in `AGENTS.md` (no gain), paperthin's other 26
skills (untested, and about half run unasked), anything with an installer.
Read and left out as packs, October 2026: superpowers (a hook that forces a
skill before every reply), graphify (sends the code to an LLM provider),
caveman (an installer and hooks), ui-ux-pro-max, taste-skill and hallmark
(scripts, outside AI services, landing pages). Their best ideas are in the
five skills written here.

## What keeps an agent inside the architecture

Five layers, strongest first:

| Layer | What it is | How strong |
|---|---|---|
| Checks that fail the build | pytest (unit, integration, row-level security), mypy strict, ruff, oxlint, tsc, Vitest, Playwright; `make semgrep`; the redaction corpus; `production_problems` in `app/config.py`; evals for prompt, model and retrieval changes | a machine says no, every time |
| `AGENTS.md` (`CLAUDE.md` imports it) | commands, conventions, prohibitions, the access rules, traps | read on every task, but a line can still be missed |
| Where to look | `docs/code-map.md` (where), the ADRs (why), the handbook (how), `TECH_STACK.md` (what, and when to add it) | read when `AGENTS.md` points there |
| Review | area reviews (`AGENTS.md`, "Reviewing a change"), `/code-review`, `/security-review` | catches what no check encodes yet |
| Skills | `.claude/skills/`: prompts loaded on demand | shapes how one task is done |

Move a rule up a layer whenever you can. A line in `AGENTS.md` that a
pattern can check becomes a Semgrep rule. A review finding that comes back
becomes a test.

### How the layers grow

| When | Add | Example |
|---|---|---|
| An agent gets the same thing wrong twice | a line in `AGENTS.md`: specific to this repository, with its reason | "Never `db.add(AuditEvent(...))`: row-level security refuses an insert that reads its row back" |
| A bug class merged once, or a rule keeps being missed in review, and a code pattern shows it | a rule in `tools/semgrep/rules.yml`, with cases it must and must not match in `rules.py` | `redact-before-cut`, `compare-digest-on-str` |
| A behaviour must hold | a test that fails on the old code | `test_hostile_text_is_redacted_in_linear_time` |
| A decision someone will question later | an ADR (`docs/adr/`) | ADR-0017, hybrid search in Postgres |
| A new file | a line in `docs/code-map.md` | |

**Don't add rules that say "be careful".** We measured a 20-line block:
trace the callers, test first, try the edges, report what you didn't check.
It showed no gain: 12 of 12 fixes were right with it or without it, and
reviews took 19% longer. The agent already works that way.

## Which tool, when, how and why

### On every change

| Tool | How | Why |
|---|---|---|
| `AGENTS.md` | Claude Code, Codex, Copilot and Cursor read it on their own | this repository's rules are in context on every task |
| `make check` | before saying done: lint (Semgrep included), types, tests. In a cloud session, the host commands in `AGENTS.md`, then CI | the agent reports what it ran, with the output |
| Area reviews | the prompt in `AGENTS.md`, "Reviewing a change" | in the bug's area: found 12 of 12, top two 9 of 12. Whole PR: found 6 of 6, top two 0 of 6 |
| `make semgrep` | runs in `make lint`, the pre-push hook and CI | four of this repository's bugs and rules, checked on every push |

### When the task calls for it

| Tool | When | How | Why |
|---|---|---|---|
| `/ponytail` | a bug fix or a small change with a clear end | `/ponytail`, then the task | as correct, 2–4 times faster, a third of the diff. It skips doc updates: leave it off when the change moves something the handbook documents |
| `/simplify` | the fix works, before review | on your own diff, in your session | 3 suggestions on #110, 2 kept |
| `/code-review` | breadth on a file where a mistake is expensive: sign-in, redaction, access, migrations | from a session on the branch. To review another checkout, give absolute paths | found the shipped bug in 6 of 6 runs, but between 2nd and 9th of 15 findings: read the whole list |
| `/security-review` | access, sign-in, redaction, anything an outsider can send | from a session on the branch, the change committed, `origin/HEAD` set (`git remote set-head origin --auto`) | by design it drops denial-of-service findings, which `AGENTS.md` requires (bounded scans). Read it next to `AGENTS.md`'s security rules |
| `/hate` | before committing to a plan, an RFC, an ADR or a new rule | `/hate` on the document | it found the flaw in our own experiment (areas cut by someone who knew where the bug was). Its cheap test then held, 11 of 12 |
| `/mandela` | before trusting an eval, a benchmark or a "we measured X" | `/mandela` on the method | it named six leaks in our own measurements. All six are now stated as limits |
| `debug`, `write-tests`, `full-stack-change`, `prove-it`, `two-pass-review` | a bug; tests; an endpoint, a schema change or a UI; before "done"; a review | nothing: each loads by itself when the task matches its description | the moments where an agent most often cuts a corner. Not measured here yet: the playbook says how they will be |
| Evals | any change to `SYSTEM_PROMPT`, the model, retrieval or embeddings | the commands in `AGENTS.md`, under "Evals" and "Runbook search" | tests check the code; evals check the answers |
| This repository's MCP server | asking your editor about your team's alerts and runbooks | `claude mcp add ...`, as in [Agents](handbook/agents.md), "MCP" | read-only, acts with your rights, sends nothing out |
| Playwright, `/run` | a change you need to see in the browser | `make prod-up && make e2e`; `/run` on a laptop | catches what jsdom can't show |

### Not used, and why

| Tool | Why not |
|---|---|
| Plugin installers (`npx ...`), plugins with hooks, MCP servers that call out | company rule. A skill is a prompt that runs with your session's permissions: read it, then copy it in |
| "Work carefully" rules in `AGENTS.md` | no measurable change, slower reviews |
| Semgrep's generic rule packs | 103 findings here, none real |
| paperthin's other 26 skills | untested, and about half run without being asked |
| A whole-PR review alone, on a large PR | it found the deep bug but never ranked it in the top two (0 of 6) |
| AWS's MCP server, for app work | needs AWS credentials and calls AWS |

## From issue to merge

1. **Issue.** Describe the symptom, not your guess at the cause. A
   non-trivial change gets an RFC first (`docs/rfc/`): run `/hate` on it.
2. **Build.** Start the agent on the branch.
   - **A scoped fix:** `/ponytail`.
   - **A bigger change:** no skill. Ask for the docs `AGENTS.md` names: the code map, `TECH_STACK.md`, the handbook chapter, and an ADR for a decision.
   - **By themselves:** `debug` on a bug, `write-tests` for tests, `full-stack-change` on an endpoint, a migration or a page.
3. **Prove it.**
   - A test that fails on the old code.
   - `make check` green.
   - A prompt, model or retrieval change: the evals, before and after.
4. **Polish.** `/simplify` on the diff. Keep what reads better; drop what
   widens the PR.
5. **Review.** `two-pass-review` loads by itself: what the issue asked, then `AGENTS.md`, reported apart.
   - **A small PR (a few hundred lines):** one review.
   - **A large PR:** one review per area, then the whole diff once.
   - **Access, sign-in or redaction:** add `/security-review`.
   - **The riskiest files:** add `/code-review`.
   - **Every finding:** fixed or answered in the PR. The template has the line.
6. **Merge.** A person merges (squash) and owns what was merged.
7. **After.** A bug got through? Write a test, plus a Semgrep rule or an
   `AGENTS.md` line, so it can't get through again.

## Setting up

- **A laptop:**
  - `make setup && make up`, then `make hooks` for the check before each push.
  - Claude Code finds `AGENTS.md`, `.claude/settings.json` and `.claude/skills/` in the clone.
- **Claude Code on the web:**
  - The session-start hook installs the api's and the web's dependencies.
  - `uv run pytest tests/unit` and `npm test` run on the host.
  - The Docker images can't be built there, so `make check` runs in CI.
- **Adding a skill:**
  - Read all of it.
  - Copy its `SKILL.md` and its licence into `.claude/skills/<name>/`, or write your own from its ideas.
  - Note the source and the commit in `.claude/skills/README.md`.
  - Add `disable-model-invocation: true` unless it should run without being asked.
  - `make lint` (`scripts/agent_kit.py check`) fails if it wouldn't load.
- **Another repository:** `make agent-kit to=../other-repo` copies the kit; the playbook's "Adopt it" has the rest.

## Other agent tools: GitHub Copilot, Codex, Cursor

Most of this carries over, because most of it is plain files and CI. The
checks fail the build whatever wrote the code.

| Piece | Claude Code | GitHub Copilot | Codex, Cursor |
|---|---|---|---|
| `AGENTS.md` | through `CLAUDE.md` | the coding agent reads it (root and nested), and also `CLAUDE.md` and `.github/copilot-instructions.md` | Codex reads it. Cursor reads it alongside its own `.cursor/rules` |
| Tests, types, lint, Semgrep, CI | yes | yes | yes |
| `.claude/skills/` | `/name` | the coding agent, Copilot CLI and VS Code's agent mode load skills from `.claude/skills/` or `.github/skills/` | Codex loads skills from `.agents/skills/`: `python3 scripts/agent_kit.py copy . --codex` makes the copies, and `make lint` keeps them equal. Elsewhere, paste the text into the prompt |
| The area-review prompt | in `AGENTS.md` | the same, for the coding agent. Copilot code review reads `.github/copilot-instructions.md`; GitHub's docs disagree on whether it reads `AGENTS.md`. If you use it, copy the review section there | paste it |
| The session-start hook | `.claude/hooks/session-start.sh` | `.github/workflows/copilot-setup-steps.yml`, one job named `copilot-setup-steps` running the same two installs | the tool's own setup |
| This repository's MCP server | `claude mcp add` | the repository's Copilot coding agent settings, as JSON (`mcpServers`); secrets prefixed `COPILOT_MCP_`; tools only. It needs the stack and a session in the agent's environment, so for now: from VS Code, on a laptop | the tool's MCP settings |
| `/code-review`, `/security-review`, `/simplify` | built in | not there: ask Copilot to review the PR, or use the prompt | not there: use the prompt |

**What changes:**
- **Approval.** Copilot's coding agent calls MCP tools without asking.
  That is fine for this repository's two read-only tools. Never give it one
  that changes anything ([Rules for tools](handbook/ai-security.md#rules-for-tools)).
- **Where the code goes.** Every one of these tools sends code to its
  vendor's models. Use only the tools your company has approved for this
  repository.
- **What we measured** is Claude Code. The checks and `AGENTS.md` hold for
  any agent. How well another agent follows the review prompt, we haven't
  tested.

Sources, October 2026: GitHub's docs on
[custom instructions](https://docs.github.com/en/copilot/reference/custom-instructions-support)
and [AGENTS.md](https://github.blog/changelog/2025-08-28-copilot-coding-agent-now-supports-agents-md-custom-instructions),
[skills](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/create-skills),
[MCP](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/extend-cloud-agent-with-mcp)
and [code review](https://docs.github.com/en/copilot/tutorials/customize-code-review);
OpenAI's on [Codex skills](https://developers.openai.com/codex/skills).
