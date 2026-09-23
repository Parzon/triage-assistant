import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Team } from '../lib/api'
import { RecentAlerts } from './RecentAlerts'

const TEAMS: Team[] = [
  { slug: 'payments', name: 'Payments', role: 'responder' },
  { slug: 'platform', name: 'Platform', role: 'viewer' },
]

function renderWithQuery(teams: Team[] = TEAMS) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RecentAlerts teams={teams} />
    </QueryClientProvider>,
  )
}

describe('RecentAlerts', () => {
  it('lists alerts newest first as the api returns them', async () => {
    const items = [
      { id: 2, team: 'payments', source: 'grafana', severity: 'critical', message: 'p95 > 2s', created_at: '2026-09-22T18:00:00Z' },
      { id: 1, team: 'platform', source: 'prometheus', severity: 'info', message: 'deploy done', created_at: '2026-09-22T17:00:00Z' },
    ]
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items, next_cursor: null }))))
    renderWithQuery()
    const rows = await screen.findAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('critical')
    expect(rows[0]).toHaveTextContent('payments')
    expect(rows[0]).toHaveTextContent('p95 > 2s')
  })

  it('narrows the list to one team', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ items: [], next_cursor: null })))
    vi.stubGlobal('fetch', fetchMock)
    renderWithQuery()
    await userEvent.setup().selectOptions(await screen.findByLabelText('Team'), 'platform')
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith('/api/alerts?limit=20&team=platform'))
  })

  it('offers no team filter to a member of one team', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [], next_cursor: null }))))
    renderWithQuery(TEAMS.slice(0, 1))
    await screen.findByText('No alerts yet.')
    expect(screen.queryByLabelText('Team')).not.toBeInTheDocument()
  })

  it('says so when there are none', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [], next_cursor: null }))))
    renderWithQuery()
    expect(await screen.findByText('No alerts yet.')).toBeInTheDocument()
  })

  it('shows a load failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('down', { status: 503 })))
    renderWithQuery()
    expect(await screen.findByText(/Could not load alerts/)).toBeInTheDocument()
  })
})
