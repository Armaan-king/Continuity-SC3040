import type { RuleOutcome } from '../lib/api'
import { OUTCOME_COLOURS, words } from './format'
import { EmptyNote, Panel } from './Panel'
import { RateFigure } from './RateFigure'
import { Legend, StackedBar, type Segment } from './StackedBar'

const LEGEND: Segment[] = [
  { key: 'pass', label: 'Pass', count: 0, colour: OUTCOME_COLOURS.pass },
  { key: 'warn', label: 'Warn', count: 0, colour: OUTCOME_COLOURS.warn },
  { key: 'fail', label: 'Fail', count: 0, colour: OUTCOME_COLOURS.fail },
  { key: 'unchecked', label: 'Unchecked', count: 0, colour: OUTCOME_COLOURS.unchecked, hatched: true },
]

function segments(row: RuleOutcome): Segment[] {
  return LEGEND.map((segment) => ({ ...segment, count: row[segment.key as 'pass' | 'warn' | 'fail' | 'unchecked'] }))
}

export function RuleOutcomes({ rules }: { rules: RuleOutcome[] }) {
  return (
    <Panel
      aside={<Legend segments={LEGEND} />}
      id="rule-outcomes"
      source="Every rule evaluation recorded in your completed runs, including re-checks after a repair. Unchecked means a figure the rule needs was not published; it is never counted as a pass."
      title="RULE OUTCOMES"
    >
      {rules.length === 0 ? (
        <EmptyNote>No rule evaluations are recorded for your completed runs.</EmptyNote>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse">
            <thead>
              <tr className="text-left font-label-caps text-[10px] text-outline">
                <th className="pb-sm pr-md font-normal" scope="col">RULE</th>
                <th className="pb-sm pr-md font-normal w-[34%]" scope="col">DISTRIBUTION</th>
                <th className="pb-sm pr-sm font-normal text-right" scope="col">PASS</th>
                <th className="pb-sm pr-sm font-normal text-right" scope="col">WARN</th>
                <th className="pb-sm pr-sm font-normal text-right" scope="col">FAIL</th>
                <th className="pb-sm pr-md font-normal text-right" scope="col">UNCHECKED</th>
                <th className="pb-sm font-normal" scope="col">FAILURE RATE</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((row) => (
                <tr className="align-middle odd:bg-surface-container-lowest/40" data-rule={row.rule} key={row.rule}>
                  <th className="py-sm pr-md text-left font-data-tabular text-data-tabular font-normal text-on-surface" scope="row">
                    {words(row.rule)}
                  </th>
                  <td className="py-sm pr-md">
                    <StackedBar
                      segments={segments(row)}
                      summary={`${words(row.rule)}: ${row.pass} pass, ${row.warn} warn, ${row.fail} fail, ${row.unchecked} unchecked`}
                    />
                  </td>
                  <td className="py-sm pr-sm text-right font-data-tabular text-data-tabular text-on-surface-variant">{row.pass}</td>
                  <td className="py-sm pr-sm text-right font-data-tabular text-data-tabular text-on-surface-variant">{row.warn}</td>
                  <td className="py-sm pr-sm text-right font-data-tabular text-data-tabular text-on-surface-variant">{row.fail}</td>
                  <td className="py-sm pr-md text-right font-data-tabular text-data-tabular text-on-surface-variant">{row.unchecked}</td>
                  <td className="py-sm">
                    <RateFigure label="failure rate" rate={row.failure_rate} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
