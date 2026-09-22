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
from httpx import AsyncClient

from app.config import Settings
from app.main import create_app
from tests.integration.conftest import ClientFactory, MockLLM

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


async def test_answer_streams_intact_with_context(
    client_for: ClientFactory, mock_llm: MockLLM
) -> None:
    async with client_for("192.0.2.60") as client:
        await client.post("/alerts", json={"source": "p", "severity": "high", "message": "a"})
        await client.post("/alerts", json={"source": "p", "severity": "info", "message": "b"})
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
    client_for: ClientFactory, mock_llm: MockLLM, fail_mode: str, code: str
) -> None:
    await mock_llm.configure(fail_mode=fail_mode)
    async with client_for("192.0.2.61") as client:
        status, body = await ask(client)
    assert status == 200  # the stream had already started (meta)
    name, data = events_of(body)[-1]
    assert (name, data["code"]) == ("error", code)
    assert data["request_id"]


async def test_heartbeats_keep_the_stream_alive_while_waiting(
    client_for: ClientFactory, mock_llm: MockLLM
) -> None:
    await mock_llm.configure(fail_mode="hang")
    async with client_for("192.0.2.62") as client:
        _, body = await ask(client)
    assert body.count(": keep-alive") >= 3  # 0.2s heartbeats during the 1s read timeout


async def test_connection_dropped_mid_answer_keeps_partial_output(
    client_for: ClientFactory, mock_llm: MockLLM
) -> None:
    await mock_llm.configure(fail_mode="drop_mid_stream")
    async with client_for("192.0.2.63") as client:
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
        AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("192.0.2.64", 1)), base_url="http://t"
        ) as client,
    ):
        _, body = await ask(client)
    assert events_of(body)[-1][1]["code"] == "llm_unavailable"
    assert (await mock_llm.stats())["requests"] == 3  # 1 try + 2 retries


async def test_bad_api_key_is_reported_not_retried(settings: Settings, mock_llm: MockLLM) -> None:
    from pydantic import SecretStr

    app = create_app(settings.model_copy(update={"llm_api_key": SecretStr("invalid-key")}))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("192.0.2.65", 1)), base_url="http://t"
        ) as client,
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
    async with (
        httpx.AsyncClient(base_url=live_server) as client,
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
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("192.0.2.70", 1)), base_url="http://t"
        ) as client,
    ):
        await app.state.redis.flushdb()
        responses = [await client.post("/chat/stream", json={"message": "hi"}) for _ in range(3)]
    assert [r.status_code for r in responses] == [200, 200, 429]
    assert responses[-1].headers["content-type"] == "application/json"
    assert responses[-1].json()["error"]["code"] == "rate_limited"


async def test_empty_message_is_rejected(client_for: ClientFactory) -> None:
    async with client_for("192.0.2.71") as client:
        response = await client.post("/chat/stream", json={"message": ""})
    assert response.status_code == 422
