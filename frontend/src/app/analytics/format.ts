import type { Rate } from '../lib/api'

/** Status colours for outcome marks. Validated together on the dark surface (#0d1516):
 *  colour-blind separation ΔE ≥ 9 for every pair, ≥ 3:1 contrast. Unchecked is a neutral
 *  grey and is always hatched, so it never depends on hue to be told apart. */
export const OUTCOME_COLOURS = {
  pass: '#4ade80',
  warn: '#fab219',
  fail: '#e5484d',
  unchecked: '#849396',
} as const

export type Outcome = keyof typeof OUTCOME_COLOURS

export function words(identifier: string) {
  return identifier.replaceAll('_', ' ')
}

export function percent(value: number) {
  const scaled = value * 100
  return `${scaled >= 10 || scaled === 0 ? Math.round(scaled) : scaled.toFixed(1)}%`
}

export function counts(rate: Rate) {
  return `${rate.numerator} of ${rate.denominator} · ${rate.unchecked} unchecked`
}
