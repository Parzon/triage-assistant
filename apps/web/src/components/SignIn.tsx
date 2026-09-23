import { signInUrl } from '../lib/api'

// The api redirects here with ?auth_error=<code> when a sign-in fails; the
// codes are the api's (app/routes/auth.py), the wording is for people.
const ERRORS: Record<string, string> = {
  access_denied: 'Sign-in was cancelled, or your account may not use this application.',
  invalid_state: 'That sign-in was already used, or started in another browser. Please sign in again.',
  expired: 'The sign-in took too long. Please try again.',
  idp_unavailable: 'The sign-in service is unavailable right now. Please try again in a moment.',
  login_failed: 'Sign-in failed. Please try again; if it keeps failing, tell the administrators.',
}

export function SignIn() {
  const error = new URLSearchParams(window.location.search).get('auth_error')
  return (
    <main className="layout">
      <header>
        <h1>triage-assistant</h1>
        <p className="muted">Incident alerts, and a model that answers questions about them.</p>
      </header>
      <section className="panel" aria-labelledby="signin-title">
        <h2 id="signin-title">Sign in</h2>
        <p>Use your organisation account. You will see the alerts of your teams.</p>
        {error && (
          <p className="error" role="alert">
            {ERRORS[error] ?? ERRORS.login_failed}
          </p>
        )}
        <a className="button" href={signInUrl()}>
          Sign in
        </a>
      </section>
    </main>
  )
}
