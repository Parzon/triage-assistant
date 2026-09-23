"""Request context: request id, access log, per-request response headers.

A pure ASGI middleware rather than @app.middleware("http"): the decorator
form (BaseHTTPMiddleware) returns when the response *starts*, so for a
streamed response it would log the time to first byte as the duration.
Wrapping `send` sees the final body chunk, so durations are real.
"""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import error_response
from app.logs import request_id_var
from app.metrics import http_duration, http_in_progress, http_requests

log = logging.getLogger(__name__)
access_log = logging.getLogger("app.access")

# Accept an upstream id (nginx sets one per request) only if it looks like
# one; anything else is replaced, so clients cannot inject log content.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


def route_template(scope: Scope) -> str:
    """ "/alerts/{alert_id}", never "/alerts/123": raw paths as log or metric
    labels grow without bound (one series per id)."""
    route = scope.get("route")
    return getattr(route, "path", None) or "__unmatched__"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                extra = scope.get("state", {}).get("response_headers") or {}
                for name, value in extra.items():
                    headers[name] = value
            await send(message)

        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send_wrapper(message)

        http_in_progress.inc()
        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            # Handled here, not by an app-level Exception handler: Starlette
            # runs that one in its outermost middleware, after this one has
            # exited, so the 500 would lose its request id.
            log.error("unhandled exception", exc_info=exc)
            if started:
                raise  # mid-stream: too late to send a different response
            await error_response(500, "internal_error", "internal error")(
                scope, receive, send_wrapper
            )
        finally:
            http_in_progress.dec()
            duration = time.perf_counter() - start
            route = route_template(scope)
            if route != "/metrics":  # scrapes every 15s would drown real traffic
                http_requests.labels(scope["method"], route, str(status)).inc()
                http_duration.labels(scope["method"], route).observe(duration)
            # Who did what: the user id (not the email - logs are copied to
            # more places than the database, so they carry no personal data).
            principal = scope.get("state", {}).get("principal")
            access_log.info(
                "request",
                extra={
                    "method": scope["method"],
                    "route": route,
                    "status": status,
                    "duration_ms": round(duration * 1000, 2),
                    "client": (scope.get("client") or ("-",))[0],
                    "user_id": getattr(principal, "user_id", None),
                },
            )
            request_id_var.reset(token)
