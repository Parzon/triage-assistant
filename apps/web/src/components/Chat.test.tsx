import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Chat } from './Chat'

function renderChat(client = new QueryClient()) {
  render(
    <QueryClientProvider client={client}>
      <Chat />
    </QueryClientProvider>,
  )
  return client
}

const encoder = new TextEncoder()

/** A fake /api/chat/stream the test feeds event by event. Aborting the
 *  request errors the body, like a real fetch does. */
function controllableStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  let signal: AbortSignal | undefined
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c
    },
  })
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    signal = init?.signal ?? undefined
    signal?.addEventListener('abort', () => controller.error(new DOMException('aborted', 'AbortError')))
    return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
  })
  vi.stubGlobal('fetch', fetchMock)
  return {
    send: (event: string, data: object) => controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    close: () => controller.close(),
    aborted: () => signal?.aborted ?? false,
  }
}

async function ask(question = 'what broke?', client?: QueryClient) {
  const user = userEvent.setup()
  renderChat(client)
  await user.type(screen.getByLabelText('Question'), question)
  await user.click(screen.getByRole('button', { name: 'Ask' }))
  return user
}

const answer = () => document.querySelector('.answer')!.textContent

describe('Chat', () => {
  it('renders the answer as it streams, verbatim', async () => {
    const stream = controllableStream()
    await ask()
    expect(screen.getByRole('status')).toHaveTextContent('Thinking…')

    stream.send('meta', { request_id: 'r1', model: 'm', alerts_in_context: 4 })
    stream.send('token', { delta: 'Disk' })
    await waitFor(() => expect(answer()).toBe('Disk'))
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Answering… · 4 alerts in context')

    stream.send('token', { delta: ' is full\n\n- café' })
    stream.send('done', { usage: null, ttft_ms: 321.4, duration_ms: 900 })
    stream.close()
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Done'))
    expect(answer()).toBe('Disk is full\n\n- café')
    expect(screen.getByRole('status')).toHaveTextContent('first token 321 ms')
  })

  it('Stop aborts the request mid-answer and does not ask again', async () => {
    const stream = controllableStream()
    const user = await ask()
    stream.send('token', { delta: 'partial' })
    await waitFor(() => expect(answer()).toBe('partial'))

    await user.click(screen.getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Stopped'))
    expect(stream.aborted()).toBe(true)
    expect(answer()).toBe('partial')
    // Regression: Ask and Stop once shared one <button> DOM node; the abort
    // re-rendered it to type="submit" mid-click and the form re-submitted.
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('shows an error event with its request id', async () => {
    const stream = controllableStream()
    await ask()
    stream.send('error', { code: 'llm_timeout', message: 'the model took too long', request_id: 'req-77' })
    stream.close()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('the model took too long')
    expect(alert).toHaveTextContent('llm_timeout')
    expect(alert).toHaveTextContent('req-77')
  })

  it('does not hang when the stream is cut off without a closing event', async () => {
    const stream = controllableStream()
    await ask()
    stream.send('token', { delta: 'half an ans' })
    stream.close()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The answer was cut off before it finished.')
    expect(screen.getByRole('status')).toHaveTextContent('Failed')
    expect(answer()).toBe('half an ans')
  })

  it('explains a rate limit and when to retry', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ error: { code: 'rate_limited', message: 'rate limit exceeded', request_id: 'rq' } }), {
          status: 429,
          headers: { 'retry-after': '12' },
        }),
      ),
    )
    await ask()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('rate limit exceeded')
    expect(alert).toHaveTextContent('Try again in 12s.')
  })

  it('a session that ended re-checks who is signed in', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ error: { code: 'unauthenticated', message: 'sign in required', request_id: 'rq' } }), {
          status: 401,
        }),
      ),
    )
    const client = new QueryClient()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    await ask('anything', client)
    expect(await screen.findByRole('alert')).toHaveTextContent('sign in required')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['me'] })
  })

  it('says so when the answer was cut off by the length limit', async () => {
    const stream = controllableStream()
    await ask()
    stream.send('meta', { request_id: 'r1', model: 'm', alerts_in_context: 1 })
    stream.send('token', { delta: 'Partial' })
    stream.send('done', { usage: null, ttft_ms: 10, duration_ms: 20, finish_reason: 'length' })
    stream.close()
    expect(await screen.findByText(/cut short/)).toBeInTheDocument()
  })

  it('lists the runbook sections the answer cites', async () => {
    const stream = controllableStream()
    await ask()
    stream.send('meta', {
      request_id: 'r1',
      model: 'm',
      alerts_in_context: 1,
      runbooks_in_context: 2,
      retrieval: 'hybrid',
    })
    stream.send('token', { delta: 'Free space first [R1].' })
    stream.send('done', {
      usage: null,
      ttft_ms: 10,
      duration_ms: 20,
      finish_reason: 'stop',
      citations: [{ ref: 'R1', runbook_id: 7, title: 'Disk full', heading: 'Disk full > Free space' }],
      invalid_citations: [],
    })
    stream.close()
    const sources = await screen.findByRole('region', { name: 'Referenced runbook sections' })
    expect(sources).toHaveTextContent('R1 Disk full > Free space')
    expect(screen.getByRole('status')).toHaveTextContent('2 runbook sections')
  })

  it('says so when runbooks were searched by keyword only', async () => {
    const stream = controllableStream()
    await ask()
    stream.send('meta', {
      request_id: 'r1',
      model: 'm',
      alerts_in_context: 1,
      runbooks_in_context: 1,
      retrieval: 'keyword_only',
    })
    stream.close()
    expect(await screen.findByText(/searched by keyword only/)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Referenced runbook sections' })).not.toBeInTheDocument()
  })

  it('shows an empty answer from the model as an error, not as done', async () => {
    const stream = controllableStream()
    await ask()
    stream.send('meta', { request_id: 'r1', model: 'm', alerts_in_context: 1 })
    stream.send('error', { code: 'llm_empty_answer', message: 'the model returned no answer', request_id: 'r1' })
    stream.close()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('the model returned no answer')
    expect(alert).toHaveTextContent('llm_empty_answer')
  })

  it('reports an unreachable server plainly', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    await ask()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the server.')
  })

  it('does not send an empty question', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderChat()
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
