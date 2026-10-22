import { expect, test } from '@playwright/test'
import { API_URL, registerFreshAccount, watchConsole } from './helpers'

test('a conversational brief gets a stable name across workspace, projects and reload', async ({ page }) => {
  const errors = watchConsole(page)
  await registerFreshAccount(page)
  const project = await (await page.request.post(`${API_URL}/projects`, { data: { name: 'Untitled board' } })).json()
  await page.goto(`/design/${project.id}`)
  const brief = 'I want to make a small weather station with a sensor, solar powered'
  await page.locator('textarea').fill(brief)
  await page.getByRole('button', { name: 'Start validation' }).click()
  await expect(page.getByTestId('workspace-project-name')).toHaveText('Weather Station')
  await expect(page.getByText('Action Required', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByTestId('workspace-project-name')).toHaveText('Weather Station')
  await expect(page.getByText('Action Required', { exact: true })).toBeVisible()
  const threads = await (await page.request.get(`${API_URL}/projects/${project.id}/threads`)).json()
  expect(threads).toHaveLength(1)
  expect(threads[0].prompt).toBe(brief)
  await page.goto('/projects')
  await expect(page.getByText('Weather Station', { exact: true })).toBeVisible()
  expect(errors).toEqual([])
})

test('starting a board preserves a name chosen by its owner', async ({ page }) => {
  await registerFreshAccount(page)
  const project = await (await page.request.post(`${API_URL}/projects`, { data: { name: 'Coursework Prototype' } })).json()
  await page.goto(`/design/${project.id}`)
  await page.locator('textarea').fill('I want to build a weather station with a sensor, solar powered')
  await page.getByRole('button', { name: 'Start validation' }).click()
  await expect(page.getByText('Action Required', { exact: true })).toBeVisible()
  await expect(page.getByTestId('workspace-project-name')).toHaveText('Coursework Prototype')
  expect((await (await page.request.get(`${API_URL}/projects/${project.id}`)).json()).name).toBe('Coursework Prototype')
})

test('a failed clarification submission keeps the question available for retry', async ({ page }) => {
  await page.route(`${API_URL}/design`, (route) => route.fulfill({ contentType: 'text/event-stream', body:
    'data: {"type":"question","seq":0,"thread_id":"retry","question_id":"supply","text":"Which supply?","suggestions":["USB"]}\n\n',
  }))
  let attempts = 0
  await page.route(`${API_URL}/resume`, (route) => {
    attempts += 1
    return attempts <= 2
      ? route.fulfill({ status: 503, json: { detail: 'Please retry your answer.' } })
      : route.fulfill({ contentType: 'text/event-stream', body:
        'data: {"type":"done","seq":1,"thread_id":"retry","summary":{"slots":0,"placed":0,"conflicts_resolved":0,"elapsed_s":0}}\n\n',
      })
  })
  await page.goto('/design')
  await page.locator('textarea').fill('Weather station')
  await page.getByRole('button', { name: 'Start validation' }).click()
  await page.getByRole('button', { name: 'USB', exact: true }).click()
  await expect(page.getByText('Please retry your answer.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'USB', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'USB', exact: true }).click()
  await expect(page.getByText('Please retry your answer.', { exact: true })).toHaveCount(2)
  await page.getByRole('button', { name: 'USB', exact: true }).click()
  await expect(page.getByText('Complete — 0/0 placed, 0 resolved.')).toBeVisible()
  expect(attempts).toBe(3)
})

test('final board preserves unresolved conflicts even after an earlier repair', async ({ page }) => {
  const part = { mpn: 'TEST-SENSOR', manufacturer: 'Test', description: 'Temperature sensor', category: 'sensor',
    interfaces: [], unit_price: 1, currency: 'USD', stock: 100, distributor: 'Test', lifecycle: 'active' }
  const slot = { id: 'sensor', label: 'Temperature Sensor', tier: 'peripherals', pinned: true,
    status: 'conflict', part, constraint: null, repair_count: 1 }
  const conflict = { rule: 'temperature_rating', subject: 'sensor', involved: ['sensor'], edge: null,
    message: 'The selected sensor does not meet the required temperature range.', evidence: [] }
  const frames = [
    { type: 'plan', slots: [slot], edges: [] },
    { type: 'conflict', ...conflict },
    { type: 'repair', slot: 'sensor', action: 'swap', rationale: 'Try another sensor', constraint: {}, alternatives: [] },
    { type: 'selection', slot: 'sensor', part, status: 'pass' },
    { type: 'board', slots: [slot], edges: [], supply: null, conflicts: [conflict] },
    { type: 'done', summary: { slots: 1, placed: 1, conflicts_resolved: 1, elapsed_s: 1,
      failures: 1, warnings: 0, unchecked: 2, stopped: false } },
  ]
  await page.route(`${API_URL}/design`, (route) => route.fulfill({ contentType: 'text/event-stream',
    body: frames.map((event, seq) => `data: ${JSON.stringify({ ...event, seq, thread_id: 'final-state' })}\n\n`).join(''),
  }))
  await page.goto('/design')
  await page.locator('textarea').fill('Temperature sensor')
  await page.getByRole('button', { name: 'Start validation' }).click()
  await expect(page.getByRole('button', { name: 'Resolve Conflicts (1)' })).toBeEnabled()
  await expect(page.getByText(/Review: 1 failed, 2 unchecked/)).toBeVisible()
  await expect(page.locator('[data-tour="graph"]')).toContainText('TEST-SENSOR')
  await expect(page.getByRole('button', { name: 'Cancel Run', exact: true })).toHaveCount(0)
  await page.screenshot({ path: 'test-results/consistency-final-board.png', fullPage: true, animations: 'disabled' })
})

test('replacing a part updates a restored BOM immediately and retains its quantity', async ({ page }) => {
  const oldPart = { mpn: 'OLD-PART', manufacturer: 'Test', description: 'Sensor', category: 'sensor',
    interfaces: [], unit_price: 1, currency: 'USD', stock: 100, distributor: 'Test', lifecycle: 'active' }
  const newPart = { ...oldPart, mpn: 'NEW-PART', unit_price: 2 }
  const slot = { id: 'sensor', label: 'Sensor', tier: 'peripherals', pinned: true,
    status: 'pass', part: oldPart, constraint: null, repair_count: 0 }
  const frames = [
    { type: 'plan', slots: [slot], edges: [] },
    { type: 'bom', rows: [{ ...oldPart, slot: 'sensor', qty: 3 }], total: 3, currency: 'USD' },
    { type: 'selection', slot: 'sensor', part: newPart, status: 'pass' },
    { type: 'question', question_id: 'review', text: 'Continue with this replacement?', suggestions: [] },
  ]
  await page.route(`${API_URL}/design`, (route) => route.fulfill({ contentType: 'text/event-stream',
    body: frames.map((event, seq) => `data: ${JSON.stringify({ ...event, seq, thread_id: 'replacement' })}\n\n`).join(''),
  }))
  await page.goto('/design')
  await page.locator('textarea').fill('Sensor board')
  await page.getByRole('button', { name: 'Start validation' }).click()
  const table = page.locator('[data-tour="bom-export"]')
  await expect(table.getByText('NEW-PART', { exact: true })).toBeVisible()
  await expect(table.getByText('OLD-PART', { exact: true })).toHaveCount(0)
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export BOM' }).click()
  const csv = Buffer.concat(await (await (await download).createReadStream()).toArray()).toString()
  expect(csv).toContain('"NEW-PART","Sensor","3","2"')
  expect(csv).not.toContain('OLD-PART')
})
