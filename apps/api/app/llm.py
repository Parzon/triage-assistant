"""The seam between this service and whichever model answers.

Routes and the triage logic only see LLMClient: an async stream of text
deltas (plus one Usage at the end) and a small set of typed errors.
Swapping providers means another class with the same two methods; nothing
else in the service changes.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, cast

import openai
from openai import AsyncOpenAI, Timeout
from openai.types.chat import ChatCompletionMessageParam

from app.config import Settings


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int


class LLMError(Exception):
    """Base for provider failures; `code` goes to the client as-is."""

    code = "llm_error"


class LLMTimeout(LLMError):
    code = "llm_timeout"


class LLMRateLimited(LLMError):
    code = "llm_rate_limited"


class LLMUnavailable(LLMError):
    code = "llm_unavailable"


class LLMClient(Protocol):
    model: str

    def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str | Usage]: ...

    async def aclose(self) -> None: ...


class OpenAICompatibleClient:
    def __init__(self, settings: Settings) -> None:
        self.model = settings.llm_model
        self._max_tokens = settings.llm_max_output_tokens
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

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str | Usage]:
        try:
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
            )
            # Leaving this block (done, error, or cancelled because the
            # client hung up) closes the HTTP stream, which is what tells
            # the provider to stop generating - and billing.
            async with stream:
                async for chunk in stream:
                    if chunk.usage is not None:
                        yield Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                    for choice in chunk.choices:
                        if choice.delta.content:
                            yield choice.delta.content
        # The SDK maps transport failures to its own exceptions both before
        # and during the stream. Order matters: APITimeoutError is a
        # subclass of APIConnectionError.
        except openai.APITimeoutError as exc:
            raise LLMTimeout("the model took too long to respond") from exc
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

    async def aclose(self) -> None:
        await self._client.close()
