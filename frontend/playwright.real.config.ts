import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e-real',
  fullyParallel: false,
  workers: 1,
  outputDir: './test-results/real-backend',
  retries: 0,
  timeout: 90000,
  use: { baseURL: 'http://127.0.0.1:18071', trace: 'retain-on-failure' },
  projects: [{ name: 'real-backend', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `${process.env.THEMIS_TEST_PYTHON ?? 'python'} ../scripts/run_browser_backend.py`,
    url: 'http://127.0.0.1:18071/api/v1/health',
    reuseExistingServer: false,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 10000 },
    timeout: 120000,
  },
});
