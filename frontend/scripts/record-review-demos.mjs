/** Record the real revision UI against a local disposable media database.
 * Preparation: backend/tools/seed_review_demo.py. No HTTP responses are mocked.
 */
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const root = fileURLToPath(new URL('../../', import.meta.url))
const baseURL = process.env.DEMO_URL ?? 'http://127.0.0.1:58002'
assert(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(baseURL).hostname), 'Use a local recording server')
const work = path.join(root, '.local/review-recording')
const output = path.join(root, 'docs/media')
await mkdir(output, { recursive: true })
const { credentials, ...session } = JSON.parse(await readFile(path.join(work, 'session.json'), 'utf8'))
const report = { ...session, recordedAt: new Date().toISOString(), viewport: { width: 1440, height: 900 },
  source: 'Real local production frontend and PostgreSQL; reference design; saved Exa retrieval reused without additional research calls', clips: [] }
const browser = await chromium.launch()
let context
const reviewURL = `/projects/${session.projectId}/review`

async function settle(page, milliseconds = 2200) {
  await page.evaluate(() => document.fonts.ready)
  await page.waitForTimeout(milliseconds)
}

async function top(page) {
  await page.getByRole('link', { name: 'Back to workspace' }).scrollIntoViewIfNeeded()
  await settle(page, 800)
}

async function activeRevision(page) {
  const response = await page.request.get(`/api/projects/${session.projectId}/revisions`)
  assert(response.ok())
  const id = (await response.json()).revisions.find(item => item.active).id
  const result = await page.request.get(`/api/revisions/${id}`)
  assert(result.ok())
  return result.json()
}

async function revisedWorkspace(page, mark) {
  await expect(page).toHaveURL(`${baseURL}/design/${session.projectId}`)
  await expect(page.getByTestId('revision-notice')).toContainText('Revision saved and revalidated')
  mark('Return to the planning workspace with the revised board')
  await settle(page, 3000)
  await page.getByRole('link', { name: 'Review design', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Validation counts' })).toBeVisible()
}

async function capture(name, run) {
  const chapters = []
  const errors = []
  context = await browser.newContext({ baseURL, viewport: report.viewport,
    recordVideo: { dir: work, size: report.viewport } })
  const auth = await context.request.post('/api/auth/login', { data: credentials })
  assert(auth.ok(), 'Media account login failed')
  const page = await context.newPage()
  page.setDefaultTimeout(30_000)
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (message.type() === 'error' && !/401 \(Unauthorized\)/.test(message.text())) errors.push(message.text())
  })
  const started = Date.now()
  const mark = title => {
    chapters.push({ seconds: Math.round((Date.now() - started) / 1000), title })
    console.log(`${name}: ${title}`)
  }
  try {
    const ready = await (await page.request.get('/api/ready')).json()
    assert.equal(ready.persistence, 'postgres')
    await run(page, mark)
    assert.deepEqual(errors, [])
    const video = page.video()
    await context.close()
    context = undefined
    await video.saveAs(path.join(work, `${name}.webm`))
    await video.delete()
    report.clips.push({ name, result: 'PASS', trimStart: chapters[0].seconds, chapters, browserErrors: errors })
    await writeFile(path.join(work, 'capture.json'), JSON.stringify(report, null, 2) + '\n')
  } catch (error) {
    await page.screenshot({ path: path.join(work, `${name}-failure.png`), fullPage: true }).catch(() => {})
    throw error
  }
}

try {
  await capture('design-review', async (page, mark) => {
    await page.goto(`/design/${session.projectId}`)
    await expect(page.getByTestId('workspace-project-name')).toHaveText('Sensor Review - Reference Board')
    await settle(page)
    mark('Reference board in the existing workspace')
    await settle(page, 3000)
    await page.getByRole('link', { name: 'Review design', exact: true }).click()
    await expect(page.getByRole('region', { name: 'Validation counts' })).toBeVisible()
    await settle(page)
    mark('Validation report: passes, failures and unknowns')
    await page.screenshot({ path: path.join(output, 'design-review-poster.png') })
    await settle(page, 3500)
    const question = page.getByRole('form', { name: 'What operating temperature range must the device tolerate?' })
    await question.scrollIntoViewIfNeeded()
    mark('Answer a review question and save a new revision')
    await question.getByRole('button', { name: 'Confirm & revalidate' }).click()
    await revisedWorkspace(page, mark)
    await expect(question).toHaveCount(0)
    await top(page)
    await page.getByRole('button', { name: 'requirements', exact: true }).click()
    await settle(page)
    mark('Edit a requirement and revalidate')
    await page.getByRole('spinbutton', { name: 'input voltage', exact: true }).fill('5')
    await settle(page, 1800)
    await page.getByRole('button', { name: 'Save and revalidate', exact: true }).click()
    await revisedWorkspace(page, mark)
    await expect(page.getByLabel('Viewing revision')).toContainText('Requirements updated and revalidated')
    await top(page)
    await page.getByRole('button', { name: 'report', exact: true }).click()
    const sensorFinding = page.locator('article').filter({ hasText: 'voltage overlap' }).filter({ hasText: 'SHT40' })
    await expect(sensorFinding).toContainText('FAIL')
    await sensorFinding.scrollIntoViewIfNeeded()
    mark('The 5 V supply creates a new voltage conflict')
    await settle(page, 4500)
    const changed = await activeRevision(page)
    assert.equal(changed.document.board.requirements.input_voltage, 5)
    await top(page)
    await page.getByRole('button', { name: 'Revision history', exact: true }).click()
    await page.getByLabel('Compare against').selectOption(session.initialRevision)
    await expect(page.getByText('input voltage: 3.3 → 5')).toBeVisible()
    mark('Compare revisions without losing the original design')
    await settle(page, 4500)
    await page.getByLabel('Viewing revision').selectOption(session.initialRevision)
    await expect(page.getByRole('button', { name: 'Restore this revision', exact: true })).toBeEnabled()
    await settle(page, 1800)
    await page.getByRole('button', { name: 'Restore this revision', exact: true }).click()
    await revisedWorkspace(page, mark)
    await expect(page.getByLabel('Viewing revision')).toContainText('Earlier revision restored and revalidated')
    mark('Restore creates a new revalidated revision')
    await settle(page, 3500)
    const restored = await activeRevision(page)
    assert.notEqual(restored.id, session.initialRevision)
    assert.equal(restored.document.board.requirements.input_voltage, 3.3)
    assert.equal(restored.parent_id, changed.id)
    await page.getByRole('button', { name: 'report', exact: true }).click()
    await page.getByRole('button', { name: 'All checks', exact: true }).click()
    await expect(page.locator('article').filter({ hasText: 'voltage overlap' }).filter({ hasText: 'SHT40' })).toContainText('PASS')
    await settle(page)
    mark('Export the same revision as a report and BOM')
    for (const [label, file] of [['Export report', 'report.json'], ['Export BOM', 'bom.csv']]) {
      const pending = page.waitForEvent('download')
      await page.getByRole('link', { name: label, exact: true }).click()
      await (await pending).saveAs(path.join(work, file))
      await settle(page, 1300)
    }
    assert.equal(JSON.parse(await readFile(path.join(work, 'report.json'), 'utf8')).revision_id, restored.id)
    assert((await readFile(path.join(work, 'bom.csv'), 'utf8')).includes(restored.id))
    await page.getByRole('link', { name: 'Back to workspace', exact: false }).click()
    await expect(page.getByTestId('workspace-project-name')).toBeVisible()
    await page.reload()
    await expect(page.getByTestId('workspace-project-name')).toBeVisible()
    const board = await (await page.request.get(`/api/threads/${session.threadId}/board`)).json()
    assert.equal(board.revision_id, restored.id)
    mark('Reopen the workspace with the restored revision')
    await settle(page, 3500)
    report.revisionVerification = { changedRevision: changed.id, restoredRevision: restored.id,
      exportedRevision: restored.id, restoredVoltage: 3.3, historyPreserved: true }
  })

  await capture('research-connections', async (page, mark) => {
    await page.goto(reviewURL)
    await expect(page.getByRole('region', { name: 'Validation counts' })).toBeVisible()
    await settle(page)
    mark('Review the saved reference design')
    await settle(page, 2500)
    const card = page.locator('#evidence-sensor')
    await card.getByText('Inspect facts & documents', { exact: true }).click()
    const detail = card.locator('details details').first()
    await detail.locator('summary').scrollIntoViewIfNeeded()
    await detail.locator('summary').click()
    await expect(detail).toContainText('requires verification')
    await detail.evaluate(element => element.scrollIntoView({ block: 'start', behavior: 'smooth' }))
    mark('Exa sources remain visibly unverified')
    await settle(page, 3000)
    await detail.locator('a').scrollIntoViewIfNeeded()
    assert(session.research.sources.includes(await detail.locator('a').getAttribute('href')))
    await settle(page, 3500)
    await page.screenshot({ path: path.join(output, 'research-connections-poster.png') })
    await detail.locator('pre').evaluate(element => element.scrollTo({ top: 220, behavior: 'smooth' }))
    await settle(page, 3000)
    await page.getByRole('heading', { name: 'Validation scope', exact: true }).scrollIntoViewIfNeeded()
    mark('Review the limits of what the engine checked')
    await settle(page, 3500)
    await top(page)
    await page.getByRole('button', { name: 'requirements', exact: true }).click()
    await page.getByRole('heading', { name: 'Connections', exact: true }).scrollIntoViewIfNeeded()
    mark('Declare the signal connection against your circuit')
    await page.getByRole('button', { name: 'Add signal connection', exact: true }).click()
    await page.getByLabel('Connection 1 name', { exact: true }).fill('sensor-i2c')
    await page.getByLabel('Connection 1 protocol', { exact: true }).selectOption('I2C')
    await page.getByLabel('Connection 1 controller', { exact: true }).selectOption('controller')
    await page.getByLabel('Humidity Sensor', { exact: true }).check()
    await page.getByRole('checkbox', { name: 'The displayed power connections match my circuit.' }).check()
    await page.getByRole('button', { name: 'Save connections and revalidate', exact: true }).scrollIntoViewIfNeeded()
    await settle(page, 3500)
    await page.getByRole('button', { name: 'Save connections and revalidate', exact: true }).click()
    await revisedWorkspace(page, mark)
    await expect(page.getByLabel('Viewing revision')).toContainText('Connections updated and revalidated')
    await top(page)
    await page.getByRole('button', { name: 'report', exact: true }).click()
    await page.getByRole('button', { name: 'All checks', exact: true }).click()
    const sensorChecks = page.getByRole('heading', { name: 'Humidity Sensor', exact: true }).locator('..').locator('..')
    const interfaceFinding = sensorChecks.locator('article').filter({ hasText: 'interface role match' }).filter({ hasText: 'sensor-i2c' })
    await expect(interfaceFinding).toContainText('physical pins and timing still require review')
    await interfaceFinding.scrollIntoViewIfNeeded()
    mark('Validate the declared bus; physical pins still require review')
    await settle(page, 4500)
    const final = await activeRevision(page)
    assert.equal(final.document.board.signal_nets[0].id, 'sensor-i2c')
    assert.equal(final.document.board.signal_nets[0].basis, 'confirmed')
    assert.equal(final.document.board.slots.sensor.part.research[0].verification, 'unverified')
    report.connectionsVerification = { revision: final.id, confirmedBus: 'sensor-i2c',
      reportCounts: final.document.report.counts, researchStillUnverified: true }
  })
  console.log('PASS: both feature recordings; immutable revisions, restore, exports, evidence and declared connections verified')
} finally {
  await context?.close()
  await browser.close()
}
