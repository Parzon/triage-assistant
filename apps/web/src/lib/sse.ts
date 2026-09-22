export interface SSEEvent {
  event: string
  data: string
  id?: string
}

/**
 * Parses a Server-Sent Events byte stream (the parts of the spec the api
 * uses). Written by hand because the browser's EventSource can only GET,
 * and the chat request carries a JSON body.
 *
 * Network chunks do not line up with events: one read can hold half an
 * event, several events, or end in the middle of a multi-byte UTF-8
 * character. So bytes are decoded with `stream: true` (a split character
 * waits for its other half) and text is buffered until whole lines arrive.
 * Lines end in \n, \r\n or \r; a blank line dispatches the event; lines
 * starting with ":" are comments (the api's keep-alive heartbeats).
 *
 * Stopping early (break, or an aborted fetch) cancels the body, which
 * closes the connection - that is how the server learns the user left.
 */
export async function* parseSSE(body: ReadableStream<Uint8Array>): AsyncGenerator<SSEEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let event = 'message'
  let data: string[] = []
  let id: string | undefined

  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })

      let lineEnd: RegExpExecArray | null
      while ((lineEnd = /\r\n|\r|\n/.exec(buffer)) !== null) {
        // A trailing "\r" may be the first half of "\r\n": wait for more.
        if (lineEnd[0] === '\r' && lineEnd.index === buffer.length - 1 && !done) break
        const line = buffer.slice(0, lineEnd.index)
        buffer = buffer.slice(lineEnd.index + lineEnd[0].length)

        if (line === '') {
          if (data.length > 0) yield { event, data: data.join('\n'), id }
          event = 'message'
          data = []
          continue
        }
        if (line.startsWith(':')) continue

        const colon = line.indexOf(':')
        const field = colon === -1 ? line : line.slice(0, colon)
        let fieldValue = colon === -1 ? '' : line.slice(colon + 1)
        if (fieldValue.startsWith(' ')) fieldValue = fieldValue.slice(1)
        if (field === 'event') event = fieldValue
        else if (field === 'data') data.push(fieldValue)
        else if (field === 'id') id = fieldValue
      }
      // Per the spec, an event without its terminating blank line is dropped.
      if (done) return
    }
  } finally {
    await reader.cancel().catch(() => undefined)
  }
}
