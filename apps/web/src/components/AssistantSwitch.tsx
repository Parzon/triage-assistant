import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { type AssistantStatus, setAssistant } from '../lib/api'

/** Org admins: switch the assistant off, with a reason everyone sees, and
 *  back on. It takes effect on every server at once, without a deploy. */
export function AssistantSwitch({ status }: { status: AssistantStatus | undefined }) {
  const queryClient = useQueryClient()
  const [reason, setReason] = useState('')
  const turn = useMutation({
    mutationFn: (next: { enabled: boolean; reason?: string }) => setAssistant(next.enabled, next.reason),
    onSuccess: (updated) => {
      queryClient.setQueryData(['assistant'], updated)
      setReason('')
    },
  })
  if (!status) return null

  return (
    <section className="panel" aria-labelledby="switch-title">
      <h2 id="switch-title">The assistant</h2>
      {status.enabled ? (
        <form
          className="ask"
          onSubmit={(e) => {
            e.preventDefault()
            turn.mutate({ enabled: false, reason: reason.trim() })
          }}
        >
          <input
            aria-label="Reason for switching it off"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why - everyone who asks will read this"
            maxLength={300}
          />
          <button type="submit" disabled={!reason.trim() || turn.isPending}>
            Switch off
          </button>
        </form>
      ) : (
        <div className="ask">
          <p>Switched off: {status.reason}</p>
          <button type="button" onClick={() => turn.mutate({ enabled: true })} disabled={turn.isPending}>
            Switch on
          </button>
        </div>
      )}
      <p className="muted">
        Off refuses every question before any model call, on every server at once. Alerts and runbooks keep
        working.
      </p>
      {turn.error && (
        <p className="error" role="alert">
          {turn.error.message}
        </p>
      )}
    </section>
  )
}
