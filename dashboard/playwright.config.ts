import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config — Synthetic Trader Dashboard E2E
 * Tests run against the live dev server at http://localhost:3000.
 * API expected at http://127.0.0.1:8001 (FastAPI).
 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
    locale: 'es-ES',
    timezoneId: 'Europe/Madrid',
  },
  projects: [
    // Desktop Chromium — primary
    {
      name: 'chromium-desktop',
      use: { ...devices['Desktop Chrome'] },
    },
    // Mobile viewport — responsive basics
    {
      name: 'mobile-chrome',
      use: { ...devices['Pixel 5'] },
      testMatch: /responsive\.spec\.ts/,
    },
  ],
});
