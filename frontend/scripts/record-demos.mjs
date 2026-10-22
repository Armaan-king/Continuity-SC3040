/** Record the real local UI for the README. Use a disposable PostgreSQL database.
 * Run from the repository root: node frontend/scripts/record-demos.mjs
 * Requires installed Playwright Chromium and a combined server at DEMO_URL.
 * No requests are mocked. The walkthrough itself is the app's labelled replay.
 */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, expect } from '@playwright/test'

const root = fileURLToPath(new URL('../../', import.meta.url))
const baseURL = process.env.DEMO_URL ?? 'http://127.0.0.1:58002'
assert(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(baseURL).hostname),
  'Record against a local server backed by a disposable database.')
const work = path.join(root, '.local/readme-recording')
const output = path.join(root, 'docs/media')
await mkdir(work, { recursive: true })
await mkdir(output, { recursive: true })

const browser = await chromium.launch()
const credentials = { email: `readme-${Date.now()}@example.com`, password: randomUUID() }
const report = { recordedAt: new Date().toISOString(), viewport: { width: 1440, height: 900 },
  source: 'Local production build; PostgreSQL; basic planning; real distributor requests', clips: [] }
let context

async function recording(name, run) {
  const started = Date.now()
  const chapters = []
  const errors = []
  context = await browser.newContext({ baseURL, viewport: report.viewport,
    recordVideo: { dir: work, size: report.viewport } })
  const page = await context.newPage()
  page.setDefaultTimeout(30_000)
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error' && !/401 \(Unauthorized\)/.test(message.text())) errors.push(message.text())
  })
  const mark = (title) => {
    chapters.push({ seconds: Math.round((Date.now() - started) / 1000), title })
    console.log(`${name}: ${title}`)
  }
  try {
    await run(page, mark)
    assert.deepEqual(errors, [], 'Unexpected browser errors')
    const video = page.video()
    await context.close()
    context = undefined
    await video.saveAs(path.join(work, `${name}.webm`))
    await video.delete()
    report.clips.push({ name, trimStart: chapters[0].seconds, chapters, browserErrors: errors, result: 'PASS' })
    await writeFile(path.join(work, 'capture.json'), JSON.stringify(report, null, 2) + '\n')
  } catch (error) {
    await page.screenshot({ path: path.join(work, `${name}-failure.png`) }).catch(() => {})
    await writeFile(path.join(work, `${name}-failure.txt`), await page.locator('body').innerText()).catch(() => {})
    throw error
  }
}

try {
  await recording('guided-walkthrough', async (page, mark) => {
    const ready = await page.request.get('/api/ready')
    assert.deepEqual(await ready.json(), { status: 'ready', persistence: 'postgres' })
    const runtime = await (await page.request.get('/api/runtime')).json()
    assert.equal(runtime.ai.mode, 'rules', 'This recording documents basic planning without an AI key')
    await page.goto('/')
    await page.evaluate(() => document.fonts.ready)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    mark('Landing page')
    await page.waitForTimeout(1800)
    await page.screenshot({ path: path.join(output, 'landing.png') })
    await page.getByRole('link', { name: 'GET_STARTED', exact: true }).click()
    mark('Create an account')
    await page.getByPlaceholder('ENTER_EMAIL').fill(credentials.email)
    await page.getByPlaceholder('ENTER_PASSWORD').fill(credentials.password)
    await page.waitForTimeout(1000)
    await page.getByRole('button', { name: 'CREATE_ACCOUNT', exact: true }).click()
    await expect(page).toHaveURL(/\/walkthrough$/)
    mark('Recorded example: brief and board graph')
    await expect(page.getByRole('heading', { name: 'The board, as a graph' })).toBeVisible({ timeout: 40_000 })
    await page.waitForTimeout(3000)
    for (const heading of ['It shows its working', 'A conflict has a verdict',
      'The check log is still here', 'Parts, prices, and export', 'Memory keeps the finding']) {
      await page.getByRole('button', { name: 'NEXT', exact: true }).click()
      await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
      mark(heading)
      await page.waitForTimeout(4000)
      if (heading === 'Parts, prices, and export') {
        await page.screenshot({ path: path.join(output, 'walkthrough-poster.png') })
      }
    }
    await page.getByRole('button', { name: 'START_BUILDING', exact: true }).click()
    await expect(page).toHaveURL(/\/projects$/)
    await page.waitForTimeout(2000)
    console.log('PASS: registration, all seven walkthrough steps, saved onboarding project')
  })

  await recording('project-workflow', async (page, mark) => {
    await page.goto('/login')
    await page.evaluate(() => document.fonts.ready)
    mark('Sign in to saved projects')
    await page.getByPlaceholder('ENTER_EMAIL').fill(credentials.email)
    await page.getByPlaceholder('ENTER_PASSWORD').fill(credentials.password)
    await page.getByRole('button', { name: 'SIGN_IN', exact: true }).click()
    await expect(page).toHaveURL(/\/projects$/)
    await page.waitForTimeout(1500)
    await page.getByRole('button', { name: 'New project', exact: true }).click()
    await expect(page).toHaveURL(/\/design\/[0-9a-f]+$/)
    const projectURL = page.url()
    const projectId = new URL(projectURL).pathname.split('/').at(-1)
    mark('Describe a board; basic planning is active')
    await expect(page.getByText('Basic planning is active.', { exact: false })).toBeVisible()
    await page.locator('textarea').pressSequentially('USB temperature and humidity sensor', { delay: 65 })
    await page.waitForTimeout(1800)
    await page.getByRole('button', { name: 'Start validation' }).click()
    await expect(page.getByRole('heading', { name: 'Validation Trace' })).toBeVisible()
    mark('Live parts lookup and electrical checks')
    const deadline = Date.now() + 180_000
    let thread
    while (Date.now() < deadline) {
      const response = await page.request.get(`/api/projects/${projectId}/threads`)
      assert(response.ok())
      thread = (await response.json())[0]
      if (thread?.status === 'done' || thread?.status === 'completed') break
      assert(!['error', 'cancelled', 'abandoned'].includes(thread?.status), JSON.stringify(thread))
      // Do not silently accept warnings merely to produce a successful-looking demo.
      assert(!['awaiting', 'awaiting_input'].includes(thread?.status),
        'The live run needs a decision. Inspect the capture before answering and recording again.')
      await page.waitForTimeout(1000)
    }
    assert(['done', 'completed'].includes(thread?.status), 'Live design did not complete in three minutes')
    await expect(page.getByText(/Complete — \d+\/\d+ placed/)).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Bill of Materials' })).toBeVisible()
    await page.waitForTimeout(2500)
    await page.screenshot({ path: path.join(output, 'workspace.png') })
    report.liveRun = { status: thread.status, summary: thread.summary, acceptedWarnings: 0 }
    mark('Inspect and export the bill of materials')
    const downloadEvent = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Export BOM' }).click()
    const download = await downloadEvent
    const csvPath = path.join(work, 'fuze-bom.csv')
    await download.saveAs(csvPath)
    assert.match(await readFile(csvPath, 'utf8'), /^part_number,description,qty,unit_price/)
    const stored = await page.request.get(`/api/threads/${thread.id}/board`)
    assert.equal((await stored.json()).status, 'done')
    await page.waitForTimeout(2000)
    mark('Reopen a saved project')
    await page.getByRole('button', { name: 'All projects', exact: true }).click()
    const projectCard = page.getByRole('button', { name: /^USB temperature and humidity sensor/ })
    await expect(projectCard).toBeVisible()
    await page.waitForTimeout(2000)
    await page.screenshot({ path: path.join(output, 'projects.png') })
    await projectCard.click()
    await expect(page.getByText(/Complete — \d+\/\d+ placed/)).toBeVisible()
    await page.reload()
    await expect(page.getByText(/Complete — \d+\/\d+ placed/)).toBeVisible()
    await page.waitForTimeout(2000)
    mark('Review validation analytics')
    await page.getByRole('button', { name: 'Analytics', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'VALIDATION ANALYTICS' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'REFRESH', exact: true })).toBeVisible()
    await page.waitForTimeout(2500)
    await page.screenshot({ path: path.join(output, 'analytics.png') })
    mark('Explore remembered boards and parts')
    await page.getByRole('button', { name: 'Memory', exact: true }).click()
    await expect(page.getByText(/\d+ PARTS · \d+ BOARDS/)).toBeVisible()
    await page.waitForTimeout(3500)
    await page.screenshot({ path: path.join(output, 'memory-current.png') })
    mark('Sign out')
    await page.getByRole('button', { name: 'Settings', exact: true }).click()
    await page.getByRole('button', { name: 'SIGN_OUT', exact: true }).click()
    await expect(page).toHaveURL(/\/login$/)
    assert.equal((await page.request.get('/api/auth/me')).status(), 401)
    await page.waitForTimeout(1000)
    console.log('PASS: login, real design, CSV export, saved-board reload, analytics, memory, logout')
  })
  console.log('PASS: both README recordings completed without browser errors')
} finally {
  await context?.close()
  await browser.close()
}
