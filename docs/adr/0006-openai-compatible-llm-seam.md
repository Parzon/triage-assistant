# ADR-0006: One OpenAI-compatible LLM client behind a small seam, with a mock provider

**Status:** accepted
**Date:** 2026-09-22

## Context

Every project built from this template calls a model, but which provider
and which model differ per project and change over time. Enterprise
teams usually reach models through a gateway (LiteLLM, Azure API
Management, an internal proxy); most gateways and self-hosted servers
(vLLM, Ollama) speak the OpenAI Chat Completions protocol.

Tests, load tests and failure drills need a model that is free, fast,
deterministic, available offline, and can be told to fail on demand.

## Decision

- `app/llm.py` defines the seam: `LLMClient.stream(messages)` yields text
  deltas and a final `Usage`; failures are `LLMTimeout`,
  `LLMRateLimited`, `LLMUnavailable` or `LLMError`. Routes and
  `app/triage.py` (the only module a project rewrites) see nothing else.
- One implementation: `OpenAICompatibleClient` on the official `openai`
  SDK, pointed anywhere with `LLM_BASE_URL` / `LLM_API_KEY` /
  `LLM_MODEL`. Explicit timeouts (connect 5 s, max silence between
  chunks 60 s, total answer 120 s) and one retry — the SDK defaults are a
  600 s read timeout and two retries. `max_tokens`, not
  `max_completion_tokens` (OpenAI-only).
- `tools/mock-llm`: an OpenAI-compatible server with realistic streaming
  and runtime-switchable failure modes (429, 500, hang, dropped
  connection) and counters (including streams the caller abandoned). It
  runs under the `mock` compose profile; tests, load tests and demos
  without a key use it.

## Alternatives considered

- **Provider-native SDKs (Anthropic, Bedrock) as the default.** Richer
  features (prompt caching, tool formats), but tie the template to one
  vendor. A native adapter is a second class behind the same seam when a
  project needs it.
- **An in-process fake instead of a mock server.** Simpler, but skips the
  real client path: HTTP connection pooling, SSE parsing of the provider
  stream, timeouts, retries, cancellation over a socket — exactly where
  the bugs live.
- **An orchestration framework (LangChain, LlamaIndex) as the seam.**
  Useful inside `app/triage.py` for a project that needs it; too much
  surface to impose on every project.

## Consequences

- Switching provider is configuration; switching protocol is one new
  adapter class plus an ADR.
- The mock must keep up with the protocol fields the client uses
  (`stream_options.include_usage`, chunk shape); its tests are the
  integration tests of the api.
- `import httpx` is not available in production: openai 3.x ships its
  HTTP client as the separate `httpx2` package. Use the SDK's own types
  (`openai.Timeout`) and exceptions.
