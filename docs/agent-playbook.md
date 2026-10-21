# Agent playbook

The framework every AI coding agent works in: the rules it reads, the
skills it loads, the gates its work must pass, and how all of it is owned
and changed. It is the same for Claude Code, GitHub Copilot, Codex and
Cursor. Everything is text and CI: free, nothing installed from outside the
company, nothing that sends code anywhere new.

**Version 2, October 2026.** Copy it into any repository with `python3
scripts/agent_kit.py copy <repo>` ("Adopt it", below); change it only as
"Govern it" says. Commands in examples come from the repository the kit
started in, triage-assistant. Yours go in your `AGENTS.md`.

## The framework on one page

| Part | What it is | Where it lives | What enforces it |
|---|---|---|---|
| **Gates** | checks that fail the build, and a person who approves and merges | CI, branch protection, `CODEOWNERS` | GitHub: red doesn't merge, a code owner approves |
| **Rules** | what an agent can't know by itself: this repository's commands, conventions, rules and traps | `AGENTS.md` (`CLAUDE.md` imports it) | every agent reads it on every task; review checks it was followed |
| **Skills** | how to do one kind of task well, loaded when the task matches | `.claude/skills/`, and a copy in `.agents/skills/` for Codex | the agent loads them; `agent_kit.py check` keeps them loadable |
| **Steps** | the path from issue to merge | this page, the PR template | the template's checklist, then review |
| **Governance** | who owns the kit, how it changes, how a lesson becomes a rule | this page, "Govern it" | `CODEOWNERS` on the kit's files |

Two principles hold it together:
- **Evidence, not assurances.** A claim comes with the command that proves
  it, a review finding with a probe, and a rule stays only while it shows a
  gain.
- **Move every rule as far up as it goes.** A mistake a machine can catch
  becomes a test or a lint rule, not a sentence. A sentence goes in
  `AGENTS.md` only when it is about this repository. "Be careful" goes
  nowhere: measured, it made no difference.

## What each agent reads

| | Claude Code | GitHub Copilot | Codex | Any other agent |
|---|---|---|---|---|
| The rules, `AGENTS.md` | yes (`CLAUDE.md` imports it) | yes | yes | tell it to read `AGENTS.md` |
| The skills | `.claude/skills/`: by themselves when the task matches, or as `/name` | `.claude/skills/`: coding agent, CLI, VS Code agent mode | `.agents/skills/` (copy with `--codex`) | as a prompt (below) |
| The gates | run on every PR, whatever wrote the code | same | same | same |

A skill is a Markdown file. Where a tool can't load it, the same words work
as a prompt: *"Read `.claude/skills/debug/SKILL.md` and follow it for this
task."*

## The skills

Five load by themselves when their moment comes; three wait to be asked.

| Skill | Loads | For |
|---|---|---|
| `debug` | by itself: a bug, a failing check, a slowdown | a command that fails on the bug before any theory; the bad value traced to where it starts; one fix there |
| `write-tests` | by itself: tests being written or reviewed | tests through the public interface, expected values from outside the code, one test and then its code |
| `full-stack-change` | by itself: an endpoint, a schema change, a UI | the checklist for each: contract, access, limits; expand and contract; accessibility, states, words |
| `prove-it` | by itself: before "done", "fixed" or "passing" | each claim next to the command that proves it, run now; what wasn't run, said plainly |
| `two-pass-review` | by itself: a review | a spec pass and a standards pass, reported apart; every finding shown with a probe |
| `ponytail` | `/ponytail` | the smallest complete change and a short reply, for a fix with a clear end |
| `hate` | `/hate` | the one objection that could sink a plan, and the cheapest test of it |
| `mandela` | `/mandela`, or when it fits | where an eval's or a benchmark's result could have leaked in |

Where each comes from, its licence and what we changed:
[`.claude/skills/README.md`](../.claude/skills/README.md).

## The steps

**1. Before you start**
- Write the issue as the symptom, not your guess at the cause, and say what
  "done" looks like.
- A bigger change gets an RFC first. Attack it with `/hate` before you
  commit to it: the one objection that could sink it, and the cheapest
  test of it.

**2. Build**
- A fix or a small change with a clear end: `/ponytail`. Leave it off when
  the change moves documented behaviour: it skips doc updates.
- A bigger change: no skill. Ask for the docs `AGENTS.md` names, such as a
  code map line, the handbook chapter, and an ADR for a decision.
- An endpoint, a schema change or a UI: `full-stack-change` has the
  checklist.
- A bug: `debug`. A command that fails on the bug comes before any fix.
- Tests: `write-tests`. A bug fix comes with a test that fails on the old
  code.

**3. Prove it: the gates**

| Gate | What it catches | In triage-assistant: locally, and its CI job |
|---|---|---|
| Lint and format | style, the repository's own rules (Semgrep), shell scripts, a skill that won't load | `make lint`; `lint` |
| Types | calls that don't fit what they call | `make typecheck`; `lint`, `web-build` |
| Tests | wrong behaviour, the failure paths included | `make test` (all), `make test-fast` (unit, seconds); `test-api`, `web-build` |
| Secrets and vulnerabilities | a leaked secret, a known vulnerability in what ships | `make secrets-scan`, `make scan`; `security` |
| The build | a production image that doesn't build or start | `make image-check`; `build` |
| The browser | the app broken through the real stack | `make prod-up && make e2e`; `e2e` |
| Model behaviour | a prompt, model or retrieval change that answers worse | `make evals ...`, before and after; the numbers go in the PR |
| Failure behaviour | an upgrade on the database path that fails differently | `make drills d="db-freeze pgbouncer-freeze ..."`; `Drills`, run by hand on the branch |

Every repository has the first four as required CI checks, the rest where
they apply. Before anything is called done: `prove-it`, each claim next to
the command that proves it. In a cloud session without Docker: the host
commands in `AGENTS.md`, then CI.

**4. Tidy**
- Claude Code: `/simplify` on your diff. Elsewhere: *"Simplify this diff:
  reuse what exists, keep the behaviour, list what you rejected and why."*
- Keep what reads better; drop what widens the PR.

**5. Security pass**, when the change touches access, sign-in, secrets or
anything an outsider can send:
- Claude Code: `/security-review`, from a session on the branch with the
  change committed (if it can't find `origin/HEAD`:
  `git remote set-head origin --auto`).
- Any agent: *"Check this diff against the security rules in `AGENTS.md`,
  one rule at a time."* `/security-review` skips denial-of-service
  findings; rules such as bounded regular expressions need this second
  look.

**6. Review**, on every PR: `two-pass-review`, or this prompt for an agent
that can't load it:

> Review *[the area: files or a few hundred lines]* of this change in two
> passes, reported apart: does it do what the issue asked, and only that;
> does it follow `AGENTS.md`. Show each finding with a probe or a test you
> ran, not a reading; probes go in a scratch folder, and leave the
> repository as you found it. List the most severe first: where, what goes
> wrong and when, how sure you are. A short list of real problems beats a
> long one; say so if there are none.

- **Small PR** (a few hundred lines): one review.
- **Large PR:** one review per area the change's promises rest on (for
  example access, sign-in, migrations), then one over the whole diff for
  what crosses areas. On two bugs that once shipped, reviews of the right
  area found the bug 12 times in 12 and ranked it in their top two 9 times;
  reviews of the whole PR found it 6 times in 6, never in their top two.
- **Claude Code, for breadth on the riskiest files:** `/code-review`. Read
  its whole list: the real bug sat anywhere from 2nd to 9th of 15.

**7. The PR**
- Fill in the template: what and why, how you verified it (commands and
  numbers), which agent wrote it and with which skills, the checklist.
- Every review finding is fixed, or answered in the PR.
- The gates are green. A person merges (squash) and owns what was merged.

**8. Before trusting a number**
- An eval, a benchmark, a "we measured X": `/mandela`. It finds where the
  result could have leaked in.

**9. After a bug gets through**
- A test that fails on the old code.
- A rule a machine checks (Semgrep, a lint rule) when a code pattern shows
  the mistake, with a case it must and one it must not match.
- A line in `AGENTS.md` when an agent gets the same thing wrong twice.
- A change to the kit when the lesson holds beyond one repository ("Govern
  it").
- Never a "be careful" line.

**Never**
- Weaken, skip or exempt a test, an eval case or a scan finding to get
  green.
- Paste a secret into a chat. If you did, rotate it.
- Install a plugin from outside the company, or a skill you haven't read.
- Let an agent merge, or approve its own work.

## Adopt it in a repository

**Day one: the files.** From a checkout of the kit (in triage-assistant,
`make agent-kit to=../your-repo [codex=1]` does the same):

    python3 scripts/agent_kit.py copy ../your-repo    # add --codex if anyone uses Codex

It copies this page, the skills, `scripts/agent_kit.py` and, if you have
none, a PR template to start from. It never touches your `AGENTS.md`. Then
write:

- **`AGENTS.md`**, about your repository and nothing else:
  - **Overview:** what it is, its main parts and where they live.
  - **Commands:** set up, run, test (fast and full), lint, types, and the
    one to run before pushing.
  - **Conventions:** style, commit messages, branches, where tests go.
  - **Rules:** security, data access, migrations, dependencies, each with
    its reason.
  - **Traps:** what has bitten people here, and what to do instead.
  - **Never:** what an agent must not do in this repository.
  - **Reviewing a change:** a link to this page.
- **`CLAUDE.md`** with the line `@AGENTS.md`, for Claude Code.

**First week: the gates.**
- CI runs lint, types, tests, the secret scan and the vulnerability scan
  on every PR, and `python3 scripts/agent_kit.py check`.
- Branch protection on the main branch: those checks required, an approving
  review from a code owner, no direct pushes.
- `CODEOWNERS` names an owner for `AGENTS.md`, `CLAUDE.md`, `.claude/`,
  `.agents/`, `docs/agent-playbook.md`, `scripts/agent_kit.py` and the PR
  template.
- The PR template's commands are yours.

**When the organisation is ready: the tools.** Set by IT, from your own
servers. Optional: the files and the gates work without it.
- **Claude Code:** managed settings, in `/etc/claude-code/` (Linux),
  `/Library/Application Support/ClaudeCode/` (macOS),
  `C:\Program Files\ClaudeCode\` (Windows), or the claude.ai admin console.
  They hold an organisation `CLAUDE.md` nobody can switch off, the kit's
  skills in its `.claude/skills/`, and `strictKnownMarketplaces` limited to
  your own repositories.
- **Claude Code, alternatively:** publish the kit as a plugin from your
  internal repository (`.claude-plugin/marketplace.json`) and require it
  with `extraKnownMarketplaces` and `enabledPlugins`. It comes from your own
  repository, so it is not a third-party plugin.
- **GitHub Copilot:** organisation custom instructions in the GitHub
  organisation's settings; the coding agent also reads each repository's
  `AGENTS.md`.
- **Codex:** reads each repository's `AGENTS.md`, and skills from
  `.agents/skills/`.
- **Every tool** sends code to its vendor's models. Allow only the tools
  the company has approved, on the repositories it approved them for.

## Govern it

**One owner.** A platform team, or one named person until there is a team,
keeps the kit in one internal repository: this page, the skills and
`scripts/agent_kit.py`. It changes only through reviewed PRs. Every release
gets a tag and a line under "Changes".

**Teams take a release by PR.** `scripts/agent_kit.py copy` from a checkout
of the tag: the update is a diff someone reads, and a person merges it.
Nothing updates itself.

**What a team changes, and where:**

| Change | Where | Who decides |
|---|---|---|
| Commands, rules and traps for one repository | its `AGENTS.md` | its code owners |
| A check for one repository | its CI, its own lint or Semgrep rules | its code owners |
| A skill for one repository | `.claude/skills/<name>/`, listed in `.claude/skills/LOCAL.md` | its code owners |
| The PR template | its `.github/pull_request_template.md`, keeping the lines on verification, review and the agent used | its code owners |
| This page, the kit's skills, `scripts/agent_kit.py` | a PR to the kit's repository | the kit's owner |

Every update overwrites the kit's files, so a local edit to them is lost.
Propose it to the kit instead.

**Changing the kit.** A PR to the kit's repository that says:
- what went wrong, with the PR, bug or review that showed it;
- what changes;
- how you'll know it worked. Run `/hate` on that before asking for review.

A new skill also needs:
- someone who has read all of it;
- text only: no scripts, no hooks, nothing that calls out;
- its source and licence in `.claude/skills/README.md`;
- short enough to read in five minutes;
- a note saying whether it is measured.

**Exceptions.** A gate skipped for one PR needs the reason in the PR and a
code owner's approval. The "Never" list has no exceptions.

**Measure it.** Each quarter the owner looks at, per repository:
- the bugs that reached the main branch, and for each whether a gate now
  catches its kind;
- reverts and hotfixes;
- review findings per PR, and how many were real;
- the time from opening a PR to merging it;
- which agents and skills wrote the changes (the PR template records it).

A rule or a skill that shows no gain goes. A gate that keeps being skipped
is either wrong or not understood: fix whichever it is.

**What is measured so far.** In triage-assistant, October 2026, on two
bugs that once shipped (its `docs/coding-with-agents.md` and
`docs/testing_coding_skills.md`): area reviews found them, and `/ponytail`
was as correct as no skill with a third of the diff. A "be careful" block
in `AGENTS.md` showed no gain and was dropped. The five skills added in
version 2 are not measured yet.

## Changes

- **Version 2, October 2026.** Five skills that load by themselves
  (`debug`, `write-tests`, `full-stack-change`, `prove-it`,
  `two-pass-review`); the framework on one page; adopting it and governing
  it; `scripts/agent_kit.py`, to copy the kit and check it; the agent and
  skills used, recorded in the PR template.
- **Version 1, October 2026.** The steps, the checks, the review prompt,
  sharing across teams.

Sources, October 2026: Claude Code's docs on
[skills](https://code.claude.com/docs/en/skills),
[memory and managed CLAUDE.md](https://code.claude.com/docs/en/memory),
[managed settings](https://code.claude.com/docs/en/managed-settings) and
[plugins for an organisation](https://code.claude.com/docs/en/plugins/org);
GitHub's on [Copilot custom instructions](https://docs.github.com/en/copilot/reference/custom-instructions-support)
and [skills](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/create-skills);
OpenAI's on [Codex skills](https://developers.openai.com/codex/skills).
Where each skill comes from: [`.claude/skills/README.md`](../.claude/skills/README.md).
