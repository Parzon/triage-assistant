import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AssistantStatus } from '../lib/api'
import { AssistantSwitch } from './AssistantSwitch'

const ON: AssistantStatus = { enabled: true, reason: null, changed_at: '2026-09-25T09:00:00Z' }

function renderSwitch(status: AssistantStatus) {
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <AssistantSwitch status={status} />
    </QueryClientProvider>,
  )
  return client
}

/** The PUT the switch sends, answered with the state it asked for. */
function api() {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body))
    return new Response(JSON.stringify({ enabled: body.enabled, reason: body.reason, changed_at: ON.changed_at }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('AssistantSwitch', () => {
  it('switches the assistant off only with a reason', async () => {
    const fetchMock = api()
    const client = renderSwitch(ON)
    const user = userEvent.setup()
    expect(screen.getByRole('button', { name: 'Switch off' })).toBeDisabled()
    await user.type(screen.getByLabelText('Reason for switching it off'), 'bad answers since 9:00')
    await user.click(screen.getByRole('button', { name: 'Switch off' }))
    await waitFor(() => expect(client.getQueryData(['assistant'])).toMatchObject({ enabled: false }))
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/assistant')
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toEqual({ enabled: false, reason: 'bad answers since 9:00' })
  })

  it('switches it back on', async () => {
    const fetchMock = api()
    renderSwitch({ enabled: false, reason: 'provider incident', changed_at: ON.changed_at })
    expect(screen.getByText('Switched off: provider incident')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Switch on' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ enabled: true, reason: null })
  })

  it('shows why a switch failed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ error: { code: 'forbidden', message: 'needs org admin', request_id: 'rq' } }), {
          status: 403,
        }),
      ),
    )
    renderSwitch(ON)
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Reason for switching it off'), 'x')
    await user.click(screen.getByRole('button', { name: 'Switch off' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('needs org admin')
  })
})
