import { expect, test, type APIRequestContext } from '@playwright/test'

const MOCK_ADMIN = process.env.MOCK_ADMIN_URL ?? 'http://mock-llm:8020/_admin'

async function mock(request: APIRequestContext, config: Record<string, unknown>) {
  await request.post(`${MOCK_ADMIN}/reset`)
  if (Object.keys(config).length) await request.post(`${MOCK_ADMIN}/config`, { data: config })
}

async function mockStats(request: APIRequestContext) {
  return (await (await request.get(`${MOCK_ADMIN}/stats`)).json()) as Record<string, number>
}

test.afterEach(async ({ request }) => {
  await request.post(`${MOCK_ADMIN}/reset`)
})

test('the answer streams in through nginx, verbatim', async ({ page, request }) => {
  await mock(request, { ttft_ms: 300, tokens_per_s: 20 }) // ~2.5s answer
  await page.goto('/')
  await page.getByLabel('Question').fill('what is on fire?')
  await page.getByRole('button', { name: 'Ask' }).click()

  const answer = page.locator('.answer')
  await expect(answer).not.toBeEmpty()
  const early = (await answer.textContent())!.length
  // Still streaming, and more text arrives: buffering anywhere on the path
  // (nginx proxy_buffering, gzip, a CDN) would deliver it all at once.
  await expect(page.getByRole('button', { name: 'Stop' })).toBeVisible()
  await expect.poll(async () => (await answer.textContent())!.length).toBeGreaterThan(early)

  await expect(page.getByRole('status')).toContainText('Done')
  const text = (await answer.textContent())!
  expect(text).toMatch(/^Triage summary for: what is on fire\?\n\nI can see \d+ recent alert/)
  expect(text).toContain('(mock-llm reply: café ☕ ünïcödé check)')
})

test('Stop in the browser cancels the model call behind it', async ({ page, request }) => {
  await mock(request, { tokens_per_s: 4 }) // a ~12s answer
  await page.goto('/')
  await page.getByLabel('Question').fill('long answer please')
  await page.getByRole('button', { name: 'Ask' }).click()
  await expect(page.locator('.answer')).not.toBeEmpty()

  await page.getByRole('button', { name: 'Stop' }).click()
  await expect(page.getByRole('status')).toContainText('Stopped')
  // browser -> nginx -> api -> provider: the hang-up must reach the end.
  await expect.poll(async () => (await mockStats(request)).streams_cancelled, { timeout: 5_000 }).toBe(1)
  expect((await mockStats(request)).streams_completed).toBe(0)
})

test('a provider failure is shown with a request id', async ({ page, request }) => {
  await mock(request, { fail_mode: 'http_500' })
  await page.goto('/')
  await page.getByLabel('Question').fill('anything')
  await page.getByRole('button', { name: 'Ask' }).click()
  const alert = page.getByRole('alert')
  await expect(alert).toContainText('llm_unavailable')
  await expect(alert).toContainText(/request id [0-9a-f]{32}/)
})

test('new alerts appear in the panel', async ({ page, request }) => {
  const message = `e2e disk alert ${Date.now()}`
  const created = await request.post('/api/alerts', {
    data: { source: 'e2e', severity: 'critical', message },
  })
  expect(created.status()).toBe(201)
  await page.goto('/')
  await expect(page.getByRole('listitem').filter({ hasText: message })).toBeVisible()
})

test('the page loads with no console errors (CSP included)', async ({ page }) => {
  const errors: string[] = []
  page.on('console', (msg) => msg.type() === 'error' && errors.push(msg.text()))
  page.on('pageerror', (err) => errors.push(err.message))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'triage-assistant' })).toBeVisible()
  expect(errors).toEqual([])
})
