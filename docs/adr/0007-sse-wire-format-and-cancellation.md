# ADR-0007: Streaming answers — JSON SSE events, heartbeats, and cancellation to the provider

**Status:** accepted — supersedes the streaming paragraph of ADR-0001
**Date:** 2026-09-22

## Context

ADR-0001 claimed the streaming plumbing would never change when a real
model replaced the placeholder. The audit showed otherwise: events were
raw text (`data: <word>`), so a token containing a blank line — every
markdown answer has them — would end the event early; the client re-added
spaces between tokens, which real tokens already carry; and nginx buffered
the whole response in production.

Two more requirements appear with real models: a model can think for
tens of seconds before its first token (proxies and load balancers cut
idle connections at ~60 s), and a user who closes the tab must stop the
provider generating tokens we pay for.

## Decision

- **Events carry JSON on one `data:` line**: `meta` (request id, model,
  alerts in context — sent immediately), `token` `{delta}`, `done`
  `{usage, ttft_ms, duration_ms}`, `error` `{code, message, request_id}`.
  JSON escapes newlines, so no content can break the framing. Tokens are
  appended verbatim.
- **Heartbeats**: an SSE comment (`: keep-alive`) after 15 s of silence.
- **Errors before the stream** (rate limit, validation, database down) are
  normal JSON errors; **errors after it started** are an `error` event.
- **Cancellation**: a producer task reads the model into a small queue;
  the response generator drains it. On disconnect or abort the producer
  is cancelled, which closes the provider HTTP stream. `SSEResponse`
  wraps the body in `contextlib.aclosing` because Starlette's
  `async for` never closes the generator itself. `X-Accel-Buffering: no`
  on every stream, and a dedicated nginx location with
  `proxy_buffering off`.
- **Client**: `fetch` + a spec-level SSE parser (`apps/web/src/lib/sse.ts`)
  that buffers partial lines and partial UTF-8 characters; an
  `AbortController` behind the Stop button.

## Alternatives considered

- **WebSockets.** Two-way, but the client never needs to push mid-answer;
  harder to run behind ordinary HTTP load balancers and proxies.
- **EventSource.** GET only — the question would have to go in the URL
  (length limits, logs).
- **Non-streaming responses.** Simplest; a 10–30 s blank screen per
  answer.

## Consequences

Verified end to end: tokens arrive incrementally through the production
nginx in a real browser; clicking Stop cancels the provider stream within
a second (the mock counts it); every byte-split of the wire format parses
identically. Any proxy added later (an ingress, a CDN, a corporate
gateway) must not buffer `text/event-stream` and must allow idle gaps of
at least the heartbeat interval.
