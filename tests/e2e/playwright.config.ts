import { defineConfig, devices } from '@playwright/test'

// Runs against the production-shaped stack (`make prod-up`, then `make e2e`):
// the real edge, nginx, built bundle, api and identity provider - the path
// users get. Tests run as alice unless they say otherwise (auth.setup.ts).
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
    // Only for the edge's local CA (make e2e sets it); a real certificate
    // needs no exception.
    ignoreHTTPSErrors: process.env.E2E_IGNORE_HTTPS_ERRORS === '1',
  },
  projects: [
    // Signs alice and bob in through the identity provider once (auth.setup.ts).
    { name: 'setup', testMatch: /.*\.setup\.ts/ },
    {
      name: 'chromium',
      testIgnore: /.*\.setup\.ts/,
      use: { ...devices['Desktop Chrome'], storageState: '.auth/alice.json' },
      dependencies: ['setup'],
    },
  ],
})
