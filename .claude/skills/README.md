# Skills

Prompts an agent loads on demand: Claude Code and GitHub Copilot's agents
read them from here, Codex from a copy in `.agents/skills/`. Plain Markdown,
nothing installed, nothing run. They are part of the agent kit: when to use
each one, and how the kit is shared and governed, is in [the agent
playbook](../../docs/agent-playbook.md).

| Skill | Loads | Use it for |
|---|---|---|
| `ponytail` | when asked | `/ponytail` on a bug fix or a small change with a clear end. Leave it off when a change moves documented behaviour: it skips doc updates |
| `hate` | when asked | `/hate` on a plan, an ADR, an RFC or a rule before adopting it: the one objection that could sink it, and the cheapest test of it |
| `mandela` | when asked, or when the agent judges a task fits | `/mandela` on an eval, a benchmark or a "we measured X" before trusting it: where the result could have leaked in |
| `debug` | by itself, on a bug, a failing check or a slowdown | a command that fails on the bug before any theory; trace the bad value to where it starts; fix it there |
| `write-tests` | by itself, when tests are written or reviewed | tests through the public interface, expected values from outside the code, one test and then its code |
| `full-stack-change` | by itself, on an endpoint, a schema change or a UI | the checklist for each: contract, access, limits; expand and contract; accessibility, states, words |
| `prove-it` | by itself, before "done", "fixed" or "passing" | each claim with the command that proves it, run now, and what wasn't run |
| `two-pass-review` | by itself, when asked for a review | a spec pass and a standards pass, reported apart; each finding shown with a probe |

`python3 scripts/agent_kit.py check` (in `make lint` and CI) fails when a
skill would not load or isn't listed here.

## Where they come from

Copied or rewritten, never installed: an update is a pull request that
shows the new text.

| Skill | Source | Commit | Licence | Changes |
|---|---|---|---|---|
| `ponytail` | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) `skills/ponytail/` | `09c434a` | MIT, in the folder | `disable-model-invocation: true`: upstream runs it on every coding task |
| `hate` | [lilmgenius/paperthin](https://github.com/lilmgenius/paperthin) `skills/depth/hate/` | `7d5dc62` | MIT, in the folder | none |
| `mandela` | [lilmgenius/paperthin](https://github.com/lilmgenius/paperthin) `skills/depth/mandela/` | `7d5dc62` | MIT, in the folder | none |
| `debug` | written here, from [mattpocock/skills](https://github.com/mattpocock/skills) `diagnosing-bugs` and [obra/superpowers](https://github.com/obra/superpowers) `systematic-debugging` | `5beb35b`, `b739a8a` | ours; ideas from MIT sources | rewritten and shortened; no scripts |
| `write-tests` | written here, from mattpocock/skills `tdd` and obra/superpowers `test-driven-development` | `5beb35b`, `b739a8a` | ours; ideas from MIT sources | rewritten; no "agree the seams with the user" step |
| `full-stack-change` | written here, from [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) (API design, security, migrations, frontend), [anthropics/skills](https://github.com/anthropics/skills) `frontend-design` and [ui-ux-pro-max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | `aeac41d`, `dbd4588`, `56ea9cf` | ours; ideas from MIT and Apache-2.0 sources | 404 for what the caller can't see, where the source says 403 |
| `prove-it` | written here, from obra/superpowers `verification-before-completion` | `b739a8a` | ours; ideas from an MIT source | rewritten without the all-caps rules |
| `two-pass-review` | written here, from mattpocock/skills `code-review` and `AGENTS.md`'s review rules | `5beb35b` | ours; ideas from an MIT source | one agent by default, not two subagents; the smells in our own words |

## Adding a skill

- Read all of it: a skill is a prompt that runs with your session's
  permissions.
- Copy it with its licence, or write your own from its ideas, into
  `.claude/skills/<name>/SKILL.md`. No scripts, no hooks, nothing that calls
  out.
- A row in the tables above. A skill only this repository uses goes in
  `LOCAL.md` next to this file instead, which the kit never overwrites.
- `disable-model-invocation: true` unless it should load by itself.
- Codex: `python3 scripts/agent_kit.py copy . --codex` refreshes
  `.agents/skills/`.
