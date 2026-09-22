import { useState } from 'react'

// Reads a Server-Sent-Events-shaped stream from a POST response. Native
// EventSource can't do POST with a body, so this is the pattern real LLM
// streaming clients (OpenAI/Anthropic SDKs) actually use: fetch +
// ReadableStream, parsed by hand. Swap the backend's placeholder
// generator for a real model call later — this parsing logic doesn't
// change.
async function streamChat(message: string, onToken: (token: string) => void) {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  })
  if (!res.body) throw new Error('No response body — streaming not supported here')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const data = line.slice(6)
      if (data === '[DONE]') return
      onToken(data)
    }
  }
}

export default function App() {
  const [input, setInput] = useState('')
  const [output, setOutput] = useState('')
  const [status, setStatus] = useState<'idle' | 'streaming' | 'error'>('idle')

  async function handleSend() {
    if (!input.trim() || status === 'streaming') return
    setOutput('')
    setStatus('streaming')
    try {
      await streamChat(input, (token) => {
        setOutput((prev) => (prev ? `${prev} ${token}` : token))
      })
      setStatus('idle')
    } catch {
      setStatus('error')
    }
  }

  return (
    <main style={{ maxWidth: 640, margin: '2rem auto', fontFamily: 'sans-serif' }}>
      <h1>triage-assistant</h1>
      <p style={{ color: '#666' }}>
        Streaming chat scaffold — placeholder generator on the backend,
        real wiring for when an actual model call replaces it.
      </p>

      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          placeholder="Type a message..."
          style={{ flex: 1, padding: '0.5rem' }}
        />
        <button onClick={handleSend} disabled={status === 'streaming'}>
          {status === 'streaming' ? 'Streaming…' : 'Send'}
        </button>
      </div>

      {status === 'error' && (
        <p style={{ color: 'crimson' }}>Something went wrong — check the API is running.</p>
      )}

      <pre
        style={{
          marginTop: '1rem',
          padding: '1rem',
          background: '#f4f4f4',
          minHeight: '4rem',
          whiteSpace: 'pre-wrap',
        }}
      >
        {output || (status === 'idle' ? '' : '…')}
      </pre>
    </main>
  )
}
