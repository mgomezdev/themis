import { defineConfig, devices } from '@playwright/test';

const origin = 'http://127.0.0.1:18173';

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  use: { baseURL: origin, trace: 'on-first-retry' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 18173 --strictPort',
    url: origin,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
