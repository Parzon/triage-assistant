# ADR-0020: An agent mode beside the pipeline, and its tools over MCP

**Status:** accepted
**Date:** 2026-09-24

## Context

The assistant answers with a fixed pipeline (a workflow): the service reads
the asker's newest alerts and the runbook sections that match the question,
puts them in the prompt, and calls the model once. The alternative is an
agent: the model gets tools and decides what to read, and how often.

Two questions needed answers with data, not opinion:
- Does letting the model choose what to read give better answers here, and
  at what cost in latency, tokens and predictability?
- Can other AI tools (Claude Code, an IDE assistant) use the same data
  safely, without each getting its own integration?

## Decision

- **The pipeline stays the default.** `CHAT_MODE=agent` switches the chat
  to the agent (`app/agent.py`): a bounded loop, at most `AGENT_MAX_STEPS`
  rounds of tool calls (default 3), then one round without tools, in which
  the model must answer.
- **The tools are read-only, and shared** (`app/tools.py`): `list_alerts`
  and `search_runbooks`, which go through the api's own queries with the
  asker's rights. No argument names a team or a user. Arguments are
  validated against a hand-written schema; a failure is a result the model
  can read and correct. Every call is audited (`tool.called`), traced
  (`execute_tool`) and counted (`tool_calls_total`).
- **The same tools over MCP** (`app/mcp_server.py`): a small stdio server
  for a local MCP client, acting as the person whose session cookie it is
  given (`TRIAGE_SESSION`). It checks the session on every call, so a
  revoked session stops it. Calls are audited as `via = "mcp"`.

## Alternatives considered

- **Replace the pipeline with the agent.** Measured on the eval suite
  (docs/handbook/agents.md): the first agent prompt passed 14 of 19 cases
  against the pipeline's 18. It filtered alerts to critical, or searched
  only the runbooks, and missed the answer. The second prompt, which tells
  it to read what the pipeline reads, passed 17, at 3.8× the prompt tokens,
  2.7× the model calls and 2.3× the time to the first word. Here the right
  context is known before the model runs.
- **Generate the tool schemas from the pydantic models.** Less code, but
  the schema is prompt text the model reads to choose and fill in a tool,
  and it should be reviewed as such, not changed by a refactor.
- **Remote MCP over HTTP.** It needs the protocol's OAuth authorization
  (an authorization server, audience-bound tokens, no token passthrough).
  Not worth building for an example; stdio with a person's session covers a
  local client.
- **An MCP server with its own service account.** Simpler, and it would
  let anyone who steers the client's model read as that account: a
  confused deputy.

## Consequences

- One tool implementation, two clients: a rule added to `tools.call()`
  holds for both.
- Agent mode costs more model calls and tokens per answer, and is less
  predictable: whether a section is read depends on the model's choice.
  Its answers are evaluated end to end through the api target, and the
  reports count model and tool calls.
- A tool that changes state needs more than this ADR: the person's
  confirmation before it runs (docs/handbook/ai-security.md, rules for
  tools).
- A new dependency: the `mcp` SDK (the protocol's reference implementation
  for Python).
