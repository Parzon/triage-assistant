"""One error shape for every failure: {"error": {code, message, request_id}}.

The request id is what a user pastes into a bug report; it finds the
matching log lines in nginx and the api. Unhandled exceptions are turned
into a generic 500 by RequestContextMiddleware (logged with traceback,
never returned to the client).
"""

import logging
import socket
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException

from app.logs import request_id_var

log = logging.getLogger(__name__)

_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 429: "rate_limited"}

# Connection-level failures only: the database is down, unreachable, too
# slow, or the pool is exhausted. Constraint violations and SQL bugs are
# not "unavailable" and fall through to their own handling / the 500.
# socket.gaierror: a *stopped* container disappears from Docker's DNS, so
# the failure is a name-resolution error, not "connection refused".
DATABASE_UNAVAILABLE = (
    OperationalError,
    InterfaceError,
    PoolTimeoutError,
    ConnectionError,
    socket.gaierror,
    TimeoutError,
)


def error_response(
    status: int, code: str, message: str, headers: dict[str, str] | None = None, **fields: Any
) -> JSONResponse:
    body = {
        "error": {"code": code, "message": message, "request_id": request_id_var.get(), **fields}
    }
    return JSONResponse(body, status_code=status, headers=headers)


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, HTTPException):  # registered for HTTPException only
        raise exc
    code = _CODES.get(exc.status_code, "error")
    return error_response(exc.status_code, code, str(exc.detail), dict(exc.headers or {}))


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    return error_response(
        422, "validation_error", "request body or parameters are invalid", details=exc.errors()
    )


async def _database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    log.error("database unavailable", extra={"error": type(exc).__name__})
    return error_response(
        503, "database_unavailable", "database unavailable, retry shortly", {"Retry-After": "5"}
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    for exc_type in DATABASE_UNAVAILABLE:
        app.add_exception_handler(exc_type, _database_unavailable)
