import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end configuration (docs/TESTING.md §12, IMPLEMENTATION_PLAN.md M1.7).
 *
 * There is deliberately **no `webServer`**. The thing under test is the
 * Compose stack — backend, worker, beat, PostgreSQL, Redis, the SPA's dev
 * server and the mock identity provider — and a `webServer` block that
 * started Vite on its own would test the SPA against nothing, which is the
 * one configuration that cannot fail honestly. `global-setup.ts` checks the
 * stack is up and says how to start it if not.
 *
 * `workers: 1` because the suite shares one database, one Redis and one
 * identity provider. Parallel workers would race on the seeded inventory and
 * on the registered server each test creates.
 *
 * `retries: 0` on purpose. This project has already paid for a flaky suite
 * once (PROGRESS issue 51): a retry would have hidden it for another six
 * runs. A test that fails intermittently here is a defect to find.
 */

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:5173';

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],
  globalSetup: './e2e/global-setup.ts',
  outputDir: './e2e/.artifacts',
  use: {
    baseURL: BASE_URL,
    // Kept only for a failure: a trace per passing test is megabytes of CI
    // artefact nobody opens.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
