import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { createAlert, type Severity, type Team } from '../lib/api'

const SEVERITIES: Severity[] = ['info', 'warning', 'high', 'critical']

/** Only rendered for users who may create alerts somewhere (responder or
 *  above). Hiding it is a courtesy; the api enforces the rule. */
export function NewAlert({ teams }: { teams: Team[] }) {
  const queryClient = useQueryClient()
  const [team, setTeam] = useState(teams[0].slug)
  const [severity, setSeverity] = useState<Severity>('warning')
  const [message, setMessage] = useState('')
  const create = useMutation({
    mutationFn: createAlert,
    onSuccess: () => {
      setMessage('')
      return queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (message.trim()) create.mutate({ team, severity, source: 'manual', message: message.trim() })
  }

  return (
    <section className="panel" aria-labelledby="new-alert-title">
      <h2 id="new-alert-title">New alert</h2>
      <form className="new-alert" onSubmit={submit}>
        <label>
          Team
          <select value={team} onChange={(e) => setTeam(e.target.value)}>
            {teams.map((t) => (
              <option key={t.slug} value={t.slug}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Severity
          <select value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
            {SEVERITIES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="grow">
          Message
          <input value={message} onChange={(e) => setMessage(e.target.value)} maxLength={4000} />
        </label>
        <button type="submit" disabled={create.isPending || !message.trim()}>
          Create
        </button>
      </form>
      {create.error && (
        <p className="error" role="alert">
          Could not create the alert: {create.error.message}
        </p>
      )}
    </section>
  )
}
