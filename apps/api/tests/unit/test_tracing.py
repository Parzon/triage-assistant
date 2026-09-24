"""Tracing's pure parts (app/tracing.py), the prompt's version, and trace ids
in log lines. The spans of a real request: tests/integration/test_tracing.py."""

import json
import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.llm import PromptRef
from app.logs import JsonFormatter
from app.tracing import MAX_CONTENT_CHARS, messages_json, provider_attributes, text_parts, trace_id
from app.triage import PROMPT, PROMPT_VERSION, SYSTEM_PROMPT

# SYSTEM_PROMPT's hash for each version. Changing the prompt changes the
# hash, and this test fails until the version is bumped: add the new pair
# here, and a row to the prompt history (docs/handbook/ai-engineering.md),
# with the eval runs before and after.
PROMPT_HASHES = {
    "v6": "98b3574d800acbd129f70e0744c11e9095f8b34c7ee2adb54357adec5289b840",
}


def test_the_prompt_is_not_changed_without_a_new_version() -> None:
    assert PROMPT.version == PROMPT_VERSION
    assert PROMPT_HASHES.get(PROMPT_VERSION) == PROMPT.sha256, (
        f"SYSTEM_PROMPT changed, but PROMPT_VERSION is still {PROMPT_VERSION}: bump it, and "
        f"record {PROMPT.sha256} for the new version in PROMPT_HASHES"
    )


def test_a_prompt_without_a_version_is_versioned_by_its_hash() -> None:
    ref = PromptRef.of("judge", "You check one answer.")
    assert ref.version == ref.sha256[:12]
    assert PromptRef.of("triage", SYSTEM_PROMPT, "v6").version == "v6"


@pytest.mark.parametrize(
    ("base_url", "provider", "address", "port"),
    [
        ("https://api.openai.com/v1", "openai", "api.openai.com", 443),
        (
            "https://acme.openai.azure.com/openai/v1",
            "azure.ai.openai",
            "acme.openai.azure.com",
            443,
        ),
        (
            "https://bedrock-runtime.eu-west-1.amazonaws.com/v1",
            "aws.bedrock",
            "bedrock-runtime.eu-west-1.amazonaws.com",
            443,
        ),
        # Anything else is named by its host: the conventions allow a custom value.
        ("http://ollama:11434/v1", "ollama", "ollama", 11434),
        ("http://mock-llm:8020/v1", "mock-llm", "mock-llm", 8020),
    ],
)
def test_the_provider_is_named_from_its_endpoint(
    base_url: str, provider: str, address: str, port: int
) -> None:
    assert provider_attributes(base_url) == {
        "gen_ai.provider.name": provider,
        "server.address": address,
        "server.port": port,
    }


def test_content_is_shaped_as_the_conventions_say_and_redacted() -> None:
    shaped = json.loads(
        messages_json([{"role": "user", "content": "is password=Winter2026! still valid?"}], "stop")
    )
    assert shaped == [
        {
            "role": "user",
            "parts": [{"type": "text", "content": "is password=[redacted] still valid?"}],
            "finish_reason": "stop",
        }
    ]
    assert json.loads(text_parts("be concise")) == [{"type": "text", "content": "be concise"}]


def test_long_content_is_cut_and_says_by_how_much() -> None:
    content = json.loads(text_parts("x" * (MAX_CONTENT_CHARS + 25)))[0]["content"]
    assert content.startswith("x" * MAX_CONTENT_CHARS)
    assert content.endswith("... [25 more characters]")


def test_log_lines_carry_the_trace_they_belong_to(spans: InMemorySpanExporter) -> None:
    record = logging.makeLogRecord({"name": "app.test", "levelname": "INFO", "msg": "hello"})
    assert "trace_id" not in json.loads(JsonFormatter().format(record))
    assert trace_id() is None
    with trace.get_tracer(__name__).start_as_current_span("work") as span:
        payload = json.loads(JsonFormatter().format(record))
        assert payload["trace_id"] == trace_id() == format(span.get_span_context().trace_id, "032x")
        assert payload["span_id"] == format(span.get_span_context().span_id, "016x")
