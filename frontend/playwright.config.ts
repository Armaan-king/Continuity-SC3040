import { execFileSync } from 'node:child_process'

import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end suite: the real FastAPI backend on its own Postgres database, the real Vite
 * client, and a seeded account. Nothing is mocked except where a test says so (the
 * analytics failure state intercepts one response).
 *
 *   npm run test:e2e
 *
 * Needs a local Postgres and `backend/.venv` (see backend/README). Ports and database are
 * separate from the dev servers so a running `npm run dev` is never touched.
 */
export const E2E = {
  apiPort: Number(process.env.E2E_API_PORT ?? 8020),
  webPort: Number(process.env.E2E_WEB_PORT ?? 5175),
  database: process.env.E2E_DATABASE_URL ?? 'postgresql:///continuity_e2e',
  python: process.env.E2E_PYTHON ?? '.venv/bin/python',
  email: 'e2e@continuity.dev',
  /** A throwaway credential for a local-only account in a disposable database. */
  password: 'e2e-local-only-pass',
}

// The backend applies its schema at startup, which Playwright runs before global setup,
// so the database has to exist by the time this file is read. Idempotent.
try {
  execFileSync('createdb', [new URL(E2E.database).pathname.slice(1)], { stdio: 'ignore' })
} catch {
  // Already exists.
}

const api = `http://localhost:${E2E.apiPort}`
const web = `http://localhost:${E2E.webPort}`

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  globalSetup: './e2e/global-setup.ts',
  use: {
    baseURL: web,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: `${E2E.python} -m uvicorn continuity.api.app:app --port ${E2E.apiPort}`,
      cwd: '../backend',
      url: `${api}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        DATABASE_URL: E2E.database,
        CONTINUITY_ORIGINS: web,
      },
    },
    {
      command: `npx vite --port ${E2E.webPort} --strictPort`,
      url: web,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { VITE_API_URL: api },
    },
  ],
})

export { api as API_URL }
