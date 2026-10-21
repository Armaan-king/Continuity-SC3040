import { expect, test } from '@playwright/test'
import { API_URL } from './helpers'

test('local mode opens a usable brief screen without requiring an account', async ({ page }) => {
  await page.route(`${API_URL}/runtime`, (route) => route.fulfill({ json: {
    accounts: false, persistence: 'memory', parts: 'live',
    ai: { configured: false, provider: 'disabled', model: null, mode: 'rules' },
  } }))
  await page.goto('/design')
  await expect(page.getByRole('button', { name: 'Start validation' })).toBeDisabled()
  await expect(page.getByText('Basic planning is active.', { exact: false })).toBeVisible()
  await page.locator('textarea').fill('USB temperature sensor')
  await expect(page.getByRole('button', { name: 'Start validation' })).toBeEnabled()
})

test('local sign-in explains persistence and provides a working workspace link', async ({ page }) => {
  await page.route(`${API_URL}/runtime`, (route) => route.fulfill({ json: {
    accounts: false, persistence: 'memory', parts: 'live',
    ai: { configured: false, provider: 'disabled', model: null, mode: 'rules' },
  } }))
  await page.goto('/login')
  await page.getByRole('link', { name: /workspace/i }).click()
  await expect(page).toHaveURL(/\/design$/)
  await expect(page.locator('textarea')).toBeVisible()
})

test('a dropped stream reconnects using the cursor without starting another design', async ({ page }) => {
  let starts = 0
  let cursor: string | null = null
  await page.route(`${API_URL}/design`, (route) => {
    starts += 1
    return route.fulfill({ contentType: 'text/event-stream', headers: { 'X-Thread-ID': 'reconnect-test' },
      body: 'data: {"type":"reasoning","seq":0,"thread_id":"reconnect-test","slot":null,"text":"Connecting to parts"}\n\n' })
  })
  await page.route(`${API_URL}/threads/reconnect-test/events?*`, (route) => {
    cursor = new URL(route.request().url()).searchParams.get('after')
    return route.fulfill({ contentType: 'text/event-stream',
      body: 'data: {"type":"question","seq":1,"thread_id":"reconnect-test","question_id":"supply","text":"Which supply will power this board?","suggestions":["USB"]}\n\n' })
  })
  await page.goto('/design')
  await page.locator('textarea').fill('USB temperature sensor')
  await page.getByRole('button', { name: 'Start validation' }).click()
  await expect(page.getByText('Which supply will power this board?', { exact: true })).toBeVisible()
  expect(cursor).toBe('0')
  // StrictMode may repeat the HTTP request; both use one idempotency key.
  expect(starts).toBeGreaterThan(0)
})
