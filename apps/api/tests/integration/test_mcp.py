"""The MCP server (app/mcp_server.py), through an MCP client connected in
memory: the tools read as the person whose session it was given, for as
long as that session lives."""

import pytest
from fastapi import FastAPI
from mcp import Client, MCPError, types

from app.cli import mint_session, revoke
from app.config import Settings
from app.mcp_server import build_server
from tests.integration.conftest import MockLLM, SignIn
from tests.integration.test_audit import trail


def text_of(result: types.CallToolResult) -> str:
    return "".join(c.text for c in result.content if isinstance(c, types.TextContent))


async def test_a_client_reads_as_the_session_it_was_given_until_it_is_revoked(
    app: FastAPI, settings: Settings, sign_in_as: SignIn, mock_llm: MockLLM
) -> None:
    writer = await sign_in_as("team:ops:responder", "team:vault:responder")
    ids = {}
    for team, message in (("ops", "db-1 disk at 96%"), ("vault", "the vault was unsealed")):
        alert = {"team": team, "source": "prom", "severity": "critical", "message": message}
        response = await writer.post("/alerts", json=alert)
        assert response.status_code == 201
        ids[team] = response.json()["id"]
    email = "mcp-user@example.com"
    cookie = await mint_session(email, ["team:ops:viewer"], hours=1)
    server = build_server(settings, app.state.sessionmaker, app.state.llm, cookie)

    async with Client(server) as client:
        listed = await client.list_tools()
        assert {t.name for t in listed.tools} == {"list_alerts", "search_runbooks"}
        assert all(t.annotations and t.annotations.read_only_hint for t in listed.tools)

        result = await client.call_tool("list_alerts", {"severity": "critical"})
        assert not result.is_error
        assert "db-1 disk at 96%" in text_of(result)
        assert "vault" not in text_of(result)

        # A bad call is the model's to correct: a result, not a protocol error.
        bad = await client.call_tool("list_alerts", {"limit": 500})
        assert bad.is_error
        assert text_of(bad).startswith("Invalid arguments: limit")
        with pytest.raises(MCPError):
            await client.call_tool("delete_alerts", {})

        await revoke(email)
        after = await client.call_tool("list_alerts", {})
        assert after.is_error
        assert "expired or was revoked" in text_of(after)

    calls = [e for e in await trail(app, "tool.called") if e.via == "mcp"]
    assert [e.detail["alerts"] for e in calls if e.detail["tool"] == "list_alerts"][:1] == [
        [ids["ops"]]
    ]
