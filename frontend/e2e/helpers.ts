import { expect, type Page } from '@playwright/test'

import { API_URL, E2E } from '../playwright.config'

/** Console errors that are part of normal operation, not regressions. */
const EXPECTED_ERRORS = [
  // The app asks "who am I?" on every load; signed out, the answer is a 401.
  /401 \(Unauthorized\)/,
]

/** Collects unexpected console errors so a test can assert there were none. */
export function watchConsole(page: Page) {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    const text = message.text()
    if (!EXPECTED_ERRORS.some((pattern) => pattern.test(text))) errors.push(text)
  })
  page.on('pageerror', (error) => errors.push(error.message))
  return errors
}

/** Signs in through the real form, as an engineer would. */
export async function signInThroughForm(page: Page, email = E2E.email, password = E2E.password) {
  await page.goto('/login')
  await page.getByPlaceholder('ENTER_EMAIL').fill(email)
  await page.getByPlaceholder('ENTER_PASSWORD').fill(password)
  await page.getByRole('button', { name: 'SIGN_IN' }).click()
  await expect(page).toHaveURL(/\/projects$/)
}

/** Signs in over the API; the session cookie lands in the page's browser context. */
export async function signIn(page: Page, email = E2E.email, password = E2E.password) {
  const response = await page.request.post(`${API_URL}/auth/login`, { data: { email, password } })
  expect(response.ok()).toBeTruthy()
}

/** A brand-new account with no runs at all. */
export async function registerFreshAccount(page: Page) {
  const email = `fresh-${Date.now()}-${Math.floor(Math.random() * 1e6)}@continuity.dev`
  const response = await page.request.post(`${API_URL}/auth/register`, {
    data: { email, password: 'a-fresh-local-password' },
  })
  expect(response.ok()).toBeTruthy()
  return email
}

export { API_URL }
