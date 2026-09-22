import { parseSSE } from './sse'

/** Every api failure, normalised: the envelope the api sends, or what we
 *  can tell from the response when it isn't JSON (e.g. an nginx 502 page). */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId?: string
  readonly retryAfterS?: number

  constructor(status: number, code: string, message: string, requestId?: string, retryAfterS?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
    this.retryAfterS = retryAfterS
  }
}

export async function toApiError(res: Response): Promise<ApiError> {
  let code = `http_${res.status}`
  let message = res.statusText || 'request failed'
  let requestId = res.headers.get('x-request-id') ?? undefined
  try {
    const body = await res.json()
    code = body.error?.code ?? code
    message = body.error?.message ?? message
    requestId = body.error?.request_id ?? requestId
  } catch {
    // Not JSON: keep what the status line and headers say.
  }
  const retryAfter = Number(res.headers.get('retry-after'))
  return new ApiError(
    res.status,
    code,
    message,
    requestId,
    Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined,
  )
}

export type ChatEvent =
  | { type: 'meta'; requestId: string; model: string; alertsInContext: number }
  | { type: 'token'; delta: string }
  | { type: 'done'; ttftMs: number | null; durationMs: number | null; completionTokens: number | null }
  | { type: 'error'; code: string; message: string; requestId: string }

/**
 * POSTs a question and yields the answer as it streams. Errors before the
 * stream starts (rate limit, validation, database down) throw ApiError;
 * errors after it started arrive as an `error` event. Aborting the signal
 * stops the stream and closes the connection.
 */
export async function* streamChat(message: string, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ message }),
    signal,
  })
  if (!res.ok || !res.body) throw await toApiError(res)

  let finished = false
  for await (const { event, data } of parseSSE(res.body)) {
    const payload = JSON.parse(data)
    switch (event) {
      case 'meta':
        yield {
          type: 'meta',
          requestId: payload.request_id,
          model: payload.model,
          alertsInContext: payload.alerts_in_context,
        }
        break
      case 'token':
        yield { type: 'token', delta: payload.delta }
        break
      case 'done':
        finished = true
        yield {
          type: 'done',
          ttftMs: payload.ttft_ms,
          durationMs: payload.duration_ms,
          completionTokens: payload.usage?.completion_tokens ?? null,
        }
        break
      case 'error':
        finished = true
        yield { type: 'error', code: payload.code, message: payload.message, requestId: payload.request_id }
        break
    }
  }
  // The connection closed without the api's closing event: a worker was
  // killed, a proxy timed out, or a deploy cut the stream. Without this the
  // UI would wait forever on a stream that is gone.
  if (!finished) {
    throw new ApiError(0, 'stream_incomplete', 'The answer was cut off before it finished.')
  }
}

export type Severity = 'info' | 'warning' | 'high' | 'critical'

export interface Alert {
  id: number
  source: string
  severity: Severity
  message: string
  created_at: string
}

export async function fetchAlerts(limit = 20): Promise<Alert[]> {
  const res = await fetch(`/api/alerts?limit=${limit}`)
  if (!res.ok) throw await toApiError(res)
  const page: { items: Alert[] } = await res.json()
  return page.items
}
