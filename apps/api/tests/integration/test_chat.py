"""/chat/stream against tools/mock-llm over real HTTP.

compose.test.yaml sets LLM_READ_TIMEOUT_S=1, LLM_MAX_RETRIES=0 and
SSE_HEARTBEAT_S=0.2 so the failure modes resolve in about a second.
"""

import asyncio
import json
import socket
from collections.abc import AsyncIterator

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from httpx import AsyncClient

from app.cli import mint_session
from app.config import Settings
from app.main import create_app
from tests.integration.conftest import BASE_URL, MockLLM, SignIn, signed_in

MOCK_REPLY_END = "(mock-llm reply: café ☕ ünïcödé check)"


def events_of(body: str) -> list[tuple[str, dict[str, object]]]:
    events = []
    for block in body.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if lines:
            name = lines[0].removeprefix("event: ")
            events.append((name, json.loads(lines[1].removeprefix("data: "))))
    return events


async def ask(client: AsyncClient, message: str = "what is on fire?") -> tuple[int, str]:
    response = await client.post("/chat/stream", json={"message": message})
    return response.status_code, response.text


async def test_answer_streams_intact_with_context(client: AsyncClient, mock_llm: MockLLM) -> None:
    for severity, message in (("high", "a"), ("info", "b")):
        alert = {"team": "default", "source": "p", "severity": severity, "message": message}
        assert (await client.post("/alerts", json=alert)).status_code == 201
    status, body = await ask(client)
    assert status == 200
    events = events_of(body)
    assert [name for name, _ in events][:2] == ["meta", "token"]
    assert events[0][1]["alerts_in_context"] == 2
    answer = "".join(str(data["delta"]) for name, data in events if name == "token")
    assert answer.startswith("Triage summary for: what is on fire?\n\nI can see 2 recent alert(s)")
    assert answer.endswith(MOCK_REPLY_END)
    done = events[-1]
    assert done[0] == "done"
    assert done[1]["usage"]["completion_tokens"] > 0  # type: ignore[index]


@pytest.mark.parametrize(
    ("fail_mode", "code"),
    [("http_429", "llm_rate_limited"), ("http_500", "llm_unavailable"), ("hang", "llm_timeout")],
)
async def test_provider_failures_become_error_events(
    client: AsyncClient, mock_llm: MockLLM, fail_mode: str, code: str
) -> None:
    await mock_llm.configure(fail_mode=fail_mode)
    status, body = await ask(client)
    assert status == 200  # the stream had already started (meta)
    name, data = events_of(body)[-1]
    assert (name, data["code"]) == ("error", code)
    assert data["request_id"]


async def test_heartbeats_keep_the_stream_alive_while_waiting(
    client: AsyncClient, mock_llm: MockLLM
) -> None:
    await mock_llm.configure(fail_mode="hang")
    _, body = await ask(client)
    assert body.count(": keep-alive") >= 3  # 0.2s heartbeats during the 1s read timeout


async def test_connection_dropped_mid_answer_keeps_partial_output(
    client: AsyncClient, mock_llm: MockLLM
) -> None:
    await mock_llm.configure(fail_mode="drop_mid_stream")
    _, body = await ask(client)
    events = events_of(body)
    assert any(name == "token" for name, _ in events)
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "llm_unavailable"


async def test_retries_happen_before_the_first_token(settings: Settings, mock_llm: MockLLM) -> None:
    await mock_llm.configure(fail_mode="http_500")
    app = create_app(settings.model_copy(update={"llm_max_retries": 2}))
    async with (
        app.router.lifespan_context(app),
        await signed_in(app, "team:default:viewer") as client,
    ):
        _, body = await ask(client)
    assert events_of(body)[-1][1]["code"] == "llm_unavailable"
    assert (await mock_llm.stats())["requests"] == 3  # 1 try + 2 retries


async def test_bad_api_key_is_reported_not_retried(settings: Settings, mock_llm: MockLLM) -> None:
    from pydantic import SecretStr

    app = create_app(settings.model_copy(update={"llm_api_key": SecretStr("invalid-key")}))
    async with (
        app.router.lifespan_context(app),
        await signed_in(app, "team:default:viewer") as client,
    ):
        _, body = await ask(client)
    assert events_of(body)[-1][1]["code"] == "llm_error"


@pytest.fixture
async def live_server(settings: Settings) -> AsyncIterator[str]:
    """The app on a real socket: needed to observe a real client hang-up."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(500):  # bounded: a server that never starts fails, not hangs
        if server.started:
            break
        await asyncio.sleep(0.01)
    else:
        raise RuntimeError("test server did not start")
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    server.should_exit = True
    await task


async def test_client_hang_up_cancels_the_provider_stream(
    live_server: str, mock_llm: MockLLM
) -> None:
    await mock_llm.configure(tokens_per_s=10)  # a ~5s answer
    name, token = (await mint_session("hangup@example.com", [], hours=0.1)).split("=", 1)
    async with (
        httpx.AsyncClient(
            base_url=live_server, cookies={name: token}, headers={"Origin": BASE_URL}
        ) as client,
        client.stream("POST", "/chat/stream", json={"message": "long answer please"}) as response,
    ):
        async for line in response.aiter_lines():
            if line.startswith("event: token"):
                break  # read one token, then hang up like a closed tab
    for _ in range(50):  # the provider side should notice within ~a second
        stats = await mock_llm.stats()
        if stats["streams_cancelled"]:
            break
        await asyncio.sleep(0.05)
    assert stats["streams_cancelled"] == 1
    assert stats["streams_completed"] == 0
    assert stats["active_streams"] == 0


async def test_rate_limit_is_a_json_429_before_any_stream(
    settings: Settings, mock_llm: MockLLM
) -> None:
    app = create_app(settings.model_copy(update={"chat_rate_limit": 2}))
    async with app.router.lifespan_context(app):
        await app.state.redis.flushdb()
        async with await signed_in(app, "team:default:viewer") as client:
            responses = [
                await client.post("/chat/stream", json={"message": "hi"}) for _ in range(3)
            ]
    assert [r.status_code for r in responses] == [200, 200, 429]
    assert responses[-1].headers["content-type"] == "application/json"
    assert responses[-1].json()["error"]["code"] == "rate_limited"


async def test_empty_message_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/chat/stream", json={"message": ""})
    assert response.status_code == 422


async def test_no_database_connection_is_held_while_the_answer_streams(
    app: FastAPI, sign_in_as: SignIn, mock_llm: MockLLM
) -> None:
    """The request's database session closes when the route returns
    (DbSession, scope="function"), not when the response has been sent - for
    a stream that is a minute later, and every stream would hold a pooled
    connection all that time: 20 slow answers would exhaust a worker's pool."""
    await mock_llm.configure(tokens_per_s=20)  # a ~2.5s answer
    client = await sign_in_as("team:default:viewer")
    async with client.stream("POST", "/chat/stream", json={"message": "slow please"}) as response:
        async for line in response.aiter_lines():
            if line.startswith("event: token"):
                assert app.state.engine.pool.checkedout() == 0
                break


async def test_an_empty_answer_is_an_error_event(client: AsyncClient, mock_llm: MockLLM) -> None:
    # What a reasoning model does when thinking uses the whole output limit.
    await mock_llm.configure(fail_mode="empty_answer")
    _, body = await ask(client)
    name, data = events_of(body)[-1]
    assert (name, data["code"]) == ("error", "llm_empty_answer")


async def test_an_answer_cut_by_the_output_limit_is_flagged(
    settings: Settings, mock_llm: MockLLM
) -> None:
    app = create_app(settings.model_copy(update={"llm_max_output_tokens": 5}))
    async with (
        app.router.lifespan_context(app),
        await signed_in(app, "team:default:viewer") as client,
    ):
        _, body = await ask(client)
    name, data = events_of(body)[-1]
    assert (name, data["finish_reason"]) == ("done", "length")
