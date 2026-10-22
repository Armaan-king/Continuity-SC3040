import { expect, test } from '@playwright/test'
import { API_URL, watchConsole } from './helpers'

test('source comparisons stay selectable, swaps replace the graph and a failed choice can be changed again', async ({ page }) => {
  const errors = watchConsole(page)
  let seq = 0
  const frames = (events: object[]) => ({ contentType: 'text/event-stream', body:
    events.map(event => `data: ${JSON.stringify({ ...event, seq: seq++, thread_id: 'recover-ui' })}\n\n`).join('') })
  const part = (mpn: string) => ({ mpn, manufacturer: 'Test', description: 'I2C sensor', category: 'sensor',
    interfaces: ['I2C'], unit_price: null, currency: 'USD', stock: null, distributor: 'External source', lifecycle: 'unknown' })
  const slot = (mpn: string) => ({ id: 'sensor', label: 'Light Sensor', tier: 'peripherals', pinned: true,
    status: 'pending', part: part(mpn), constraint: null, repair_count: 4 })
  const conflict = { type: 'conflict', rule: 'voltage_overlap', subject: 'sensor', involved: ['sensor'], edge: null,
    message: 'The selected part cannot accept 3.3 V.', evidence: [] }
  const question = (text: string, alternatives: object[] = []) => ({ type: 'question', question_id: 'escalation', text,
    suggestions: ['Research alternatives with Exa', 'Choose a different component', 'Redesign the device'], alternatives })
  const answers: string[] = []
  await page.route(`${API_URL}/design`, route => route.fulfill(frames([
    { type: 'plan', slots: [slot('OLD-SENSOR')], edges: [] },
    { type: 'selection', slot: 'sensor', part: part('OLD-SENSOR'), status: 'conflict' },
    conflict, question('Choose how to repair this board.'),
  ])))
  await page.route(`${API_URL}/resume`, route => {
    const answer = route.request().postDataJSON().answer
    answers.push(answer)
    if (answer === 'Research alternatives with Exa') return route.fulfill(frames([
      question('Review source passages before choosing a trial.', [
        { action: 'Try alternative 1: SENSOR-A', label: 'SENSOR-A · Manufacturer A', summary: 'Manufacturer passage for sensor A.',
          verification: 'Web candidate; electrical compatibility unverified', source: 'https://www.st.com/sensor-a' },
        { action: 'Try alternative 2: SENSOR-B', label: 'SENSOR-B · Manufacturer B', summary: 'Manufacturer passage for sensor B.',
          verification: 'Web candidate; electrical compatibility unverified', source: 'https://www.ti.com/sensor-b' },
      ]),
    ]))
    if (answer === 'Try alternative 1: SENSOR-A') return route.fulfill(frames([
      { type: 'plan', replace: true, slots: [slot('SENSOR-A')], edges: [] }, conflict,
      question('Your chosen component was applied and rechecked. Choose another candidate or redesign.'),
    ]))
    expect(answer).toBe('Try alternative 2: SENSOR-B')
    return route.fulfill(frames([
      { type: 'plan', replace: true, slots: [slot('SENSOR-B')], edges: [] },
      { type: 'board', slots: [{ ...slot('SENSOR-B'), status: 'pass' }], edges: [], supply: null, conflicts: [] },
      { type: 'bom', rows: [{ ...part('SENSOR-B'), slot: 'sensor', qty: 1 }], total: 0, currency: 'USD' },
      { type: 'done', summary: { slots: 1, placed: 1, conflicts_resolved: 1, elapsed_s: 1, failures: 0, unchecked: 2 } },
    ]))
  })
  await page.goto('/design')
  await page.locator('textarea').fill('Plant light monitor')
  await page.getByRole('button', { name: 'Start validation' }).click()
  await page.getByRole('button', { name: 'Research alternatives with Exa', exact: true }).click()
  await expect(page.getByRole('link', { name: 'View source' })).toHaveCount(2)
  await expect(page.locator('[data-tour="graph"]')).toContainText('OLD-SENSOR')
  await page.screenshot({ path: 'test-results/recovery-alternatives.png', fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: 'Try alternative 1: SENSOR-A', exact: true }).click()
  await expect(page.locator('[data-tour="graph"]')).toContainText('SENSOR-A')
  await expect(page.locator('[data-tour="graph"]')).not.toContainText('OLD-SENSOR')
  await expect(page.getByText(/Your chosen component was applied and rechecked/)).toBeVisible()
  await page.getByRole('button', { name: 'Research alternatives with Exa', exact: true }).click()
  await page.getByRole('button', { name: 'Try alternative 2: SENSOR-B', exact: true }).click()
  await expect(page.locator('[data-tour="graph"]')).toContainText('SENSOR-B')
  await expect(page.getByText('Conflict: Voltage Overlap', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/Review: 2 unchecked/)).toBeVisible()
  await expect(page.locator('[data-tour="bom-export"]')).toContainText('SENSOR-B')
  expect(answers).toEqual(['Research alternatives with Exa', 'Try alternative 1: SENSOR-A', 'Research alternatives with Exa', 'Try alternative 2: SENSOR-B'])
  expect(errors).toEqual([])
})

test('a redesign replaces obsolete nodes and accepts a written instruction', async ({ page }) => {
  let seq = 0
  const stream = (events: object[]) => ({ contentType: 'text/event-stream', body:
    events.map(event => `data: ${JSON.stringify({ ...event, seq: seq++, thread_id: 'redesign-ui' })}\n\n`).join('') })
  const slot = (id: string, label: string) => ({ id, label, tier: 'core', pinned: true, status: 'pending', part: null, constraint: null, repair_count: 0 })
  await page.route(`${API_URL}/design`, route => route.fulfill(stream([
    { type: 'plan', slots: [slot('obsolete', 'Obsolete module')], edges: [] },
    { type: 'question', question_id: 'escalation', text: 'The device needs a controller.', suggestions: ['Redesign the device'] },
  ])))
  await page.route(`${API_URL}/resume`, route => {
    expect(route.request().postDataJSON().answer).toBe('Replace the module with an I2C microcontroller')
    return route.fulfill(stream([
      { type: 'plan', replace: true, slots: [slot('mcu', 'New I2C Microcontroller')], edges: [] },
      { type: 'reasoning', slot: null, text: 'Reconfigured the board; all checks will run again.' },
      { type: 'question', question_id: 'escalation', text: 'Choose a controller candidate.', suggestions: ['Search for more components'] },
    ]))
  })
  await page.goto('/design')
  await page.locator('textarea').fill('Plant light monitor')
  await page.getByRole('button', { name: 'Start validation' }).click()
  await page.getByRole('textbox', { name: 'Your instruction' }).fill('Replace the module with an I2C microcontroller')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(page.locator('[data-tour="graph"]')).toContainText('NEW I2C MICROCONT')
  await expect(page.locator('[data-tour="graph"]')).not.toContainText('OBSOLETE MODULE')
  await expect(page.getByText('Choose a controller candidate.', { exact: true })).toBeVisible()
})
