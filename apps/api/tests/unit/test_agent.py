"""The agent's loop (app/agent.py) and the tool calls it makes (app/tools.py):
what happens to each kind of call. The tools read the database:
tests/integration/test_agent.py."""

import asyncio
import copy
from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import Any, cast

import pytest
from prometheus_client import REGISTRY

from app import agent, tools
from app.llm import Finish, Message, PromptRef, ToolCall, Usage
from app.triage import Item, ToolEvent


class ScriptedLLM:
    """One scripted turn per model call; a call past the script answers."""

    model = "fake-agent"

    def __init__(self, *turns: list[str | ToolCall]) -> None:
        self.turns = list(turns)
        self.calls: list[tuple[list[Message], object]] = []

    async def stream(
        self,
        messages: list[Message],
        prompt: PromptRef | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str | Usage | Finish | ToolCall]:
        self.calls.append((copy.deepcopy(messages), tools))
        turn = self.turns.pop(0) if self.turns else ["The answer."]
        for item in turn:
            yield item
        yield Usage(100, 10)
        yield Finish("tool_calls" if any(isinstance(i, ToolCall) for i in turn) else "stop")

    async def aclose(self) -> None:
        return None


def call(name: str, arguments: str = "{}", id: str = "c1") -> ToolCall:
    return ToolCall(id, name, arguments)


async def run(llm: ScriptedLLM, max_steps: int = 3) -> list[Item]:
    settings = SimpleNamespace(agent_max_steps=max_steps)
    ctx = tools.ToolContext(cast(Any, None), cast(Any, None), cast(Any, None), cast(Any, settings))
    items: list[Item] = []

    async def emit(item: Item) -> None:
        items.append(item)

    await agent.answer(cast(Any, llm), "db-1 is full, what now?", ctx)(emit)
    return items


@pytest.fixture
def alerts_read(monkeypatch: pytest.MonkeyPatch) -> list[tools.ListAlertsArgs]:
    """list_alerts without a database: records its arguments."""
    seen: list[tools.ListAlertsArgs] = []

    async def fake(args: tools.ListAlertsArgs, ctx: tools.ToolContext) -> tools.Result:
        seen.append(args)
        return tools.Result("- [critical] db-1 disk at 96%", True, "1 alerts")

    monkeypatch.setattr(tools, "_list_alerts", fake)
    return seen


def tool_messages(llm: ScriptedLLM) -> list[str]:
    return [m["content"] for m in llm.calls[-1][0] if m["role"] == "tool"]


async def test_the_model_reads_through_a_tool_then_answers(
    alerts_read: list[tools.ListAlertsArgs],
) -> None:
    llm = ScriptedLLM([call("list_alerts", '{"severity": "critical"}')])
    items = await run(llm)
    assert ToolEvent("list_alerts", True, "1 alerts") in items
    assert [i for i in items if isinstance(i, str)] == ["The answer."]
    assert alerts_read[0].severity == "critical"
    # The second call sees its own tool call, then the result under its id.
    assistant, result = llm.calls[1][0][-2:]
    assert assistant["tool_calls"][0]["function"]["name"] == "list_alerts"
    assert (result["role"], result["tool_call_id"]) == ("tool", "c1")


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ('{"query": ""}', "Invalid arguments: query: String should have at least 1 character"),
        ('{"k": 3}', "Invalid arguments: query: Field required"),
        ('{"query": "disk", "team": "payments"}', "Invalid arguments: team: Extra inputs"),
        ("disk full", "Invalid arguments: not valid JSON"),
    ],
)
async def test_invalid_arguments_get_an_error_the_model_can_correct(
    arguments: str, error: str
) -> None:
    llm = ScriptedLLM([call("search_runbooks", arguments)])
    items = await run(llm)
    assert ToolEvent("search_runbooks", False, "invalid arguments") in items
    assert tool_messages(llm)[0].startswith(error)


async def test_the_same_call_twice_is_refused(alerts_read: list[tools.ListAlertsArgs]) -> None:
    llm = ScriptedLLM(
        [call("list_alerts", '{"limit": 5}')],
        [call("list_alerts", '{ "limit" : 5 }', id="c2")],
    )
    items = await run(llm)
    assert ToolEvent("list_alerts", False, "repeated call refused") in items
    assert len(alerts_read) == 1


async def test_the_step_limit_makes_the_model_answer(
    alerts_read: list[tools.ListAlertsArgs],
) -> None:
    turns: list[list[str | ToolCall]] = [
        [call("list_alerts", f'{{"limit": {n}}}', id=f"c{n}")] for n in range(1, 10)
    ]
    llm = ScriptedLLM(*turns)
    await run(llm, max_steps=2)
    # Two rounds with tools, then one without: whatever that round asks for
    # is never run.
    assert [tools is not None for _, tools in llm.calls] == [True, True, False]
    assert len(alerts_read) == 2


async def test_an_invented_tool_is_refused_and_counted_as_unknown() -> None:
    before = REGISTRY.get_sample_value(
        "tool_calls_total", {"tool": "unknown", "outcome": "error", "via": "api"}
    )
    llm = ScriptedLLM([call("delete_runbook", '{"id": 7}')])
    items = await run(llm)
    assert ToolEvent("unknown", False, "unknown tool") in items
    assert tool_messages(llm)[0].startswith("Unknown tool.")
    after = REGISTRY.get_sample_value(
        "tool_calls_total", {"tool": "unknown", "outcome": "error", "via": "api"}
    )
    assert after == (before or 0) + 1


async def test_a_slow_tool_times_out_and_the_model_is_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hang(args: tools.ListAlertsArgs, ctx: tools.ToolContext) -> tools.Result:
        await asyncio.sleep(10)
        raise AssertionError("not reached")

    monkeypatch.setattr(tools, "_list_alerts", hang)
    monkeypatch.setattr(tools, "TIMEOUT_S", 0.01)
    llm = ScriptedLLM([call("list_alerts")])
    items = await run(llm)
    assert ToolEvent("list_alerts", False, "timed out") in items
    assert tool_messages(llm)[0].startswith("The tool timed out.")
