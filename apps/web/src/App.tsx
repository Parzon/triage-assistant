import { Chat } from './components/Chat'
import { RecentAlerts } from './components/RecentAlerts'

export default function App() {
  return (
    <main className="layout">
      <header>
        <h1>triage-assistant</h1>
        <p className="muted">Incident alerts, and a model that answers questions about them.</p>
      </header>
      <Chat />
      <RecentAlerts />
    </main>
  )
}
