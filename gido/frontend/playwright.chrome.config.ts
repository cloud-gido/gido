/**
 * Local-only: use system Google Chrome when Playwright browser download fails.
 * Do not commit reliance in CI — CI should use bundled chromium.
 */
import { defineConfig, devices } from '@playwright/test'
import base from './playwright.config'

export default defineConfig({
  ...base,
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome',
      },
    },
  ],
})
