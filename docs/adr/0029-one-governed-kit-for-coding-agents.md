# ADR-0029: One governed kit for every coding agent

**Status:** accepted
**Date:** 2026-10-21

## Context

Code here is written by several agents: Claude Code, GitHub Copilot, and
in other teams Codex and Cursor. The company allows no external plugins,
and code may only go where it already goes. Version 1 of
`docs/agent-playbook.md` listed the steps, but it was written for this
repository: other teams had to pick out what applied to them, and nothing
said who may change it or how a team takes an update.

Skill packs are popular, and we read the ten most-starred in October 2026.
The useful ones carry real know-how (debugging from a failing command,
tests that can fail, expand-and-contract migrations, reviewing against the
spec), but they arrive with installers, hooks that run on every session,
scripts, and in two cases calls to outside AI services. One returns 403
where our rule is 404.

We also measured that rules saying "work carefully" made no difference
(docs/handbook/code-style.md, "What we measured"). What helped was checks,
review of the right area, and repository-specific rules.

## Decision

- **One kit**, owned by one person or team: `docs/agent-playbook.md` (the
  framework: gates, rules, skills, steps, governance), the skills in
  `.claude/skills/`, and `scripts/agent_kit.py`. It is versioned, and
  changes only by reviewed PR (`CODEOWNERS`).
- **Skills are ours, in text.** Five new skills (`debug`, `write-tests`,
  `full-stack-change`, `prove-it`, `two-pass-review`) are rewritten from
  the packs' ideas, credited in `.claude/skills/README.md`, and load by
  themselves at their moment. No pack is installed.
- **The kit travels by copy.** `scripts/agent_kit.py copy <repo>` writes the
  kit's files into another checkout, so an update is a diff a person
  reviews. It never touches the target's `AGENTS.md`; a team's own skills
  live in `.claude/skills/LOCAL.md`.
- **The kit is checked like code.** `scripts/agent_kit.py check`, in `make
  lint` and CI, fails when a skill would not load (an agent skips it
  silently), isn't listed with its source, or its Codex copy differs.
- **What the gates can't see is recorded.** The PR template asks which
  agent and skills wrote the change, so the owner's quarterly review can
  tell what helps.

## Alternatives considered

- **Install the packs** (superpowers, Matt Pocock's, Addy Osmani's).
  Against the company rule, and superpowers adds a hook that makes every
  reply load a skill first. We would also inherit rules that contradict
  ours.
- **A plugin from an internal marketplace.** Works for Claude Code only;
  Copilot and Codex read files in the repository. The playbook keeps it as
  an option for the Claude Code side.
- **A git submodule or package for the kit.** Updates would arrive without
  a diff in the team's own repository, and agents would read skills from a
  path that changes under them.
- **Generic rules in every `AGENTS.md`.** Measured: no gain, slower reviews.

## Consequences

- Any team adopts the framework with one command and one PR, and every
  agent it uses reads the same rules and skills.
- The five new skills are not measured yet. The playbook says so, and the
  owner's quarterly review decides whether each stays.
- Two copies of the skills where Codex is used (`.claude/skills/` and
  `.agents/skills/`); the check keeps them equal.
- The kit's files are overwritten by each update, so local edits to them
  are lost: changes go through the kit's owner.
