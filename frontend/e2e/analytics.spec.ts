import { expect, test } from '@playwright/test'

import { API_URL, registerFreshAccount, signIn, signInThroughForm, watchConsole } from './helpers'

/**
 * UC-16 Review Validation Analytics, driven through the real interface against the seeded
 * corpus (25 completed runs, 2 abandoned, 1 errored — see tools/seed_analytics.py).
 */

test.describe('validation analytics', () => {
  test('a signed-out visitor is sent to sign in', async ({ page }) => {
    await page.goto('/analytics')
    await expect(page).toHaveURL(/\/login$/)
  })

  test('the side rail opens the analysis from the projects page', async ({ page }) => {
    const errors = watchConsole(page)
    await signInThroughForm(page)

    await page.getByRole('button', { name: 'Analytics' }).click()

    await expect(page).toHaveURL(/\/analytics$/)
    await expect(page.getByRole('heading', { name: 'VALIDATION ANALYTICS' })).toBeVisible()
    expect(errors).toEqual([])
  })

  test('the overview counts only completed runs and names what was excluded', async ({ page }) => {
    await signIn(page)
    await page.goto('/analytics')

    const overview = page.getByTestId('analytics-overview')
    await expect(overview).toContainText('COMPLETED RUNS')
    await expect(overview).toContainText('25')
    await expect(overview).toContainText('excluded: 2 abandoned · 1 errored · 0 in progress')
  })

  test('every rule shows all four outcomes, and a mostly-unchecked rate is withheld with its reason', async ({ page }) => {
    await signIn(page)
    await page.goto('/analytics')

    const rules = page.getByTestId('rule-outcomes')
    await expect(rules.getByRole('columnheader', { name: 'UNCHECKED' })).toBeVisible()

    const pinBudget = rules.locator('[data-rule="pin_budget"]')
    await expect(pinBudget.getByTestId('rate-withheld')).toContainText('WITHHELD')
    await expect(pinBudget).toContainText('above the 20% limit for reporting a rate')
    await expect(pinBudget.getByTestId('rate-value')).toHaveCount(0)

    const thermal = rules.locator('[data-rule="thermal_dissipation"]')
    await expect(thermal.getByTestId('rate-value')).toContainText(/\d+(\.\d)?%/)
    await expect(thermal.getByTestId('rate-value')).toContainText(/\d+ of \d+ · \d+ unchecked/)
  })

  test('hovering a bar segment shows its count and share', async ({ page }) => {
    await signIn(page)
    await page.goto('/analytics')

    await page.locator('[data-rule="pin_budget"] [data-segment="unchecked"]').hover()

    const tooltip = page.getByRole('tooltip')
    await expect(tooltip).toContainText('UNCHECKED')
    await expect(tooltip).toContainText(/\d+ of \d+ · \d+%/)
  })

  test('repair effectiveness, supply exposure and recurring conflicts are all reported', async ({ page }) => {
    await signIn(page)
    await page.goto('/analytics')

    const repairs = page.getByTestId('repair-effectiveness')
    for (const action of ['change rail', 'swap', 'change topology']) {
      await expect(repairs).toContainText(action)
    }
    await expect(repairs.getByTestId('rate-value').first()).toBeVisible()

    const supply = page.getByTestId('supply-exposure')
    await expect(supply).toContainText('OBSOLETE')
    await expect(supply.getByTestId('single-source-withheld')).toBeVisible()
    await expect(supply).toContainText('one distributor feed (JLCPCB)')

    const recurring = page.getByTestId('recurring-conflicts')
    await expect(recurring.getByRole('listitem')).toHaveCount(4)
    await expect(recurring).toContainText('MOST OFTEN HELD')
  })

  test('every figure on screen matches the API it was computed from', async ({ page }) => {
    await signIn(page)
    const report = await (await page.request.get(`${API_URL}/analytics`)).json()
    await page.goto('/analytics')

    for (const rule of report.rules) {
      const row = page.locator(`[data-rule="${rule.rule}"]`)
      const cells = row.getByRole('cell')
      await expect(cells.nth(1)).toHaveText(String(rule.pass))
      await expect(cells.nth(2)).toHaveText(String(rule.warn))
      await expect(cells.nth(3)).toHaveText(String(rule.fail))
      await expect(cells.nth(4)).toHaveText(String(rule.unchecked))
      const counts = `${rule.failure_rate.numerator} of ${rule.failure_rate.denominator} · ${rule.failure_rate.unchecked} unchecked`
      await expect(row).toContainText(counts)
    }
  })

  test('viewing the analysis changes nothing', async ({ page }) => {
    await signIn(page)
    const before = await (await page.request.get(`${API_URL}/analytics`)).json()

    await page.goto('/analytics')
    await page.getByRole('button', { name: 'REFRESH' }).click()
    await expect(page.getByTestId('rule-outcomes')).toBeVisible()

    const after = await (await page.request.get(`${API_URL}/analytics`)).json()
    expect(after).toEqual(before)
  })

  test('an engineer with no completed runs sees an empty state, not an error', async ({ page }) => {
    await registerFreshAccount(page)
    await page.goto('/analytics')

    await expect(page.getByTestId('analytics-empty')).toContainText('NO COMPLETED RUNS YET')
    await expect(page.getByRole('alert')).toHaveCount(0)
  })

  test('when retrieval fails the page says so and shows no partial analysis', async ({ page }) => {
    await signIn(page)
    let failing = true
    await page.route(`${API_URL}/analytics`, async (route) => {
      if (failing) {
        await route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"down"}' })
      } else {
        await route.continue()
      }
    })

    await page.goto('/analytics')

    await expect(page.getByRole('alert')).toContainText('ANALYSIS COULD NOT LOAD')
    await expect(page.getByTestId('rule-outcomes')).toHaveCount(0)
    await expect(page.getByTestId('analytics-overview')).toHaveCount(0)

    failing = false
    await page.getByRole('button', { name: 'RETRY' }).click()
    await expect(page.getByTestId('rule-outcomes')).toBeVisible()
  })
})
