import { defineConfig, devices } from '@playwright/test'

// Runs against the production-shaped stack (`make prod-up`, then `make e2e`):
// the real nginx, the real built bundle, the real api - the path users get.
// @playwright/test is pinned to the exact version of the Docker image that
// runs it (mcr.microsoft.com/playwright:v1.63.0-noble): the browsers inside
// the image only match that version.
export default defineConfig({
  testDir: './specs',
  timeout: 30_000,
  fullyParallel: false,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8080',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
