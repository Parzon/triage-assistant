import { useState } from 'react'
import { signOut, type Me } from '../lib/api'

export function Account({ me }: { me: Me }) {
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)

  async function leave() {
    setBusy(true)
    setFailed(false)
    try {
      // To the identity provider's sign-out page, which comes back here: its
      // own session would otherwise sign the user straight back in.
      window.location.assign(await signOut())
    } catch {
      setFailed(true)
      setBusy(false)
    }
  }

  return (
    <div className="account">
      <span className="who">{me.name ?? me.email}</span>
      <ul className="roles" aria-label="Your access">
        {me.org_admin ? (
          <li>org admin</li>
        ) : (
          me.teams.map((team) => (
            <li key={team.slug}>
              {team.slug}: {team.role}
            </li>
          ))
        )}
      </ul>
      <button type="button" className="secondary" onClick={leave} disabled={busy}>
        Sign out
      </button>
      {failed && (
        <span className="error" role="alert">
          Could not sign out, try again.
        </span>
      )}
    </div>
  )
}
