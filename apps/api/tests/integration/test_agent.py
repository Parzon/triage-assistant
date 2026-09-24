"""CHAT_MODE=agent end to end: the mock model calls both tools, then answers
(tools/mock-llm). The tools read as the asker, and each call is recorded."""

import hashlib

import pytest
from fastapi import FastAPI

from app.config import Settings
from tests.integration.conftest import MockLLM, SignIn
from tests.integration.test_audit import trail, user_id
from tests.integration.test_chat import ask, events_of
from tests.integration.test_runbooks import save


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"chat_mode": "agent"})


async def test_the_agent_reads_only_what_the_asker_may_and_each_call_is_recorded(
    app: FastAPI, sign_in_as: SignIn, mock_llm: MockLLM
) -> None:
    writer = await sign_in_as("team:ops:admin", "team:vault:admin")
    ids = {}
    for team, message in (("ops", "db-1 disk at 96%"), ("vault", "the vault was unsealed")):
        alert = {"team": team, "source": "prom", "severity": "critical", "message": message}
        response = await writer.post("/alerts", json=alert)
        assert response.status_code == 201
        ids[team] = response.json()["id"]
    runbook = await save(writer, "ops", "Disk full")

    asker = await sign_in_as("team:ops:viewer")
    question = "db-1's disk is full, what do I do?"
    status, body = await ask(asker, question)

    assert status == 200
    events = events_of(body)
    assert events[0][1]["mode"] == "agent"
    tools = [data for name, data in events if name == "tool"]
    assert [(t["name"], t["ok"]) for t in tools] == [
        ("list_alerts", True),
        ("search_runbooks", True),
    ]
    answer = "".join(str(data["delta"]) for name, data in events if name == "token")
    assert "I can see 1 recent alert(s)" in answer  # not the vault team's
    done = events[-1][1]
    assert (done["model_calls"], done["tool_calls"]) == (2, 2)
    assert [c["runbook_id"] for c in done["citations"]] == [runbook["id"]]  # type: ignore[attr-defined]

    calls = await trail(app, "tool.called", actor_user_id=await user_id(asker))
    by_tool = {e.detail["tool"]: e.detail for e in calls}
    assert by_tool["list_alerts"]["alerts"] == [ids["ops"]]
    search = by_tool["search_runbooks"]
    assert search["query_sha256"] == hashlib.sha256(question.encode()).hexdigest()
    assert {s["runbook_id"] for s in search["sections"]} == {runbook["id"]}
    (asked,) = await trail(app, "chat.asked", actor_user_id=await user_id(asker))
    assert asked.detail["mode"] == "agent"
