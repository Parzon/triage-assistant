# Agent playbook

The steps every change follows, whichever coding agent writes it: Claude
Code, GitHub Copilot, Codex, Cursor. One page, to copy to any team. The
reasons and measurements are in [Coding with agents](coding-with-agents.md)
and [testing_coding_skills.md](testing_coding_skills.md). Everything here
is free, and nothing is installed from outside the company.

## What each agent reads

| | Claude Code | GitHub Copilot | Codex | Any other agent |
|---|---|---|---|---|
| The rules, `AGENTS.md` | yes (`CLAUDE.md` imports it) | yes | yes | tell it to read `AGENTS.md` |
| The skills, `.claude/skills/` | yes, as `/ponytail`, `/hate`, `/mandela` | yes: coding agent, CLI, VS Code agent mode | from `.agents/skills/`: copy them there | as a prompt (below) |
| CI | runs on every PR, whatever wrote the code | same | same | same |

A skill is a Markdown file. Where a tool cannot load it, the same words
work as a prompt: *"Read `.claude/skills/ponytail/SKILL.md` and follow it
for this task."*

## The steps

**1. Before you start**
- Write the issue as the symptom, not your guess at the cause.
- A bigger change gets an RFC first (`docs/rfc/`). Attack it with **hate**
  before you commit to it: `/hate`, or *"follow
  `.claude/skills/hate/SKILL.md` on this plan"*. It returns the one
  objection that could sink the plan, and the cheapest test of it.

**2. Build**
- A bug fix or a small change with a clear end: **ponytail** (`/ponytail`).
  The smallest complete change, and a short reply. Leave it off when the
  change moves behaviour the handbook documents: it skips doc updates.
- A bigger change: no skill. Ask for the docs `AGENTS.md` names: a line in
  `docs/code-map.md`, a `TECH_STACK.md` entry, the handbook chapter, an ADR
  for a decision.
- Ask for a test that fails on the old code before the fix.

**3. Prove it: the checks**

| Check | Run it locally | CI job |
|---|---|---|
| Lint, format, shell scripts, the repo's own rules (Semgrep) | `make lint` | `lint` |
| Types | `make typecheck` | `lint` (mypy), `web-build` (tsc) |
| Tests | `make test` (all), `make test-fast` (unit, seconds) | `test-api`, `web-build` |
| Leaked secrets, known vulnerabilities | `make secrets-scan`, `make scan` | `security` |
| The production image | `make image-check` | `build` |
| The browser, through the real stack | `make prod-up && make e2e` | `e2e` |
| A prompt, model or retrieval change | `make evals ...`, before and after | none: the numbers go in the PR |
| An upgrade on the database path (asyncpg, SQLAlchemy, PgBouncer) | `make drills d="db-freeze pgbouncer-freeze ..."` | `Drills`, run by hand on the branch: compare its table with the matrix in `docs/handbook/operations.md` |

`make check` (lint, types, tests) is green before anything is called done.
In a cloud session without Docker: the host commands in `AGENTS.md`.

**4. Tidy**
- Claude Code: `/simplify` on your diff. Elsewhere: *"Simplify this diff:
  reuse what exists, keep the behaviour, list what you rejected and why."*
- Keep what reads better; drop what widens the PR.

**5. Security pass**, when the change touches access, sign-in, secrets or
anything an outsider can send:
- Claude Code: `/security-review`, from a session on the branch with the
  change committed (if it can't find `origin/HEAD`:
  `git remote set-head origin --auto`).
- Any agent: *"Check this diff against the Security considerations in
  `AGENTS.md`, one rule at a time."* `/security-review` skips
  denial-of-service findings; our rules (bounded regular expressions) don't.

**6. Review**, on every PR, with this prompt (the rules in `AGENTS.md`,
"Reviewing a change"):

> Review *[the area: files or a few hundred lines]* of this change. Show
> each finding with a probe or a test you ran, not a reading; probes go in
> a scratch folder, and leave the repository as you found it. List the
> most severe first: where, what goes wrong and when, how sure you are. A
> short list of real problems beats a long one; say so if there are none.

- **Small PR** (a few hundred lines): one review.
- **Large PR:** one review per area the change's promises rest on
  (redaction, sign-in, access, migrations), then one over the whole diff
  for what crosses areas.
- **Claude Code, for breadth on the riskiest files:** `/code-review`. Read
  its whole list: the real bug sat anywhere from 2nd to 9th of 15.

**7. The PR**
- Fill in the template (`.github/pull_request_template.md`): what and why,
  how you verified it (commands and numbers), the checklist.
- Every review finding is fixed, or answered in the PR.
- CI green. A person merges (squash) and owns what was merged.

**8. Before trusting a number**
- An eval, a benchmark, a "we measured X": **mandela** (`/mandela`). It
  finds where the result could have leaked in.

**9. After a bug gets through**
- A test that fails on the old code.
- A Semgrep rule in `tools/semgrep/rules.yml` when a code pattern shows
  the mistake, with a case it must and one it must not match in `rules.py`.
- A line in `AGENTS.md` when an agent gets the same thing wrong twice.
- Never a "be careful" line: measured, it showed no gain.

**Never**
- Weaken, skip or exempt a test, an eval case or a scan finding to get green.
- Paste a secret into a chat. If you did, rotate it.
- Install a plugin from outside the company, or a skill you haven't read.
- Let an agent merge.

## Share it across teams

**What a team copies, in one PR:**

| File | What it is | Who reads it |
|---|---|---|
| `AGENTS.md` | the team's own rules, plus the "Reviewing a change" section | every agent |
| `CLAUDE.md` containing `@AGENTS.md` | points Claude Code at `AGENTS.md` | Claude Code |
| `.claude/skills/` | ponytail, hate, mandela, their licences and sources | Claude Code and Copilot; Codex from `.agents/skills/` |
| `docs/agent-playbook.md` | these steps | people, and agents told to read it |
| `.github/pull_request_template.md` | the PR checklist | people, and agents opening PRs |
| the CI jobs, `tools/semgrep/` | the checks no agent can skip | GitHub, on every PR |

New services start with all of it: `scripts/new-project.sh` copies this
template.

**One governed source:**
1. **One owner.** A platform team keeps the kit (skills, playbook, review
   prompt, PR template, generic Semgrep rules) in one internal repository.
   It changes only through reviewed PRs (`CODEOWNERS`) and is tagged per
   release. Teams take a tag by PR, so an update is a diff someone reads.
   Nothing updates itself.
2. **CI is the rule that holds.** Turn on branch protection in every
   repository, requiring its checks (here: `lint`, `test-api`, `build`,
   `security`, `web-build`, `e2e`). Whatever wrote the code, an agent or a
   person, red does not merge.
3. **Per tool, set by IT, from your own servers** (optional):
   - **Claude Code:** managed settings, in `/etc/claude-code/` (Linux),
     `/Library/Application Support/ClaudeCode/` (macOS),
     `C:\Program Files\ClaudeCode\` (Windows), or the claude.ai admin
     console. They hold an organisation `CLAUDE.md` nobody can switch off,
     the kit's skills in its `.claude/skills/`, and
     `strictKnownMarketplaces` limited to your own repositories.
   - **Claude Code, alternatively:** publish the kit as a plugin from that
     internal repository (`.claude-plugin/marketplace.json`) and require it
     with `extraKnownMarketplaces` and `enabledPlugins`. It comes from your
     own repository, so it is not a third-party plugin.
   - **GitHub Copilot:** organisation custom instructions in the GitHub
     organisation's settings; the coding agent also reads each
     repository's `AGENTS.md`.
   - **Codex:** reads each repository's `AGENTS.md`; skills from
     `.agents/skills/`.

Sources, October 2026: Claude Code's docs on
[skills](https://code.claude.com/docs/en/skills),
[memory and managed CLAUDE.md](https://code.claude.com/docs/en/memory),
[managed settings](https://code.claude.com/docs/en/managed-settings) and
[plugins for an organisation](https://code.claude.com/docs/en/plugins/org);
GitHub's on [Copilot custom instructions](https://docs.github.com/en/copilot/reference/custom-instructions-support)
and [skills](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/create-skills);
OpenAI's on [Codex skills](https://developers.openai.com/codex/skills).
