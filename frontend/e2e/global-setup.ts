import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { E2E } from '../playwright.config'

/**
 * A known corpus before any test runs: seed the e2e account with `tools/seed_analytics.py`.
 * Re-seeding replaces only that account, so the suite is repeatable.
 */
export default function globalSetup() {
  const backend = path.resolve(import.meta.dirname, '../../backend')
  execFileSync(E2E.python, ['tools/seed_analytics.py', '--email', E2E.email], {
    cwd: backend,
    stdio: 'inherit',
    env: { ...process.env, DATABASE_URL: E2E.database, FUZE_SEED_PASSWORD: E2E.password },
  })
  execFileSync(E2E.python, ['tools/seed_review.py'], {
    cwd: backend, stdio: 'inherit', env: { ...process.env, DATABASE_URL: E2E.database, FUZE_SEED_PASSWORD: E2E.password },
  })
}
