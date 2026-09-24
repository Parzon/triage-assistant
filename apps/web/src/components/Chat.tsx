import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { ApiError, type Citation, streamChat } from '../lib/api'

type Status = 'idle' | 'waiting' | 'streaming' | 'done' | 'stopped' | 'error'

interface Failure {
  code: string
  message: string
  requestId?: string
  retryAfterS?: number
}

interface Meta {
  requestId: string
  alertsInContext: number | null
  runbooksInContext: number
  retrieval: string | null
  citations?: Citation[]
  ttftMs?: number | null
  durationMs?: number | null
  truncated?: boolean
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
  const queryClient = useQueryClient()
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [status, setStatus] = useState<Status>('idle')
  const [failure, setFailure] = useState<Failure | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  // The agent's tool calls, as they happen: what it read before answering.
  const [steps, setSteps] = useState<string[]>([])
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
    setSteps([])
    setStatus('waiting')
    try {
      for await (const event of streamChat(message, controller.current.signal)) {
        if (event.type === 'meta') {
          setMeta({
            requestId: event.requestId,
            alertsInContext: event.alertsInContext,
            runbooksInContext: event.runbooksInContext,
            retrieval: event.retrieval,
          })
        } else if (event.type === 'tool') {
          const step = event.ok ? event.summary : `failed (${event.summary})`
          setSteps((s) => [...s, `${event.name}: ${step}`])
        } else if (event.type === 'token') {
          setStatus('streaming')
          // Tokens carry their own spacing and newlines: append verbatim.
          setAnswer((text) => text + event.delta)
        } else if (event.type === 'done') {
          setMeta(
            (m) =>
              m && {
                ...m,
                ttftMs: event.ttftMs,
                durationMs: event.durationMs,
                truncated: event.finishReason === 'length',
                citations: event.citations,
              },
          )
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
        // The session ended: re-ask who is signed in (shows the sign-in page).
        if (err.status === 401) void queryClient.invalidateQueries({ queryKey: ['me'] })
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
        {meta?.alertsInContext != null && ` · ${meta.alertsInContext} alerts in context`}
        {steps.map((step) => ` · ${step}`)}
        {meta?.retrieval && ` · ${meta.runbooksInContext} runbook sections`}
        {meta?.ttftMs != null && ` · first token ${Math.round(meta.ttftMs)} ms`}
      </p>

      <div className="answer" aria-live="polite" aria-busy={busy}>
        {answer}
      </div>
      {meta?.truncated && (
        <p className="muted">The answer was cut short: it reached the length limit.</p>
      )}
      {meta?.citations && meta.citations.length > 0 && (
        // "Referenced", not "Sources": an answer may name a section to say it
        // does not apply.
        <section aria-label="Referenced runbook sections" className="sources">
          <h3>Referenced runbook sections</h3>
          <ul>
            {meta.citations.map((c) => (
              <li key={c.ref}>
                <code>{c.ref}</code> {c.heading}
              </li>
            ))}
          </ul>
        </section>
      )}
      {meta?.retrieval === 'keyword_only' && (
        <p className="muted">
          Runbooks were searched by keyword only: the embedding model did not answer in time.
        </p>
      )}

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
