import type { Rate } from '../lib/api'
import { counts, percent } from './format'

type RateFigureProps = {
  rate: Rate
  /** What the rate measures, for the accessible name: "failure rate", "held". */
  label: string
}

/** A rate and the counts it came from — or the counts and the reason it was withheld.
 *  Withholding is shown in words, never as a blank or a zero. */
export function RateFigure({ rate, label }: RateFigureProps) {
  if (rate.withheld !== undefined) {
    return (
      <div className="flex flex-col gap-[2px]" data-testid="rate-withheld">
        <span className="font-label-caps text-label-caps text-on-surface-variant flex items-center gap-xs">
          <span aria-hidden className="material-symbols-outlined text-[14px]">visibility_off</span>
          WITHHELD
        </span>
        <span className="font-data-tabular text-[11px] leading-[15px] text-outline">{counts(rate)}</span>
        <span className="text-body-sm text-on-surface-variant max-w-[260px]">{rate.withheld}</span>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-[2px]" data-testid="rate-value">
      <span aria-label={`${label} ${percent(rate.value)}`} className="font-data-tabular text-[15px] leading-5 text-on-surface">
        {percent(rate.value)}
      </span>
      <span className="font-data-tabular text-[11px] leading-[15px] text-outline">{counts(rate)}</span>
    </div>
  )
}
