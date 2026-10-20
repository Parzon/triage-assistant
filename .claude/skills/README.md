# Skills

Prompts an agent loads on demand: Claude Code reads them from here, and so
do GitHub Copilot's agents. Plain Markdown, nothing installed, nothing run.
When to use which, with the other tools: [Coding with
agents](../../docs/coding-with-agents.md); what each did when we tried it:
[testing_coding_skills.md](../../docs/testing_coding_skills.md).

| Skill | Use it | Runs |
|---|---|---|
| `ponytail` | `/ponytail` for a bug fix or a small change with a clear end. Leave it off when a change moves behaviour the handbook documents: it skips doc updates. | when asked |
| `hate` | `/hate` on a plan, an ADR, an RFC or a rule before adopting it: the one objection that could sink it, and the cheapest test of it. | when asked |
| `mandela` | `/mandela` on an eval, a benchmark or a "we measured X" before trusting it: where the result could have leaked in. | when asked, or when the agent judges a task fits |

## Where they come from

Copied, not installed: an update is a pull request that shows the new text.

| Skill | Source | Commit | Changes |
|---|---|---|---|
| `ponytail` | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) `skills/ponytail/` | `09c434a` | `disable-model-invocation: true`: upstream runs it on every coding task |
| `hate` | [lilmgenius/paperthin](https://github.com/lilmgenius/paperthin) `skills/depth/hate/` | `7d5dc62` | none |
| `mandela` | [lilmgenius/paperthin](https://github.com/lilmgenius/paperthin) `skills/depth/mandela/` | `7d5dc62` | none |

All three are MIT; each folder keeps its licence. Before adding a skill,
read all of it: it is a prompt with your session's permissions.
