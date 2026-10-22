import { expect, test } from '@playwright/test'

import { API_URL, registerFreshAccount, signIn, signInThroughForm, watchConsole } from './helpers'

/**
 * Smoke coverage of the screens that existed before analytics, so adding it cannot quietly
 * break them: landing, sign-in and sign-out, projects, a saved board in the workspace, BOM
 * export (UC-13) and memory (UC-15).
 */

const PROJECTS = ['Weather station', 'Solar data logger', 'Motor controller', 'BLE wearable', 'USB sensor hub']

test('the landing page renders', async ({ page }) => {
  const errors = watchConsole(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1 })).toContainText('regulator overheats')
  expect(errors).toEqual([])
})

test('wrong credentials are refused without signing in', async ({ page }) => {
  await page.goto('/login')
  await page.getByPlaceholder('ENTER_EMAIL').fill('e2e@continuity.dev')
  await page.getByPlaceholder('ENTER_PASSWORD').fill('not-the-password')
  await page.getByRole('button', { name: 'SIGN_IN' }).click()
  await expect(page).toHaveURL(/\/login$/)
})

test('signing in lists every project, and signing out ends the session', async ({ page }) => {
  const errors = watchConsole(page)
  await signInThroughForm(page)

  for (const name of PROJECTS) {
    await expect(page.getByText(name, { exact: true })).toBeVisible()
  }

  await page.getByRole('button', { name: 'Settings' }).click()
  await page.getByRole('button', { name: 'SIGN_OUT' }).click()
  await expect(page).toHaveURL(/\/login$/)
  await page.goto('/projects')
  await expect(page).toHaveURL(/\/login$/)
  expect(errors).toEqual([])
})

test('a finished board opens with its trace and bill of materials, and exports as CSV', async ({ page }) => {
  const errors = watchConsole(page)
  await signIn(page)
  await page.goto('/projects')
  await page.getByText('USB sensor hub', { exact: true }).click()

  await expect(page).toHaveURL(/\/design\/[0-9a-f]+$/)
  await expect(page.getByRole('heading', { name: 'Validation Trace' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Bill of Materials' })).toBeVisible()
  await expect(page.getByText(/Complete — \d+\/\d+ placed/)).toBeVisible()

  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export BOM' }).click()
  const file = await download
  expect(file.suggestedFilename()).toBe('continuity-bom.csv')
  const csv = await (await file.createReadStream()).toArray()
  expect(Buffer.concat(csv).toString()).toMatch(/^part_number,description,qty,unit_price/)
  expect(errors).toEqual([])
})

test('the server-side CSV export still answers for a finished run', async ({ page }) => {
  await signIn(page)
  const projects = await (await page.request.get(`${API_URL}/projects`)).json()
  const hub = projects.find((project: { name: string }) => project.name === 'USB sensor hub')
  const threads = await (await page.request.get(`${API_URL}/projects/${hub.id}/threads`)).json()
  const done = threads.find((thread: { status: string }) => thread.status === 'done')

  const response = await page.request.get(`${API_URL}/export/${done.id}.csv`)

  expect(response.status()).toBe(200)
  expect(response.headers()['content-type']).toContain('text/csv')
})

test('memory shows the parts and boards from past runs', async ({ page }) => {
  const errors = watchConsole(page)
  await signIn(page)
  await page.goto('/memory')

  await expect(page.getByRole('button', { name: 'MEMORY', exact: true })).toBeVisible()
  await expect(page.getByText(/\d+ PARTS · \d+ BOARDS/)).toBeVisible()
  await expect(page.getByRole('main').locator('canvas')).toBeVisible()
  expect(errors).toEqual([])
})

test('the guided walkthrough still replays for a new account', async ({ page }) => {
  // Its recorded check frames predate the `unchecked` field, so this is also the
  // backward-compatibility check for frames that do not carry it.
  const errors = watchConsole(page)
  await registerFreshAccount(page)
  await page.goto('/walkthrough')

  await expect(page.getByRole('heading', { name: 'Validation Trace' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Bill of Materials' })).toBeVisible()
  expect(errors).toEqual([])
})
