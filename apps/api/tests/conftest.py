"""Shared by the unit and integration tests: one in-memory trace store.

OpenTelemetry allows one tracer provider per process, and `make test` runs
both suites in one process, so it is installed here, once, before any app
starts (an app's lifespan then finds it and leaves it alone). Spans are
exported synchronously: a test can read them as soon as a request returns.
Every test runs with tracing on, as production can.
"""

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.config import Settings
from app.tracing import configure_tracing

SPANS = InMemorySpanExporter()

configure_tracing(
    Settings(
        database_url="postgresql://tests@unused/tests",  # type: ignore[arg-type]
        redis_url="redis://unused",  # type: ignore[arg-type]
        llm_base_url="http://unused/v1",
        llm_api_key="unused",  # type: ignore[arg-type]
        llm_model="unused",
        public_url="https://unused",
        otel_service_name="triage-assistant-tests",
        app_version="test",
    ),
    SimpleSpanProcessor(SPANS),
)


@pytest.fixture(autouse=True)
def spans() -> InMemorySpanExporter:
    """The spans finished during this test (cleared before each one)."""
    SPANS.clear()
    return SPANS
