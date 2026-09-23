import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Me } from '../lib/api'
import { Account } from './Account'

const ME: Me = { id: 1, email: 'a@example.com', name: null, org_admin: false, teams: [] }

describe('Account', () => {
  it('signs out here, then at the identity provider', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ logout_url: 'https://idp.example/logout?x=1' })))
    vi.stubGlobal('fetch', fetchMock)
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    render(<Account me={ME} />)
    expect(screen.getByText('a@example.com')).toBeInTheDocument() // no name: the email
    await userEvent.setup().click(screen.getByRole('button', { name: 'Sign out' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/logout', { method: 'POST' })
    expect(assign).toHaveBeenCalledWith('https://idp.example/logout?x=1')
  })

  it('says so when signing out fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 503 })))
    render(<Account me={ME} />)
    await userEvent.setup().click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not sign out')
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeEnabled()
  })
})
