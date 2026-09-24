"""RequestContextMiddleware against a tiny app: no database needed."""

import json
import logging
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

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


def server_span(spans: InMemorySpanExporter) -> ReadableSpan:
    (span,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER]
    return span


async def test_each_request_is_a_span_named_after_its_route(
    client: httpx.AsyncClient, spans: InMemorySpanExporter
) -> None:
    response = await client.get("/items/42", headers={"X-Request-ID": "nginx-0123456789abcdef"})
    span = server_span(spans)
    # The template, not "/items/42": one name for every item.
    assert span.name == "GET /items/{item_id}"
    assert span.attributes is not None
    assert span.attributes["http.route"] == "/items/{item_id}"
    assert span.attributes["url.path"] == "/items/42"
    assert span.attributes["http.response.status_code"] == 200
    # The request id links the span to the log lines (make trace id=...).
    assert span.attributes["app.request_id"] == response.headers["x-request-id"]
    assert span.status.status_code is StatusCode.UNSET


async def test_a_callers_trace_context_is_joined(
    client: httpx.AsyncClient, spans: InMemorySpanExporter
) -> None:
    parent_trace, parent_span = "0af7651916cd43dd8448eb211c80319c", "b7ad6b7169203331"
    await client.get("/items/1", headers={"traceparent": f"00-{parent_trace}-{parent_span}-01"})
    span = server_span(spans)
    assert format(span.context.trace_id, "032x") == parent_trace
    assert span.parent is not None
    assert format(span.parent.span_id, "016x") == parent_span


async def test_a_500_marks_its_span_as_an_error_with_the_exception(
    client: httpx.AsyncClient, spans: InMemorySpanExporter
) -> None:
    await client.get("/boom")
    span = server_span(spans)
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes is not None
    assert span.attributes["http.response.status_code"] == 500
    assert [event.name for event in span.events] == ["exception"]


async def test_an_unmatched_path_keeps_the_method_as_its_name(
    client: httpx.AsyncClient, spans: InMemorySpanExporter
) -> None:
    await client.get("/nowhere/at/all")
    span = server_span(spans)
    assert span.name == "GET"
    assert span.attributes is not None
    assert "http.route" not in span.attributes
