import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { fetchAlerts, type Team } from '../lib/api'

// Server state belongs to TanStack Query (caching, polling, retries,
// loading/error states); no global store needed for it.
export function RecentAlerts({ teams }: { teams: Team[] }) {
  const [team, setTeam] = useState('') // '' = every team the user can see
  const { data, error, isPending } = useQuery({
    queryKey: ['alerts', 'recent', team],
    queryFn: () => fetchAlerts(20, team || undefined),
    refetchInterval: 5_000,
  })

  return (
    <section className="panel" aria-labelledby="alerts-title">
      <div className="panel-head">
        <h2 id="alerts-title">Recent alerts</h2>
        {teams.length > 1 && (
          <label>
            Team{' '}
            <select value={team} onChange={(e) => setTeam(e.target.value)}>
              <option value="">All my teams</option>
              {teams.map((t) => (
                <option key={t.slug} value={t.slug}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {isPending && <p className="muted">Loading…</p>}
      {error && <p className="error">Could not load alerts: {error.message}</p>}
      {data?.length === 0 && <p className="muted">No alerts yet.</p>}
      {data && data.length > 0 && (
        <ul className="alerts">
          {data.map((alert) => (
            <li key={alert.id}>
              <span className={`sev sev-${alert.severity}`}>{alert.severity}</span>
              <span className="team">{alert.team}</span>
              <span className="alert-text">
                <strong>{alert.source}</strong> {alert.message}
              </span>
              <time dateTime={alert.created_at}>{new Date(alert.created_at).toLocaleTimeString()}</time>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
