import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Chat } from './Chat'

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

async function ask(question = 'what broke?') {
  const user = userEvent.setup()
  render(<Chat />)
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

  it('reports an unreachable server plainly', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    await ask()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the server.')
  })

  it('does not send an empty question', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    render(<Chat />)
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
