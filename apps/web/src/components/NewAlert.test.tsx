import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Team } from '../lib/api'
import { NewAlert } from './NewAlert'

const TEAMS: Team[] = [
  { slug: 'payments', name: 'Payments', role: 'responder' },
  { slug: 'platform', name: 'Platform', role: 'admin' },
]

function renderForm() {
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <NewAlert teams={TEAMS} />
    </QueryClientProvider>,
  )
  return { user: userEvent.setup(), invalidate }
}

describe('NewAlert', () => {
  it('creates an alert for the chosen team and refreshes the list', async () => {
    const created = { id: 9, team: 'platform', source: 'manual', severity: 'high', message: 'x', created_at: '' }
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(created), { status: 201 }))
    vi.stubGlobal('fetch', fetchMock)
    const { user, invalidate } = renderForm()
    await user.selectOptions(screen.getByLabelText('Team'), 'platform')
    await user.selectOptions(screen.getByLabelText('Severity'), 'high')
    await user.type(screen.getByLabelText('Message'), '  db failover  ')
    await user.click(screen.getByRole('button', { name: 'Create' }))
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['alerts'] }))
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/alerts')
    expect(JSON.parse(init.body as string)).toEqual({
      team: 'platform',
      severity: 'high',
      source: 'manual',
      message: 'db failover',
    })
    expect(screen.getByLabelText('Message')).toHaveValue('')
  })

  it('shows why the api refused', async () => {
    const refusal = { error: { code: 'forbidden', message: 'needs the responder role in this team', request_id: 'r' } }
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(refusal), { status: 403 })))
    const { user } = renderForm()
    await user.type(screen.getByLabelText('Message'), 'x')
    await user.click(screen.getByRole('button', { name: 'Create' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('needs the responder role in this team')
  })

  it('does not send an empty message', () => {
    renderForm()
    expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled()
  })
})
