import { describe, expect, it } from 'vitest'
import { parseSSE, type SSEEvent } from './sse'

const encoder = new TextEncoder()

function streamOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk)
      controller.close()
    },
  })
}

async function collect(chunks: Uint8Array[]): Promise<SSEEvent[]> {
  const events: SSEEvent[] = []
  for await (const event of parseSSE(streamOf(chunks))) events.push(event)
  return events
}

// What the api sends: JSON data, a heartbeat comment, multi-byte text,
// and a token whose text contains blank lines.
const WIRE =
  'event: meta\ndata: {"request_id":"r1"}\n\n' +
  ': keep-alive\n\n' +
  'event: token\ndata: {"delta":"café ☕ — \\n\\nnext"}\n\n'

const EXPECTED = [
  { event: 'meta', data: '{"request_id":"r1"}' },
  { event: 'token', data: '{"delta":"café ☕ — \\n\\nnext"}' },
]

describe('parseSSE', () => {
  it('parses events and skips comments', async () => {
    expect(await collect([encoder.encode(WIRE)])).toEqual(EXPECTED)
  })

  it('gives the same events wherever the network splits the bytes', async () => {
    const bytes = encoder.encode(WIRE)
    for (let cut = 1; cut < bytes.length; cut++) {
      expect(await collect([bytes.slice(0, cut), bytes.slice(cut)])).toEqual(EXPECTED)
    }
  })

  it('survives one byte per chunk, including split UTF-8 characters', async () => {
    const bytes = encoder.encode(WIRE)
    const chunks = Array.from(bytes, (b) => Uint8Array.of(b))
    expect(await collect(chunks)).toEqual(EXPECTED)
  })

  it('accepts CRLF and CR line endings, even split between chunks', async () => {
    const crlf = encoder.encode('event: token\r\ndata: a\r\n\r\n')
    expect(await collect([crlf])).toEqual([{ event: 'token', data: 'a' }])
    const splitCRLF = [encoder.encode('data: b\r'), encoder.encode('\n\r\n')]
    expect(await collect(splitCRLF)).toEqual([{ event: 'message', data: 'b' }])
    expect(await collect([encoder.encode('data: c\r\r')])).toEqual([{ event: 'message', data: 'c' }])
  })

  it('joins multi-line data with newlines and keeps the id', async () => {
    const events = await collect([encoder.encode('id: 7\ndata: one\ndata:two\n\n')])
    expect(events).toEqual([{ event: 'message', data: 'one\ntwo', id: '7' }])
  })

  it('drops an event whose terminating blank line never arrives', async () => {
    expect(await collect([encoder.encode('data: complete\n\ndata: cut off\n')])).toEqual([
      { event: 'message', data: 'complete' },
    ])
  })

  it('cancels the body when the reader stops early', async () => {
    let cancelled = false
    const endless = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(encoder.encode('data: x\n\n'))
      },
      cancel() {
        cancelled = true
      },
    })
    for await (const event of parseSSE(endless)) {
      expect(event.data).toBe('x')
      break
    }
    expect(cancelled).toBe(true)
  })
})
