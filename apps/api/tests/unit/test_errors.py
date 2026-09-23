"""Which database errors mean "unavailable, retry" (503) and which are bugs (500)."""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError

from app.errors import connection_failed, install_error_handlers
from app.middleware import RequestContextMiddleware


class DriverError(Exception):
    """Stands in for the driver's exception: asyncpg's carry a SQLSTATE."""

    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("driver error")
        self.sqlstate = sqlstate


def dbapi_error(sqlstate: str | None, *, invalidated: bool = False) -> DBAPIError:
    return DBAPIError("SELECT 1", None, DriverError(sqlstate), connection_invalidated=invalidated)


@pytest.mark.parametrize(
    ("sqlstate", "unavailable"),
    [
        ("08003", True),  # connection does not exist: PgBouncer closed it mid-query
        ("08P01", True),  # PgBouncer's own refusals ("server login has been failing")
        ("08006", True),  # connection failure
        ("57P01", True),  # admin shutdown
        ("57P03", True),  # cannot connect now: starting up
        ("53300", True),  # too many connections
        ("23505", False),  # unique violation: the request's fault
        ("42601", False),  # syntax error: our bug
        ("57014", False),  # statement timeout: an OperationalError, handled on its own
        (None, False),
    ],
)
def test_classifies_by_sqlstate(sqlstate: str | None, unavailable: bool) -> None:
    assert connection_failed(dbapi_error(sqlstate)) is unavailable


def test_an_invalidated_connection_is_unavailable_whatever_the_sqlstate() -> None:
    assert connection_failed(dbapi_error(None, invalidated=True))


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/raise/{sqlstate}")
    async def raise_(sqlstate: str) -> None:
        raise dbapi_error(sqlstate)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_connection_loss_is_a_503(client: httpx.AsyncClient) -> None:
    response = await client.get("/raise/08003")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["error"]["code"] == "database_unavailable"


async def test_a_sql_bug_stays_a_500(client: httpx.AsyncClient) -> None:
    response = await client.get("/raise/42601")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
