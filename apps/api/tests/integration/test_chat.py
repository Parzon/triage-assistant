"""CHAT_RATE_LIMIT=3 in compose.test.yaml."""

from tests.integration.conftest import ClientFactory


async def test_streams_server_sent_events_unbuffered(client_for: ClientFactory) -> None:
    async with (
        client_for("192.0.2.50") as client,
        client.stream("POST", "/chat/stream", json={"message": "hello there"}) as response,
    ):
        body = (await response.aread()).decode()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["ratelimit-limit"] == "3"
    assert body.endswith("data: [DONE]\n\n")


async def test_chat_is_rate_limited(client_for: ClientFactory) -> None:
    async with client_for("192.0.2.51") as client:
        statuses = []
        for _ in range(4):
            response = await client.post("/chat/stream", json={"message": "hi"})
            statuses.append(response.status_code)
    assert statuses == [200, 200, 200, 429]


async def test_empty_message_is_rejected(client_for: ClientFactory) -> None:
    async with client_for("192.0.2.52") as client:
        response = await client.post("/chat/stream", json={"message": ""})
    assert response.status_code == 422
