import { expect, test } from '@playwright/test'
import { PASSWORD } from '../users'

test.describe('signed out', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('signing in and out goes through the identity provider', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: 'Sign in' }).click()
    await page.locator('#username').fill('alice')
    await page.locator('#password').fill(PASSWORD)
    await page.locator('#kc-login').click()
    await expect(page.getByText('Alice Payments')).toBeVisible()
    await expect(page.getByRole('list', { name: 'Your access' })).toContainText('payments: responder')

    await page.getByRole('button', { name: 'Sign out' }).click()
    await expect(page.getByRole('link', { name: 'Sign in' })).toBeVisible()
    // The provider's session ended too: signing in asks for the password
    // again instead of silently signing alice back in.
    await page.getByRole('link', { name: 'Sign in' }).click()
    await expect(page.locator('#password')).toBeVisible()
  })

  test('the api refuses anyone not signed in', async ({ request }) => {
    const response = await request.get('/api/alerts')
    expect(response.status()).toBe(401)
    expect((await response.json()).error.code).toBe('unauthenticated')
  })

  test("the identity provider's admin side is not exposed", async ({ request }) => {
    for (const path of ['/auth/admin/', '/auth/admin/master/console/', '/auth/realms/master/account/']) {
      expect((await request.get(path)).status(), path).toBe(404)
    }
  })
})

test('each team sees only its own alerts', async ({ page, browser }) => {
  const paymentsOnly = `payments-only ${Date.now()}`
  await page.goto('/')
  const form = page.getByRole('region', { name: 'New alert' })
  await form.getByLabel('Message').fill(paymentsOnly)
  await form.getByRole('button', { name: 'Create' }).click()
  await expect(page.getByRole('listitem').filter({ hasText: paymentsOnly })).toBeVisible()

  const bob = await browser.newContext({ storageState: '.auth/bob.json' })
  const bobPage = await bob.newPage()
  await bobPage.goto('/')
  await expect(bobPage.getByText('Bob Platform')).toBeVisible()
  const platformNote = `platform-note ${Date.now()}`
  const bobForm = bobPage.getByRole('region', { name: 'New alert' })
  await bobForm.getByLabel('Message').fill(platformNote)
  await bobForm.getByRole('button', { name: 'Create' }).click()
  await expect(bobPage.getByRole('listitem').filter({ hasText: platformNote })).toBeVisible()
  // bob is not in payments: alice's alert is not his to see.
  await expect(bobPage.getByRole('listitem').filter({ hasText: paymentsOnly })).toHaveCount(0)
  await bob.close()

  // alice views platform: bob's alert reaches her list.
  await page.reload()
  await expect(page.getByRole('listitem').filter({ hasText: platformNote })).toBeVisible()
})

test('a request from another site is refused, even with the session cookie', async ({ page }) => {
  await page.goto('/')
  const response = await page.request.post('/api/alerts', {
    headers: { Origin: 'https://evil.example' },
    data: { team: 'payments', source: 'x', severity: 'info', message: 'forged' },
  })
  expect(response.status()).toBe(403)
  expect((await response.json()).error.code).toBe('csrf_failed')
})
