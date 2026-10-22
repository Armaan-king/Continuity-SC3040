import { expect, test, type Page } from '@playwright/test'
import { API_URL, registerFreshAccount } from './helpers'

function gate() {
  let release!: () => void
  const promise = new Promise<void>((resolve) => { release = resolve })
  return { promise, release }
}

async function start(page: Page) {
  await page.goto('/design')
  await page.locator('textarea').fill('Plant light monitor')
  await page.getByRole('button', { name: 'Start validation' }).click()
}

const question = (id: string) => ({ contentType: 'text/event-stream',
  headers: { 'X-Thread-ID': id, 'Access-Control-Expose-Headers': 'X-Thread-ID' },
  body: `data: ${JSON.stringify({ type: 'question', seq: 0, thread_id: id,
    question_id: 'supply', text: 'Which supply?', suggestions: ['USB'] })}\n\n`,
})

test('Stop waits for server acknowledgement and then a new run can start', async ({ page }) => {
  const acknowledgement = gate()
  let stopped = false
  const designs = new Map<string, string>()
  await page.route(`${API_URL}/design`, (route) => {
    const key = route.request().postDataJSON().request_id as string
    const existing = designs.get(key)
    if (existing) return route.fulfill(question(existing))
    if (designs.size > 0 && !stopped) return route.fulfill({ status: 409,
      json: { detail: 'A design is already running. Reopen it or stop it first.' } })
    const id = `run-${designs.size + 1}`
    designs.set(key, id)
    return route.fulfill(question(id))
  })
  await page.route(`${API_URL}/threads/run-1/cancel`, async (route) => {
    await acknowledgement.promise
    stopped = true
    await route.fulfill({ status: 204 })
  })
  await start(page)
  await expect(page.getByText('Which supply?', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Stopping…', exact: true })).toBeDisabled()
  await expect(page.getByText('Stopped. You can start a new design.', { exact: true })).toHaveCount(0)
  acknowledgement.release()
  await expect(page.getByText('Stopped. You can start a new design.', { exact: true })).toBeVisible()
  await start(page)
  await expect(page.getByText('Which supply?', { exact: true })).toBeVisible()
  expect(designs.size).toBe(2)
})

test('a rejected stop stays visible and retry cancels the same run', async ({ page }) => {
  let attempts = 0
  await page.route(`${API_URL}/design`, (route) => route.fulfill(question('retry-stop')))
  await page.route(`${API_URL}/threads/retry-stop/cancel`, (route) => {
    attempts += 1
    return attempts === 1 ? route.fulfill({ status: 503, json: { detail: 'Temporary cancellation failure.' } })
      : route.fulfill({ status: 204 })
  })
  await start(page)
  await expect(page.getByText('Which supply?', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(page.getByText('Stop not confirmed. The run may still be active.', { exact: true })).toBeVisible()
  await expect(page.getByText(/Temporary cancellation failure\./)).toBeVisible()
  await page.getByRole('button', { name: 'Retry Stop', exact: true }).click()
  await expect(page.getByText('Stopped. You can start a new design.', { exact: true })).toBeVisible()
  expect(attempts).toBe(2)
})

test('an immediate Stop waits for the creation response to identify the run', async ({ page }) => {
  const creation = gate()
  let cancellations = 0
  await page.route(`${API_URL}/design`, async (route) => {
    await creation.promise
    await route.fulfill(question('early-stop'))
  })
  await page.route(`${API_URL}/threads/early-stop/cancel`, (route) => {
    cancellations += 1
    return route.fulfill({ status: 204 })
  })
  await start(page)
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Stopping…', exact: true })).toBeDisabled()
  expect(cancellations).toBe(0)
  creation.release()
  await expect(page.getByText('Stopped. You can start a new design.', { exact: true })).toBeVisible()
  expect(cancellations).toBe(1)
})

test('real cancellation persists and permits another project to start', async ({ page }) => {
  await registerFreshAccount(page)
  const first = await (await page.request.post(`${API_URL}/projects`, { data: { name: 'Stopped board' } })).json()
  await page.goto(`/design/${first.id}`)
  await page.locator('textarea').fill('Solar powered weather station with a sensor')
  await page.getByRole('button', { name: 'Start validation' }).click()
  await expect(page.getByText('Action Required', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(page.getByText('Stopped. You can start a new design.', { exact: true })).toBeVisible()
  const threads = await (await page.request.get(`${API_URL}/projects/${first.id}/threads`)).json()
  expect(threads).toHaveLength(1)
  expect(threads[0].status).toBe('abandoned')

  const next = await (await page.request.post(`${API_URL}/projects`, { data: { name: 'Next board' } })).json()
  await page.goto(`/design/${next.id}`)
  await page.locator('textarea').fill('Solar powered weather station with a sensor')
  await page.getByRole('button', { name: 'Start validation' }).click()
  await expect(page.getByText('Action Required', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(page.getByText('Stopped. You can start a new design.', { exact: true })).toBeVisible()
})
