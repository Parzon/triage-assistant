import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import type { AssistantStatus, Me } from './lib/api'

const ALICE: Me = {
  id: 1,
  email: 'alice@example.com',
  name: 'Alice Payments',
  org_admin: false,
  teams: [
    { slug: 'payments', name: 'payments', role: 'responder' },
    { slug: 'platform', name: 'platform', role: 'viewer' },
  ],
}

const ON: AssistantStatus = { enabled: true, reason: null, changed_at: '2026-09-25T09:00:00Z' }

/** The api, by path: /api/me answers `me` (null = signed out, 401), and
 *  /api/assistant the off switch's state. */
function api(me: Me | null, assistant: AssistantStatus = ON) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/api/me') {
        return me ? new Response(JSON.stringify(me)) : new Response('{}', { status: 401 })
      }
      if (url === '/api/assistant') return new Response(JSON.stringify(assistant))
      return new Response(JSON.stringify({ items: [], next_cursor: null }))
    }),
  )
}

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  )
}

describe('App', () => {
  it('asks a signed-out visitor to sign in, and comes back to the same page', async () => {
    api(null)
    window.history.pushState({}, '', '/alerts/42')
    renderApp()
    const link = await screen.findByRole('link', { name: 'Sign in' })
    expect(link).toHaveAttribute('href', '/api/auth/login?next=%2Falerts%2F42')
    expect(screen.queryByLabelText('Question')).not.toBeInTheDocument()
    window.history.pushState({}, '', '/')
  })

  it('explains a failed sign-in in words, not codes', async () => {
    api(null)
    window.history.pushState({}, '', '/?auth_error=idp_unavailable')
    renderApp()
    expect(await screen.findByRole('alert')).toHaveTextContent('The sign-in service is unavailable')
    window.history.pushState({}, '', '/')
  })

  it('shows the signed-in user, their access, and what they may do', async () => {
    api(ALICE)
    renderApp()
    expect(await screen.findByText('Alice Payments')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Your access' })).toHaveTextContent('payments: responder')
    expect(screen.getByLabelText('Question')).toBeInTheDocument()
    // Responder in payments only: the form offers payments, not platform.
    const form = screen.getByRole('region', { name: 'New alert' })
    const team = within(form).getByRole('combobox', { name: 'Team' })
    expect([...team.querySelectorAll('option')].map((o) => o.value)).toEqual(['payments'])
  })

  it('hides alert creation from viewers', async () => {
    api({ ...ALICE, teams: [{ slug: 'platform', name: 'platform', role: 'viewer' }] })
    renderApp()
    await screen.findByText('Alice Payments')
    expect(screen.queryByRole('heading', { name: 'New alert' })).not.toBeInTheDocument()
  })

  it('tells someone in no team why the page is empty', async () => {
    api({ ...ALICE, teams: [] })
    renderApp()
    expect(await screen.findByText(/not in any team yet/)).toBeInTheDocument()
  })

  it('shows an org admin as such rather than every team', async () => {
    api({ ...ALICE, org_admin: true, teams: [{ slug: 'default', name: 'Default', role: 'admin' }] })
    renderApp()
    expect(await screen.findByRole('list', { name: 'Your access' })).toHaveTextContent('org admin')
  })

  it('tells everyone when the assistant is switched off, and why', async () => {
    api(ALICE, { enabled: false, reason: 'the model gives bad advice', changed_at: ON.changed_at })
    renderApp()
    expect(await screen.findByText(/switched off: the model gives bad advice/)).toBeInTheDocument()
    expect(screen.getByLabelText('Question')).toBeDisabled()
    expect(screen.queryByRole('region', { name: 'The assistant' })).not.toBeInTheDocument()
  })

  it('offers org admins the switch', async () => {
    api({ ...ALICE, org_admin: true })
    renderApp()
    expect(await screen.findByRole('region', { name: 'The assistant' })).toBeInTheDocument()
  })

  it('says so when the service cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('bad gateway', { status: 502 })))
    renderApp()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the service')
  })
})
