"""The spans of a real question (app/tracing.py, ADR-0018): one trace from
the request through retrieval to the model, the GenAI attributes on each
step, no content unless asked for, and the outcome of a hung-up answer."""

import asyncio
import json

import httpx
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from app.cli import mint_session
from app.config import Settings
from app.main import create_app
from tests.integration.conftest import BASE_URL, MockLLM, SignIn, signed_in, started
from tests.integration.test_chat import events_of
from tests.integration.test_runbooks import save

QUESTION = "db-1 disk full, what now? QUESTION-MARKER"
ALERT = "ALERT-MARKER db-1 disk 96% full; admin password is Winter2026!"
RUNBOOK = """Steps for a full disk. RUNBOOK-MARKER

## Free space
Delete old WAL archives after a successful backup.
"""
MARKERS = ("QUESTION-MARKER", "ALERT-MARKER", "RUNBOOK-MARKER", "Winter2026!")


def by_name(spans: InMemorySpanExporter) -> dict[str, ReadableSpan]:
    return {span.name: span for span in spans.get_finished_spans()}


def attrs(span: ReadableSpan) -> dict[str, object]:
    return dict(span.attributes or {})


def parent_id(span: ReadableSpan) -> int | None:
    return span.parent.span_id if span.parent else None


async def ask_with_context(sign_in_as: SignIn) -> list[tuple[str, dict[str, object]]]:
    admin = await sign_in_as("team:default:admin")
    await save(admin, "default", "Disk full", RUNBOOK)
    assert (
        await admin.post(
            "/alerts",
            json={
                "team": "default",
                "source": "prometheus",
                "severity": "critical",
                "message": ALERT,
            },
        )
    ).status_code == 201
    asker = await sign_in_as("team:default:viewer")
    return events_of((await asker.post("/chat/stream", json={"message": QUESTION})).text)


async def test_a_question_is_one_trace_from_request_to_model(
    sign_in_as: SignIn, mock_llm: MockLLM, spans: InMemorySpanExporter
) -> None:
    spans.clear()  # the runbook's save has its own trace
    events = await ask_with_context(sign_in_as)
    chat_trace = events[0][1]["trace_id"]
    finished = [
        s for s in spans.get_finished_spans() if format(s.context.trace_id, "032x") == chat_trace
    ]
    named = {s.name: s for s in finished}

    request = named["POST /chat/stream"]
    retrieval = named["retrieval runbooks"]
    embedding = named["embeddings mock-embed"]
    chat = named["chat mock-1"]
    assert request.kind is SpanKind.SERVER
    assert request.parent is None
    # Retrieval and the model are the request's children; the question's
    # embedding and the search's SQL are retrieval's.
    assert parent_id(retrieval) == request.context.span_id
    assert parent_id(chat) == request.context.span_id
    assert parent_id(embedding) == retrieval.context.span_id
    assert any(
        parent_id(s) == retrieval.context.span_id and attrs(s).get("db.system") == "postgresql"
        for s in finished
    )

    assert attrs(retrieval) | {"gen_ai.retrieval.documents": "-"} == attrs(retrieval) | {
        "gen_ai.operation.name": "retrieval",
        "gen_ai.data_source.id": "runbooks",
        "gen_ai.retrieval.top_k": 4,
        "app.retrieval.mode": "hybrid",
        "app.retrieval.requested_mode": "hybrid",
        "app.retrieval.hits": 2,
        "gen_ai.retrieval.documents": "-",
    }
    documents = json.loads(str(attrs(retrieval)["gen_ai.retrieval.documents"]))
    assert len(documents) == 2
    assert {"id", "score", "keyword_rank", "semantic_rank"} <= set(documents[0])

    assert attrs(embedding)["gen_ai.operation.name"] == "embeddings"
    assert attrs(embedding)["gen_ai.provider.name"] == "mock-llm"
    assert embedding.kind is SpanKind.CLIENT

    chat_attrs = attrs(chat)
    assert chat.kind is SpanKind.CLIENT
    assert {
        k: chat_attrs[k]
        for k in (
            "gen_ai.operation.name",
            "gen_ai.request.model",
            "gen_ai.response.model",
            "gen_ai.prompt.name",
            "gen_ai.prompt.version",
            "gen_ai.request.stream",
            "app.llm.outcome",
        )
    } == {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "mock-1",
        "gen_ai.response.model": "mock-1",
        "gen_ai.prompt.name": "triage",
        "gen_ai.prompt.version": "v6",
        "gen_ai.request.stream": True,
        "app.llm.outcome": "ok",
    }
    assert chat_attrs["gen_ai.response.finish_reasons"] == ("stop",)
    assert int(str(chat_attrs["gen_ai.usage.input_tokens"])) > 0
    assert int(str(chat_attrs["gen_ai.usage.output_tokens"])) > 0
    assert float(str(chat_attrs["gen_ai.response.time_to_first_chunk"])) > 0
    assert [e.name for e in chat.events] == ["first token"]

    assert {
        k: attrs(request)[k]
        for k in (
            "app.chat.outcome",
            "app.chat.retrieval",
            "app.prompt.sections",
            "app.prompt.alerts",
            "app.prompt.redactions",
        )
    } == {
        "app.chat.outcome": "ok",
        "app.chat.retrieval": "hybrid",
        "app.prompt.sections": 2,
        "app.prompt.alerts": 1,
        "app.prompt.redactions": 1,
    }


async def test_no_question_prompt_answer_or_runbook_text_reaches_a_span(
    sign_in_as: SignIn, mock_llm: MockLLM, spans: InMemorySpanExporter
) -> None:
    await ask_with_context(sign_in_as)
    recorded = json.dumps(
        [
            [attrs(s), [dict(e.attributes or {}) for e in s.events], s.name]
            for s in spans.get_finished_spans()
        ],
        default=str,
    )
    for marker in MARKERS:
        assert marker not in recorded, f"{marker} reached a span"


async def test_content_capture_records_redacted_content_when_turned_on(
    settings: Settings, mock_llm: MockLLM, spans: InMemorySpanExporter
) -> None:
    app = create_app(settings.model_copy(update={"trace_content": True}))
    async with started(app):
        admin = await signed_in(app, "team:default:admin")
        await save(admin, "default", "Disk full", RUNBOOK)
        await admin.post(
            "/alerts",
            json={
                "team": "default",
                "source": "prometheus",
                "severity": "critical",
                "message": ALERT,
            },
        )
        asker = await signed_in(app, "team:default:viewer")
        await asker.post("/chat/stream", json={"message": QUESTION})
        await admin.aclose()
        await asker.aclose()
    named = by_name(spans)
    chat = attrs(named["chat mock-1"])
    assert "QUESTION-MARKER" in str(chat["gen_ai.input.messages"])
    system = str(chat["gen_ai.system_instructions"])
    # The whole prompt: the alerts and the sections (a runbook's intro is a
    # section of its own, "Disk full").
    assert "ALERT-MARKER" in system
    assert "RUNBOOK-MARKER" in system
    # Redacted before the prompt, and again on the way into the span.
    assert "Winter2026!" not in system
    assert "[redacted]" in system
    assert "gen_ai.output.messages" in chat
    retrieval = attrs(named["retrieval runbooks"])
    assert "QUESTION-MARKER" in str(retrieval["gen_ai.retrieval.query.text"])
    assert set(retrieval["app.retrieval.headings"]) == {"Disk full > Free space", "Disk full"}  # type: ignore[call-overload]


async def test_a_failed_embedding_is_an_error_span_and_retrieval_says_why(
    sign_in_as: SignIn, mock_llm: MockLLM, spans: InMemorySpanExporter
) -> None:
    admin = await sign_in_as("team:default:admin")
    await save(admin, "default", "Disk full", RUNBOOK)
    await mock_llm.configure(embed_fail_mode="http_500")
    spans.clear()
    asker = await sign_in_as("team:default:viewer")
    events = events_of((await asker.post("/chat/stream", json={"message": QUESTION})).text)
    assert events[-1][0] == "done"  # the answer still comes, from keywords
    named = by_name(spans)
    embedding = named["embeddings mock-embed"]
    assert embedding.status.status_code is StatusCode.ERROR
    assert attrs(embedding)["error.type"] == "llm_unavailable"
    retrieval = attrs(named["retrieval runbooks"])
    assert (retrieval["app.retrieval.mode"], retrieval["app.retrieval.embedding_error"]) == (
        "keyword_only",
        "llm_unavailable",
    )


async def test_a_hung_up_answer_ends_its_model_span_as_cancelled(
    live_server: str,
    mock_llm: MockLLM,
    spans: InMemorySpanExporter,
) -> None:
    await mock_llm.configure(tokens_per_s=10)  # a ~5s answer
    name, token = (await mint_session("hangup@example.com", [], hours=0.1)).split("=", 1)
    async with (
        httpx.AsyncClient(
            base_url=live_server, cookies={name: token}, headers={"Origin": BASE_URL}
        ) as client,
        client.stream("POST", "/chat/stream", json={"message": "long answer please"}) as response,
    ):
        async for line in response.aiter_lines():
            if line.startswith("event: token"):
                break
    for _ in range(50):
        named = by_name(spans)
        if "chat mock-1" in named and "POST /chat/stream" in named:
            break
        await asyncio.sleep(0.05)
    assert attrs(named["chat mock-1"])["app.llm.outcome"] == "cancelled"
    assert attrs(named["POST /chat/stream"])["app.chat.outcome"] == "cancelled"
