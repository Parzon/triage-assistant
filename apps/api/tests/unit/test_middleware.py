"""RequestContextMiddleware against a tiny app: no database needed."""

import json
import logging
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from app.errors import install_error_handlers
from app.middleware import RequestContextMiddleware


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    @app.get("/stream-then-boom")
    async def stream_then_boom() -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            yield b"first chunk\n"
            raise RuntimeError("mid-stream failure")

        return StreamingResponse(body())

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_generates_a_request_id_when_none_is_sent(client: httpx.AsyncClient) -> None:
    response = await client.get("/items/1")
    assert len(response.headers["x-request-id"]) == 32


async def test_echoes_a_valid_upstream_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/items/1", headers={"X-Request-ID": "nginx-0123456789abcdef"})
    assert response.headers["x-request-id"] == "nginx-0123456789abcdef"


@pytest.mark.parametrize("bad", ["short", 'x" injected="yes', "a" * 200])
async def test_replaces_malformed_request_ids(client: httpx.AsyncClient, bad: str) -> None:
    response = await client.get("/items/1", headers={"X-Request-ID": bad})
    assert response.headers["x-request-id"] != bad


async def test_access_log_uses_the_route_template(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.access")
    await client.get("/items/42")
    await client.get("/nowhere")
    routes = [r.__dict__["route"] for r in caplog.records if r.name == "app.access"]
    assert routes == ["/items/{item_id}", "__unmatched__"]


async def test_unhandled_error_is_a_json_500_carrying_the_request_id(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/boom", headers={"X-Request-ID": "trace-me-0123456789"})
    assert response.status_code == 500
    assert response.headers["x-request-id"] == "trace-me-0123456789"
    body = response.json()["error"]
    assert body == {
        "code": "internal_error",
        "message": "internal error",
        "request_id": "trace-me-0123456789",
    }
    assert "kaboom" not in json.dumps(body)


async def test_failure_after_streaming_started_is_not_masked() -> None:
    # Headers are already sent, so a 500 is impossible: the middleware must
    # re-raise (the server then cuts the connection) instead of swallowing
    # the error or sending a second response.
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="mid-stream failure"):
            await client.get("/stream-then-boom")
