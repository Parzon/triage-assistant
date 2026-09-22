import { describe, expect, it, vi } from 'vitest'
import { ApiError, fetchAlerts, streamChat, toApiError, type ChatEvent } from './api'

const sseBody = (text: string) =>
  new Response(text, { status: 200, headers: { 'content-type': 'text/event-stream' } })

async function all(gen: AsyncGenerator<ChatEvent>): Promise<ChatEvent[]> {
  const out: ChatEvent[] = []
  for await (const event of gen) out.push(event)
  return out
}

describe('toApiError', () => {
  it('reads the api error envelope', async () => {
    const res = new Response(
      JSON.stringify({ error: { code: 'rate_limited', message: 'rate limit exceeded', request_id: 'req-1' } }),
      { status: 429, headers: { 'retry-after': '12' } },
    )
    const err = await toApiError(res)
    expect(err).toMatchObject({ status: 429, code: 'rate_limited', requestId: 'req-1', retryAfterS: 12 })
    expect(err.message).toBe('rate limit exceeded')
  })

  it('copes with a non-JSON body such as an nginx 502 page', async () => {
    const res = new Response('<html>502 Bad Gateway</html>', {
      status: 502,
      statusText: 'Bad Gateway',
      headers: { 'x-request-id': 'edge-42' },
    })
    const err = await toApiError(res)
    expect(err).toMatchObject({ status: 502, code: 'http_502', requestId: 'edge-42' })
    expect(err.retryAfterS).toBeUndefined()
  })
})

describe('streamChat', () => {
  it('turns SSE events into typed chat events', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        sseBody(
          'event: meta\ndata: {"request_id":"r","model":"m","alerts_in_context":3}\n\n' +
            'event: token\ndata: {"delta":"Hi\\n"}\n\n' +
            'event: done\ndata: {"usage":{"prompt_tokens":1,"completion_tokens":2},"ttft_ms":12.5,"duration_ms":40}\n\n',
        ),
      ),
    )
    expect(await all(streamChat('q'))).toEqual([
      { type: 'meta', requestId: 'r', model: 'm', alertsInContext: 3 },
      { type: 'token', delta: 'Hi\n' },
      { type: 'done', ttftMs: 12.5, durationMs: 40, completionTokens: 2 },
    ])
  })

  it('passes stream errors through as events', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        sseBody('event: error\ndata: {"code":"llm_timeout","message":"slow","request_id":"r9"}\n\n'),
      ),
    )
    expect(await all(streamChat('q'))).toEqual([
      { type: 'error', code: 'llm_timeout', message: 'slow', requestId: 'r9' },
    ])
  })

  it('throws ApiError when the request is refused before streaming', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ error: { code: 'rate_limited', message: 'no' } }), { status: 429 })),
    )
    await expect(all(streamChat('q'))).rejects.toBeInstanceOf(ApiError)
  })
})

describe('fetchAlerts', () => {
  it('returns the page items', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [{ id: 1 }], next_cursor: null }))))
    expect(await fetchAlerts()).toEqual([{ id: 1 }])
  })

  it('throws ApiError on failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('down', { status: 503 })))
    await expect(fetchAlerts()).rejects.toMatchObject({ status: 503 })
  })
})
