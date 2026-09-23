"""Fixtures for tests that need the real stack (Postgres via PgBouncer, Redis).

They run inside the api container of the throwaway compose project that
`make test` starts, so connection settings come from the same environment
variables the service uses in production - through PgBouncer, as the
least-privilege app role.

Signed-in clients are made by sessions.sign_in - what the sign-in callback
runs once the identity provider has vouched for a user - so every test goes
through the real session, access and tenant code. test_auth_flow.py drives
the identity provider itself.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from app.oidc import Identity
from app.sessions import session_cookie, sign_in

# The app's address in these tests (PUBLIC_URL in compose.test.yaml): HTTPS,
# so the Secure session cookie behaves as in production.
BASE_URL = "https://test"
TEST_ISSUER = "https://idp.test"

# (groups..., email=, ip=) -> a client signed in with those groups.
SignIn = Callable[..., Awaitable[AsyncClient]]
ClientFactory = Callable[[str], AsyncClient]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        item.add_marker(pytest.mark.integration)


@pytest.fixture
def settings() -> Settings:
    if "DATABASE_URL" not in os.environ:
        pytest.skip("needs the compose stack: run `make test`")
    return Settings()  # type: ignore[call-arg]


async def reset_data(app: FastAPI) -> None:
    async with app.state.engine.begin() as conn:
        for table in ("alerts", "sessions", "login_requests", "memberships", "users"):
            await conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608 - fixed names
        await conn.execute(text("DELETE FROM teams WHERE slug <> 'default'"))
    await app.state.redis.flushdb()


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    async with started(app):
        await reset_data(app)
        yield app


def started(app: FastAPI):  # type: ignore[no-untyped-def]
    """Run the app's lifespan (engine, Redis client) around a test."""
    return app.router.lifespan_context(app)


async def signed_in(
    app: FastAPI, *groups: str, email: str | None = None, ip: str = "198.51.100.7"
) -> AsyncClient:
    """A client of `app` signed in as a user whose identity provider sent
    `groups` ("team:payments:responder", "org:admin"). The Origin header is
    the app's own, as a browser sends it; tests of the CSRF check drop it."""
    email = email or f"{'-'.join(groups) or 'nobody'}@example.com".replace(":", ".")
    async with app.state.sessionmaker() as db:
        token = await sign_in(
            db,
            issuer=TEST_ISSUER,
            identity=Identity(
                subject=email, email=email, name=email, groups=tuple(groups), id_token=None
            ),
            settings=app.state.settings,
        )
        await db.commit()
    client = AsyncClient(
        transport=ASGITransport(app=app, client=(ip, 50000)),
        base_url=BASE_URL,
        headers={"Origin": BASE_URL},
    )
    client.cookies.set(session_cookie(app.state.settings), token)
    return client


@pytest.fixture
async def sign_in_as(app: FastAPI) -> AsyncIterator[SignIn]:
    clients: list[AsyncClient] = []

    async def make(*groups: str, email: str | None = None, ip: str = "198.51.100.7") -> AsyncClient:
        client = await signed_in(app, *groups, email=email, ip=ip)
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()


@pytest.fixture
async def client(sign_in_as: SignIn) -> AsyncClient:
    """A responder of the default team: may read and create its alerts."""
    return await sign_in_as("team:default:responder")


@pytest.fixture
def anonymous(app: FastAPI) -> ClientFactory:
    """A client with no session, from a given IP."""

    def make(ip: str = "198.51.100.7") -> AsyncClient:
        transport = ASGITransport(app=app, client=(ip, 50000))
        return AsyncClient(transport=transport, base_url=BASE_URL, headers={"Origin": BASE_URL})

    return make


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
