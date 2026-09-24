"""Tracing's pure parts (app/tracing.py), the prompt's version, and trace ids
in log lines. The spans of a real request: tests/integration/test_tracing.py."""

import json
import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.agent import AGENT_PROMPT_REF
from app.llm import PromptRef
from app.logs import JsonFormatter
from app.tracing import MAX_CONTENT_CHARS, messages_json, provider_attributes, text_parts, trace_id
from app.triage import PROMPT, SYSTEM_PROMPT

# Each prompt's hash for each version. Changing a prompt changes its hash,
# and this test fails until its version is bumped: add the new pair here,
# and a row to the prompt history (docs/handbook/ai-engineering.md), with
# the eval runs before and after.
PROMPT_HASHES = {
    ("triage", "v6"): "98b3574d800acbd129f70e0744c11e9095f8b34c7ee2adb54357adec5289b840",
    ("triage-agent", "v1"): "99554ca00ec709c40be2932a6a3618bfe46a25271c73c02c9a7bfb13ad6fdd3e",
    ("triage-agent", "v2"): "40a7d1757488b6e0dc4e35644cee2169fba09263a5ac28969063f539cb998ff5",
}


@pytest.mark.parametrize("ref", [PROMPT, AGENT_PROMPT_REF], ids=lambda ref: ref.name)
def test_a_prompt_is_not_changed_without_a_new_version(ref: PromptRef) -> None:
    assert PROMPT_HASHES.get((ref.name, ref.version)) == ref.sha256, (
        f"The {ref.name} prompt changed, but its version is still {ref.version}: bump it, "
        f"and record {ref.sha256} for the new version in PROMPT_HASHES"
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


def test_an_unsampled_trace_hands_out_no_id() -> None:
    # A sampler that drops the trace still gives the request a valid id;
    # handed out, it would lead nowhere in Jaeger.
    record = logging.makeLogRecord({"name": "app.test", "levelname": "INFO", "msg": "hello"})
    unsampled = trace.NonRecordingSpan(
        trace.SpanContext(
            trace_id=0x0AF7651916CD43DD8448EB211C80319C,
            span_id=0xB7AD6B7169203331,
            is_remote=False,
            trace_flags=trace.TraceFlags(trace.TraceFlags.DEFAULT),
        )
    )
    with trace.use_span(unsampled):
        assert trace_id() is None
        assert "trace_id" not in json.loads(JsonFormatter().format(record))
