import { useQuery } from '@tanstack/react-query'
import { Account } from './components/Account'
import { Chat } from './components/Chat'
import { NewAlert } from './components/NewAlert'
import { RecentAlerts } from './components/RecentAlerts'
import { SignIn } from './components/SignIn'
import { atLeast, fetchMe } from './lib/api'

export default function App() {
  // Who is signed in decides what renders. Any 401 later (session expired,
  // revoked, signed out in another tab) refetches this - see main.tsx.
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe, staleTime: 60_000 })

  if (me.isPending) {
    return (
      <main className="layout">
        <p className="muted">Loading…</p>
      </main>
    )
  }
  if (me.error) {
    return (
      <main className="layout">
        <h1>triage-assistant</h1>
        <p className="error" role="alert">
          Could not reach the service: {me.error.message}
        </p>
      </main>
    )
  }
  if (me.data === null) return <SignIn />

  const writable = me.data.teams.filter((team) => atLeast(team.role, 'responder'))
  return (
    <main className="layout">
      <header className="top">
        <div>
          <h1>triage-assistant</h1>
          <p className="muted">Incident alerts, and a model that answers questions about them.</p>
        </div>
        <Account me={me.data} />
      </header>
      {me.data.teams.length === 0 && (
        <p className="notice">
          You are not in any team yet, so there are no alerts to show. Ask your identity provider's
          administrators to add you to a team group.
        </p>
      )}
      <Chat />
      {writable.length > 0 && <NewAlert teams={writable} />}
      <RecentAlerts teams={me.data.teams} />
    </main>
  )
}
