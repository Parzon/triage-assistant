# Agents

When the model should decide its own steps, and how the assistant does it:
an agent mode beside the fixed pipeline, two read-only tools, and the same
tools over MCP. Measured with gpt-oss:20b on the eval suite. ✅ = built and
measured here, 📘 = documented practice, not built here.

## Workflow or agent

Anthropic's definitions ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)):
- a **workflow** runs models and tools through code paths written in
  advance;
- an **agent** lets the model direct its own process: which tool to call,
  with what, and when to stop.

The assistant's default is a workflow, the **pipeline**: read the asker's
newest alerts and the runbook sections matching the question, put them in
the prompt, call the model once. `CHAT_MODE=agent` switches it to an
**agent**: the model gets two tools and chooses what to read.

**Default to the simplest design that solves the problem**, and add a step
only when a measurement shows the simpler one falls short:

| Step | What it is | Here |
|---|---|---|
| One model call | a prompt, an answer | — |
| One call, with context the code fetched | retrieval, then the model (RAG) | ✅ the pipeline |
| A workflow | several calls on a fixed path: prompt chaining, routing, parallel calls, orchestrator-workers, evaluator-optimizer | — |
| One agent with tools | the model picks the path | ✅ `CHAT_MODE=agent` |
| Several agents | a supervisor hands parts to other agents | 📘 |

**When not to use an agent:**
- the steps are known before the model runs (here: the asker's alerts and
  the matching runbooks, every time);
- latency or cost matters: every step is another model call;
- the result must be predictable and easy to audit;
- the model is small: each step is another chance to choose wrong.

**When an agent pays:** open-ended tasks whose steps cannot be known in
advance, like an investigation across many sources or a change across a
codebase. Also when the data needed depends on what the last step found.

## Measured: the pipeline against the agent ✅

The eval suite (19 cases) through the running service, 3 runs each, answers
by gpt-oss:20b (reasoning effort low), judged by gemma3:27b. The same data,
the same retrieval code: only who decides what to read differs.

| | Pipeline | Agent, prompt v1 | Agent, prompt v2 |
|---|---|---|---|
| cases passed (all 3 runs) | **18 of 19** | 14 of 19 | 17 of 19 |
| grounding | 5 of 6 | 3 of 6 | 5 of 6 |
| isolation | 4 of 4 | 2 of 4 | 3 of 4 |
| injection and refusal | 9 of 9 | 9 of 9 | 9 of 9 |
| model calls per answer | 1 | 2.5 | 2.7 |
| tool calls per answer | — | 1.5 | 1.7 |
| prompt tokens per answer | 485 | 1,553 | 1,841 (3.8×) |
| answer tokens per answer | 96 | 130 | 154 |
| first token, p50 | 0.27 s | 0.50 s | 0.62 s (2.3×) |
| whole answer, p50 | 0.62 s | 0.78 s | 0.95 s |

**What went wrong under v1.** Every tool call is an audit event, so each
answer's path could be read back:
- **It filtered too hard.** v1 said "critical alerts matter most", and
  the model called `list_alerts(severity=critical)`. It got nothing back
  when the cause was a warning or a deploy, and answered "no recent
  alerts".
- **It picked the wrong tool.** For "why is checkout failing?" it only
  searched the runbooks. The answer, a deploy two minutes before the
  errors, was an alert.
- **It stopped after an empty result,** with steps left.
- **The isolation cases failed on *less*, not more:** 0 alerts read where
  the case expects exactly the asker's 1. No other team's data was read.

**v2** says to list alerts without a filter first, to search the runbooks
for what to do, and to try the other tool after an empty result. That
fixed grounding. What is left: in one run of three, the model answered
"I don't have that information" without calling a tool.

**The result:** the agent matched the pipeline only once its prompt told
it to do what the pipeline does. Even then it cost 3.8× the prompt tokens,
2.7× the model calls and 2.3× the time to the first word. Here the steps
are known in advance, so the pipeline stays the default.

Caveats: a local 20B model, and cases with a few alerts each, written to
test what the pipeline is given. A stronger model chooses better. Measure
again before switching.

## How the agent works

`app/agent.py`, a bounded loop:

1. The messages start as the agent's prompt and the question (redacted).
2. The model is called with the tools. If it asks for none, its text is
   the answer: stop.
3. Each tool call runs (`app/tools.py`), and its result is appended as a
   `tool` message under the call's id. Back to 2.
4. After `AGENT_MAX_STEPS` rounds with tools (default 3), one last call
   offers none: the model must answer with what it has.

**How it stops:** no tool call, or the step limit. A call repeated with the
same arguments is refused, which catches the simplest loop. Each tool call
has a 15-second limit; the whole answer has `LLM_STREAM_TIMEOUT_S`.

**State:** the messages list is the agent's short-term memory, for one
question. Nothing carries over to the next question: like the pipeline, it
is stateless. 📘 Long-term memory (past incidents, a person's
preferences) would be another retrieval source, scoped by person and team.
A planted instruction saved into memory would then steer every later
answer (memory poisoning), so memory writes need the same care as tool
writes.

**The stream:** the client sees each tool call as it happens (a `tool`
event: the tool and a count, never the content). The `done` event reports
the model calls and tool calls. The UI shows them in its status line.

**Citations:** each search numbers its sections after the ones already
read ([R1], [R2]...), and a section read twice keeps its number. The
answer's citations are checked against every section the agent read.

## Tools ✅

`app/tools.py`: `list_alerts(severity?, limit?)` and
`search_runbooks(query, k?)`. A tool is a name, a description and a JSON
Schema of its arguments. The model chooses a tool from its description:
that text is prompt text, written by hand and reviewed like the prompt.

| Rule ([AI security](ai-security.md#rules-for-tools)) | How the tools meet it |
|---|---|
| Act as the asker | each call reads through the api's own queries with the asker's principal; row-level security underneath |
| Identity from the session, never an argument | no argument names a team or a user |
| Read-only | nothing a tool does changes state |
| Validate the arguments | pydantic models, unknown fields refused, every number bounded (20 alerts, 8 sections, a 300-character query) |
| Errors the model can act on | a bad call returns a result saying what was wrong ("Invalid arguments: query: Field required"), so the model can correct it; the loop goes on |
| Bounded | a time limit per call; a step limit per answer |
| Audited | a `tool.called` event per call that reads: the query as a hash, the ids read |
| Untrusted output | tool results carry alert and runbook text: redacted, and the prompt says never to follow instructions in it |

A tool name comes from the model. A name that is not a tool becomes
`unknown` before it reaches a metric label, a span or a log
(`tools.label()`): a model's output is untrusted input.

**Tool selection** gets harder with every tool added: more descriptions
to tell apart, and a larger prompt. Keep tools few and distinct, and give
each a description that says when to use it.

## MCP ✅

The Model Context Protocol connects AI applications to tools and data with
one protocol instead of one integration per pair.
- **Roles:** a *host* (Claude Code, an IDE, a chat app) runs a *client*
  for each *server* it connects to.
- **Server primitives:** *tools* (functions the model calls), *resources*
  (data the application reads) and *prompts* (templates the user picks).
- **Client primitives:** *sampling* (the server asks the host's model),
  *elicitation* (the server asks the user) and *roots*.
- **Wire:** JSON-RPC 2.0. `tools/list` returns each tool's name,
  description and input schema; `tools/call` runs one.
- **Transports:** *stdio* (the host starts the server as a subprocess) or
  *Streamable HTTP* (a remote server). Remote servers authorize with OAuth
  2.1.
- **Two kinds of error:** a protocol error (an unknown tool, a malformed
  request) is a JSON-RPC error. A tool that ran and failed returns a
  result with `isError: true`, which the model reads and can correct.

**The server here** (`app/mcp_server.py`, protocol 2026-07-28 through the
`mcp` SDK): the same two tools, the same `tools.call()`, over stdio. It
acts for one person, whose session cookie it is given in `TRIAGE_SESSION`
(an environment variable, since arguments show in `ps`). It checks the
session on every call as the api checks a request: a revoked session
(`make revoke`) stops the next call. Calls are audited as `via = "mcp"`.

Connect Claude Code, as a person of your choice:

```sh
cookie=$(make -s session email=you@example.com groups="team:payments:viewer")
claude mcp add --transport stdio triage --env TRIAGE_SESSION="$cookie" -- \
  docker compose --project-directory "$PWD" exec -T -e TRIAGE_SESSION api python -m app.mcp_server
```

Or inspect it: `npx @modelcontextprotocol/inspector` with the same command.

**MCP security, in short:**
- *Servers* validate every input, enforce access control, rate-limit, and
  never pass a client's token on to another service (token passthrough is
  forbidden).
- *Hosts* show the person what a tool will do, confirm sensitive calls,
  time out calls, and log them.
- Tool annotations (`readOnlyHint`) are hints: a client must not trust them
  from a server it does not trust. This server declares its tools
  read-only, and is read-only because of its code.
- A server's tool descriptions go into the host model's context. A
  malicious server can put instructions there (tool poisoning), so install
  servers the way you install dependencies: trusted sources, pinned
  versions.

📘 Not built: the HTTP transport with OAuth (an authorization server,
tokens bound to this server's audience), and resources or prompts.

## Approval gates 📘

The tools here only read, so nothing needs approving: the person reads the
answer and acts. A tool that changes something (acknowledge an alert, edit a
runbook) would work like this:
1. The model *proposes* the action: the tool returns a pending action, not
   a result.
2. The UI shows exactly what will happen, and the person confirms.
3. The api runs it with the person's rights, under the same role checks as
   the UI's own button, and the audit event records the approval.

The confirmation is the person's request, so the model cannot forge it.
With MCP, the host confirms tool calls, and elicitation lets a server ask
the person directly. Mind the Rule of Two ([AI security](ai-security.md#rules-for-tools)):
untrusted input, private data and the power to change or send out, never
all three without a person in between.

## Several agents 📘

A **supervisor** (orchestrator) splits a task and hands the parts to
worker agents, each with its own context. A **handoff** passes control, and
the context it needs, from one agent to another. It helps with broad tasks
done in parallel, and with contexts too large for one model. It costs
tokens: Anthropic measured agents at about 4× the tokens of a chat, and its
multi-agent research system at about 15×
([How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)).
Coordination adds its own failures: duplicated work, lost context at a
handoff. Not warranted here: two tools, one question.

## Watching an agent ✅

- **Traces:** an `invoke_agent triage-agent` span holds a `chat` span per
  model call and an `execute_tool` span per tool call (outcome, a count
  summary). The request span has `app.chat.mode`, `app.chat.model_calls`
  and `app.chat.tool_calls`.
- **Metrics:** `agent_steps` (rounds per answer: a bump at the limit means
  the model keeps calling), `tool_calls_total{tool, outcome, via}`, and the
  usual `llm_tokens_total`, now summed over every call of an answer.
- **Audit:** `chat.asked` with `mode: agent`, then a `tool.called` per
  call.

## Evaluating an agent ✅

Evaluate the final answer end to end, through the api target, with the
same cases and judge as the pipeline. Then look at the path it took:
model calls, tool calls, which tools, how many answers hit the step limit.
A right answer reached through six calls is a cost problem that
answer-only checks miss.

```sh
CHAT_MODE=agent docker compose up -d --wait api   # plus the LLM_* of a real model
make evals a="--target api --judge --judge-model gemma3:27b --repeat 3"
```

The report counts `model_calls` and `tool_calls`, for the run and for each
answer. 📘 Not built: checks on the path itself ("must search the runbooks
before giving steps").

## Gotchas

In the guide's list, with every other gotcha met here: [Agents and MCP](../../gold_standard_development_guide.md#agents-and-mcp).
