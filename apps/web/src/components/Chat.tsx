import { useEffect, useRef, useState } from 'react'
import { ApiError, streamChat } from '../lib/api'

type Status = 'idle' | 'waiting' | 'streaming' | 'done' | 'stopped' | 'error'

interface Failure {
  code: string
  message: string
  requestId?: string
  retryAfterS?: number
}

interface Meta {
  requestId: string
  alertsInContext: number
  ttftMs?: number | null
  durationMs?: number | null
}

const STATUS_TEXT: Record<Status, string> = {
  idle: '',
  waiting: 'Thinking…',
  streaming: 'Answering…',
  done: 'Done',
  stopped: 'Stopped',
  error: 'Failed',
}

export function Chat() {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [status, setStatus] = useState<Status>('idle')
  const [failure, setFailure] = useState<Failure | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const controller = useRef<AbortController | null>(null)
  const busy = status === 'waiting' || status === 'streaming'

  // Leaving the page mid-answer stops the stream (and the model behind it).
  useEffect(() => () => controller.current?.abort(), [])

  async function ask() {
    const message = question.trim()
    if (!message || busy) return
    controller.current = new AbortController()
    setAnswer('')
    setFailure(null)
    setMeta(null)
    setStatus('waiting')
    try {
      for await (const event of streamChat(message, controller.current.signal)) {
        if (event.type === 'meta') {
          setMeta({ requestId: event.requestId, alertsInContext: event.alertsInContext })
        } else if (event.type === 'token') {
          setStatus('streaming')
          // Tokens carry their own spacing and newlines: append verbatim.
          setAnswer((text) => text + event.delta)
        } else if (event.type === 'done') {
          setMeta((m) => m && { ...m, ttftMs: event.ttftMs, durationMs: event.durationMs })
          setStatus('done')
        } else {
          setFailure({ code: event.code, message: event.message, requestId: event.requestId })
          setStatus('error')
        }
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        setStatus('stopped')
      } else if (err instanceof ApiError) {
        setFailure({ code: err.code, message: err.message, requestId: err.requestId, retryAfterS: err.retryAfterS })
        setStatus('error')
      } else {
        setFailure({ code: 'network_error', message: 'Could not reach the server.' })
        setStatus('error')
      }
    }
  }

  return (
    <section className="panel" aria-labelledby="chat-title">
      <h2 id="chat-title">Ask about the alerts</h2>
      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault()
          void ask()
        }}
      >
        <input
          aria-label="Question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="What is on fire right now?"
          maxLength={4000}
        />
        {/* Distinct keys = two DOM nodes. Without them React reuses one
            <button> and flips its type: the abort re-renders it to "submit"
            while the Stop click is still being dispatched, and the browser
            then submits the form - Stop would start a new question. */}
        {busy ? (
          <button key="stop" type="button" onClick={() => controller.current?.abort()}>
            Stop
          </button>
        ) : (
          <button key="ask" type="submit" disabled={!question.trim()}>
            Ask
          </button>
        )}
      </form>

      <p className="status" role="status">
        {STATUS_TEXT[status]}
        {meta && ` · ${meta.alertsInContext} alerts in context`}
        {meta?.ttftMs != null && ` · first token ${Math.round(meta.ttftMs)} ms`}
      </p>

      <div className="answer" aria-live="polite" aria-busy={busy}>
        {answer}
      </div>

      {failure && (
        <div className="error" role="alert">
          <strong>{failure.message}</strong>
          {failure.retryAfterS != null && <span> Try again in {failure.retryAfterS}s.</span>}
          <div className="error-meta">
            code <code>{failure.code}</code>
            {failure.requestId && (
              <>
                {' '}
                · request id <code>{failure.requestId}</code>
              </>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
