"""The seam between this service and whichever model answers.

Routes and the triage logic only see LLMClient: an async stream of text
deltas (plus one Usage at the end) and a small set of typed errors.
Swapping providers means another class with the same two methods; nothing
else in the service changes.
"""

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast

import openai
from openai import AsyncOpenAI, Timeout, omit
from openai.types.chat import ChatCompletionMessageParam
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from app.config import Settings
from app.models import EMBEDDING_DIM
from app.tracing import messages_json, provider_attributes, text_parts

tracer = trace.get_tracer(__name__)


@dataclass(frozen=True)
class PromptRef:
    """Which prompt produced a call: a name, a version people bump, and the
    hash of the template's text, which nobody has to remember to bump. On
    the model's span (gen_ai.prompt.*) and in eval reports."""

    name: str
    version: str
    sha256: str

    @classmethod
    def of(cls, name: str, template: str, version: str | None = None) -> "PromptRef":
        """No version: the hash's first 12 characters serve as one."""
        sha256 = hashlib.sha256(template.encode()).hexdigest()
        return cls(name, version or sha256[:12], sha256)


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class Finish:
    """Why the provider stopped: "stop" (the answer is complete), "length"
    (the output limit cut it), or another reason it reports
    ("content_filter"). Reasoning models spend hidden tokens first: a
    "length" with no text at all means they used the whole budget thinking."""

    reason: str


class LLMError(Exception):
    """Base for provider failures; `code` goes to the client as-is."""

    code = "llm_error"


class LLMTimeout(LLMError):
    code = "llm_timeout"


class LLMRateLimited(LLMError):
    code = "llm_rate_limited"


class LLMUnavailable(LLMError):
    code = "llm_unavailable"


class LLMEmptyAnswer(LLMError):
    code = "llm_empty_answer"


class LLMClient(Protocol):
    model: str

    def stream(
        self, messages: list[dict[str, str]], prompt: PromptRef | None = None
    ) -> AsyncIterator[str | Usage | Finish]: ...

    async def aclose(self) -> None: ...


class Embedder(Protocol):
    # None: embeddings are off (EMBEDDING_MODEL unset).
    embedding_model: str | None

    async def embed(
        self, texts: Sequence[str], *, timeout_s: float | None = None
    ) -> list[list[float]]: ...


@contextmanager
def _provider_errors(what: str) -> Iterator[None]:
    """The SDK maps transport failures to its own exceptions, both before
    and during a stream; these become ours. Order matters: APITimeoutError
    is a subclass of APIConnectionError."""
    try:
        yield
    except openai.APITimeoutError as exc:
        raise LLMTimeout(f"{what} took too long to respond") from exc
    except openai.RateLimitError as exc:
        raise LLMRateLimited("the model provider is rate limiting us") from exc
    except (openai.APIConnectionError, openai.InternalServerError) as exc:
        raise LLMUnavailable("the model provider is unavailable") from exc
    except openai.APIStatusError as exc:
        # 400/401/403/404: our request or configuration is wrong (key,
        # model name, parameters) - retrying will not help.
        raise LLMError(f"the model provider rejected the request ({exc.status_code})") from exc
    except openai.APIError as exc:  # e.g. an error event inside the stream
        raise LLMUnavailable("the model provider failed mid-answer") from exc


class OpenAICompatibleClient:
    def __init__(self, settings: Settings, *, temperature: float | None = None) -> None:
        self.model = settings.llm_model
        self._max_tokens = settings.llm_max_output_tokens
        # None = not sent: the provider's default, and the only value OpenAI's
        # reasoning models accept. The service leaves it unset; the eval
        # judge sends 0.
        self._temperature = temperature
        # Only when configured: non-reasoning models reject the parameter.
        self._extra: dict[str, str] = (
            {"reasoning_effort": settings.llm_reasoning_effort}
            if settings.llm_reasoning_effort
            else {}
        )
        self.embedding_model = settings.embedding_model
        self._embedding_dimensions = settings.embedding_dimensions
        self._provider = provider_attributes(settings.llm_base_url)
        self._trace_content = settings.trace_content
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            # The SDK's own Timeout type: openai 3.x ships its HTTP client as the
            # separate `httpx2` package - importing `httpx` here only worked in
            # dev, where httpx happened to be installed as a test tool.
            timeout=Timeout(
                connect=settings.llm_connect_timeout_s,
                read=settings.llm_read_timeout_s,
                write=10.0,
                pool=5.0,
            ),
            max_retries=settings.llm_max_retries,
        )

    async def stream(
        self, messages: list[dict[str, str]], prompt: PromptRef | None = None
    ) -> AsyncIterator[str | Usage | Finish]:
        # Started, never made current: this is an async generator, and a
        # span made current here would stay current in the caller's code
        # between chunks (and fail to detach when the caller closes the
        # generator from another context). The parent is whatever is current
        # when the stream starts: the request's span.
        span = tracer.start_span(
            f"chat {self.model}", kind=SpanKind.CLIENT, attributes=self._chat_attributes(prompt)
        )
        if self._trace_content:
            system = [m["content"] for m in messages if m["role"] == "system"]
            if system:
                span.set_attribute("gen_ai.system_instructions", text_parts(system[0]))
            span.set_attribute(
                "gen_ai.input.messages",
                messages_json([m for m in messages if m["role"] != "system"]),
            )
        timing = _StreamTiming(span)
        answer: list[str] = []
        finish_reasons: list[str] = []
        outcome = "cancelled"
        try:
            with _provider_errors("the model"):
                stream = await self._client.chat.completions.create(
                    model=self.model,
                    # The seam speaks plain role/content dicts (provider-neutral);
                    # the SDK's TypedDicts stay inside this adapter.
                    messages=cast("list[ChatCompletionMessageParam]", messages),
                    stream=True,
                    stream_options={"include_usage": True},
                    # max_tokens, not max_completion_tokens: the latter is
                    # OpenAI-only; every compatible server accepts max_tokens.
                    max_tokens=self._max_tokens,
                    temperature=omit if self._temperature is None else self._temperature,
                    extra_body=self._extra or None,
                )
                # Leaving this block (done, error, or cancelled because the
                # client hung up) closes the HTTP stream, which is what tells
                # the provider to stop generating - and billing. One generator,
                # not two nested: closing an outer one would leave an inner
                # one, and its stream, open until garbage collection.
                async with stream:
                    async for chunk in stream:
                        timing.chunk(chunk.id, chunk.model)
                        if chunk.usage is not None:
                            span.set_attributes(
                                {
                                    "gen_ai.usage.input_tokens": chunk.usage.prompt_tokens,
                                    "gen_ai.usage.output_tokens": chunk.usage.completion_tokens,
                                }
                            )
                            yield Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                        for choice in chunk.choices:
                            if choice.delta.content:
                                timing.content()
                                if self._trace_content:
                                    answer.append(choice.delta.content)
                                yield choice.delta.content
                            elif (choice.delta.model_extra or {}).get("reasoning"):
                                # Not in the OpenAI schema: Ollama streams a
                                # reasoning model's thinking in its own field,
                                # before the answer.
                                timing.reasoning_chunks += 1
                            if choice.finish_reason:
                                finish_reasons.append(choice.finish_reason)
                                yield Finish(choice.finish_reason)
            outcome = "ok"
        except LLMError as exc:
            outcome = exc.code
            span.set_attribute("error.type", exc.code)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        except (GeneratorExit, asyncio.CancelledError):
            raise  # the caller stopped reading: outcome stays "cancelled"
        except Exception as exc:
            outcome = "internal_error"
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
        finally:
            if finish_reasons:
                span.set_attribute("gen_ai.response.finish_reasons", finish_reasons)
            if answer:
                span.set_attribute(
                    "gen_ai.output.messages",
                    messages_json(
                        [{"role": "assistant", "content": "".join(answer)}],
                        finish_reasons[0] if finish_reasons else None,
                    ),
                )
            span.set_attributes(
                {
                    "app.llm.outcome": outcome,
                    "app.llm.reasoning_chunks": timing.reasoning_chunks,
                    "app.llm.content_chunks": timing.content_chunks,
                }
            )
            span.end()

    def _chat_attributes(self, prompt: PromptRef | None) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": self.model,
            "gen_ai.request.stream": True,
            "gen_ai.request.max_tokens": self._max_tokens,
            **self._provider,
        }
        if self._temperature is not None:
            attributes["gen_ai.request.temperature"] = self._temperature
        if "reasoning_effort" in self._extra:
            attributes["gen_ai.request.reasoning.level"] = self._extra["reasoning_effort"]
        if prompt is not None:
            attributes |= {
                "gen_ai.prompt.name": prompt.name,
                "gen_ai.prompt.version": prompt.version,
                "app.prompt.sha256": prompt.sha256,
            }
        return attributes

    async def embed(
        self, texts: Sequence[str], *, timeout_s: float | None = None
    ) -> list[list[float]]:
        """One vector per text, in order, EMBEDDING_DIM long each."""
        if self.embedding_model is None:
            raise LLMError("runbook search is off: EMBEDDING_MODEL is not set")
        client = self._client
        if timeout_s is not None:
            # On the chat's critical path: fail fast, no retries.
            client = client.with_options(timeout=timeout_s, max_retries=0)
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "embeddings",
            "gen_ai.request.model": self.embedding_model,
            "gen_ai.request.encoding_formats": ["float"],
            "app.embeddings.inputs": len(texts),
            **self._provider,
        }
        if self._embedding_dimensions is not None:
            attributes["gen_ai.embeddings.dimension.count"] = self._embedding_dimensions
        with tracer.start_as_current_span(
            f"embeddings {self.embedding_model}", kind=SpanKind.CLIENT, attributes=attributes
        ) as span:
            try:
                with _provider_errors("the embedding model"):
                    response = await client.embeddings.create(
                        model=self.embedding_model,
                        input=list(texts),
                        # Floats, not the SDK's base64 default (decoded with
                        # numpy when installed): every server speaks floats.
                        encoding_format="float",
                        dimensions=omit
                        if self._embedding_dimensions is None
                        else self._embedding_dimensions,
                    )
            except LLMError as exc:
                span.set_attribute("error.type", exc.code)
                raise
            span.set_attribute("gen_ai.response.model", response.model)
            if response.usage is not None:
                span.set_attribute("gen_ai.usage.input_tokens", response.usage.prompt_tokens)
        vectors = [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
        if len(vectors) != len(texts):
            raise LLMError(f"asked for {len(texts)} embeddings, got {len(vectors)}")
        if wrong := {len(v) for v in vectors} - {EMBEDDING_DIM}:
            raise LLMError(
                f"the embedding model returned {min(wrong)} dimensions; the database stores "
                f"{EMBEDDING_DIM}. Set EMBEDDING_DIMENSIONS={EMBEDDING_DIM} if the model can "
                "shorten its vectors (Matryoshka), or choose another model"
            )
        return vectors

    async def aclose(self) -> None:
        await self._client.close()


class _StreamTiming:
    """When the first chunk and the first answer text arrived, on the span.

    Time to first chunk (gen_ai.response.time_to_first_chunk) is how long
    the provider took to start; the "first token" event marks the first
    answer text. Between them, a reasoning model thinks: measured with
    gpt-oss:20b, its first chunks are reasoning, not answer."""

    def __init__(self, span: Span) -> None:
        self._span = span
        self._start = time.perf_counter()
        self._first_chunk = False
        self.reasoning_chunks = 0
        self.content_chunks = 0

    def chunk(self, response_id: str, model: str) -> None:
        if not self._first_chunk:
            self._first_chunk = True
            self._span.set_attributes(
                {
                    "gen_ai.response.time_to_first_chunk": time.perf_counter() - self._start,
                    "gen_ai.response.id": response_id,
                    "gen_ai.response.model": model,
                }
            )

    def content(self) -> None:
        if self.content_chunks == 0:
            self._span.add_event(
                "first token",
                {"app.llm.time_to_first_token": time.perf_counter() - self._start},
            )
        self.content_chunks += 1
