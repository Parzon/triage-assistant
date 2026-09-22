import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RecentAlerts } from './RecentAlerts'

function renderWithQuery() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RecentAlerts />
    </QueryClientProvider>,
  )
}

describe('RecentAlerts', () => {
  it('lists alerts newest first as the api returns them', async () => {
    const items = [
      { id: 2, source: 'grafana', severity: 'critical', message: 'p95 > 2s', created_at: '2026-09-22T18:00:00Z' },
      { id: 1, source: 'prometheus', severity: 'info', message: 'deploy done', created_at: '2026-09-22T17:00:00Z' },
    ]
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items, next_cursor: null }))))
    renderWithQuery()
    const rows = await screen.findAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('critical')
    expect(rows[0]).toHaveTextContent('p95 > 2s')
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
