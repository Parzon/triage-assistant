"""Fixtures for tests that need the real stack (Postgres via PgBouncer, Redis).

They run inside the api container of the throwaway compose project that
`make test` starts, so connection settings come from the same environment
variables the service uses in production - through PgBouncer, as the
least-privilege app role.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text

from app.config import Settings
from app.main import create_app

ClientFactory = Callable[[str], AsyncClient]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        item.add_marker(pytest.mark.integration)


@pytest.fixture
def settings() -> Settings:
    if "DATABASE_URL" not in os.environ:
        pytest.skip("needs the compose stack: run `make test`")
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    async with started(app):
        async with app.state.engine.begin() as conn:
            await conn.execute(text("DELETE FROM alerts"))
        await app.state.redis.flushdb()
        yield app


def started(app: FastAPI):  # type: ignore[no-untyped-def]
    """Run the app's lifespan (engine, Redis client) around a test."""
    return app.router.lifespan_context(app)


@pytest.fixture
def client_for(app: FastAPI) -> ClientFactory:
    """A client whose requests come from a given IP, as the proxy-headers
    middleware would report it behind nginx."""

    def make(ip: str = "198.51.100.7") -> AsyncClient:
        transport = ASGITransport(app=app, client=(ip, 50000))
        return AsyncClient(transport=transport, base_url="http://test")

    return make


@pytest.fixture
async def client(client_for: ClientFactory) -> AsyncIterator[AsyncClient]:
    async with client_for("198.51.100.7") as c:
        yield c


@pytest.fixture
def with_redis(settings: Settings) -> Callable[[str], Settings]:
    return lambda url: settings.model_copy(update={"redis_url": SecretStr(url)})


@pytest.fixture
def with_database(settings: Settings) -> Callable[[str], Settings]:
    return lambda url: settings.model_copy(update={"database_url": SecretStr(url)})


@pytest.fixture
async def blackhole_port() -> AsyncIterator[int]:
    """A TCP server that accepts connections and never answers: what a hung
    dependency (frozen process, dropped packets) looks like to a client."""

    async def swallow(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while await reader.read(1024):
            pass
        writer.close()

    server = await asyncio.start_server(swallow, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()
    server.abort_clients()
    await server.wait_closed()


class MockLLM:
    """Drives tools/mock-llm's admin API: failure modes, timing, counters."""

    def __init__(self, admin_url: str) -> None:
        self._http = AsyncClient(base_url=admin_url, timeout=5)

    async def configure(self, **behaviour: object) -> None:
        (await self._http.post("/config", json=behaviour)).raise_for_status()

    async def stats(self) -> dict[str, int]:
        return dict((await self._http.get("/stats")).json())

    async def reset(self) -> None:
        (await self._http.post("/reset")).raise_for_status()


@pytest.fixture
async def mock_llm(settings: Settings) -> AsyncIterator[MockLLM]:
    mock = MockLLM(os.environ["MOCK_LLM_ADMIN_URL"])
    await mock.reset()
    await mock.configure(ttft_ms=50, tokens_per_s=500)  # fast unless a test slows it
    yield mock
    await mock.reset()
    await mock._http.aclose()
