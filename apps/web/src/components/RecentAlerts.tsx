import { useQuery } from '@tanstack/react-query'
import { fetchAlerts } from '../lib/api'

// Server state belongs to TanStack Query (caching, polling, retries,
// loading/error states); no global store needed for it.
export function RecentAlerts() {
  const { data, error, isPending } = useQuery({
    queryKey: ['alerts', 'recent'],
    queryFn: () => fetchAlerts(20),
    refetchInterval: 5_000,
  })

  return (
    <section className="panel" aria-labelledby="alerts-title">
      <h2 id="alerts-title">Recent alerts</h2>
      {isPending && <p className="muted">Loading…</p>}
      {error && <p className="error">Could not load alerts: {error.message}</p>}
      {data?.length === 0 && <p className="muted">No alerts yet.</p>}
      {data && data.length > 0 && (
        <ul className="alerts">
          {data.map((alert) => (
            <li key={alert.id}>
              <span className={`sev sev-${alert.severity}`}>{alert.severity}</span>
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
