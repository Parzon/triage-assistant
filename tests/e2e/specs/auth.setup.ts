import { expect, test as setup } from '@playwright/test'
import { PASSWORD } from '../users'

// Signs the demo users in once, through the identity provider's real login
// page, and saves their cookies: every other test starts signed in
// (storageState in playwright.config.ts) instead of logging in again.

for (const user of ['alice', 'bob']) {
  setup(`sign in as ${user}`, async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: 'Sign in' }).click()
    await expect(page).toHaveURL(/\/auth\/realms\/triage\//) // Keycloak's page, via the edge
    await page.locator('#username').fill(user)
    await page.locator('#password').fill(PASSWORD)
    await page.locator('#kc-login').click()
    await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
    await page.context().storageState({ path: `.auth/${user}.json` })
  })
}
