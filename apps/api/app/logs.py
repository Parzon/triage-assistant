"""Structured JSON logs on stdout, one object per line.

stdout because the container runtime collects it (docker logs, CloudWatch,
Loki) - files inside a container are invisible to all of them. JSON so a
log backend can filter on fields (request_id, route, status) instead of
grepping text. Every line carries the request id of the request that
produced it, so one request can be followed across nginx and the api, and,
when tracing is on, the trace id, which opens the same request in Jaeger.
"""

import json
import logging
import logging.config
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Attributes every LogRecord has; anything else was passed via `extra=`.
_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        span_context = trace.get_current_span().get_span_context()
        # Only a recorded trace: an unsampled one's id finds nothing.
        if span_context.is_valid and span_context.trace_flags.sampled:
            payload["trace_id"] = format(span_context.trace_id, "032x")
            payload["span_id"] = format(span_context.span_id, "016x")
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def logging_config(level: str) -> dict[str, Any]:
    """dictConfig shared by the app and gunicorn (see gunicorn.conf.py)."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": JsonFormatter}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            # The app writes its own access line (with route template and
            # request id); uvicorn's would duplicate every request.
            "uvicorn.access": {"handlers": [], "propagate": False},
            "uvicorn.error": {"handlers": ["stdout"], "level": level, "propagate": False},
            "gunicorn.error": {"handlers": ["stdout"], "level": level, "propagate": False},
            "gunicorn.access": {"handlers": [], "propagate": False},
            # Chatty below WARNING; raise to INFO to see every SQL statement.
            "sqlalchemy.engine": {"level": "WARNING"},
            # The openai SDK's HTTP client logs every request at INFO: one
            # line per model call, thousands under load. Our own
            # "chat answered" line already records each answer.
            "httpx2": {"level": "WARNING"},
            "httpx": {"level": "WARNING"},
        },
    }


def configure_logging(level: str) -> None:
    logging.config.dictConfig(logging_config(level))
