import type { RepairOutcome } from '../lib/api'
import { OUTCOME_COLOURS, words } from './format'
import { EmptyNote, Panel } from './Panel'
import { RateFigure } from './RateFigure'
import { Legend, StackedBar, type Segment } from './StackedBar'

const LEGEND: Segment[] = [
  { key: 'held', label: 'Held', count: 0, colour: OUTCOME_COLOURS.pass },
  { key: 'not_held', label: 'Did not hold', count: 0, colour: OUTCOME_COLOURS.fail },
  { key: 'unchecked', label: 'Re-check unchecked', count: 0, colour: OUTCOME_COLOURS.unchecked, hatched: true },
]

export function RepairEffectiveness({ repairs }: { repairs: RepairOutcome[] }) {
  return (
    <Panel
      aside={repairs.length ? <Legend segments={LEGEND} /> : undefined}
      id="repair-effectiveness"
      source="Each repair the engine applied, and whether the rule it targeted passed when the board was re-validated. A repair whose re-check could not be evaluated is not counted as held."
      title="REPAIR EFFECTIVENESS"
    >
      {repairs.length === 0 ? (
        <EmptyNote>No repairs are recorded. None of your completed runs needed the engine to repair a conflict.</EmptyNote>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-md p-0">
          {repairs.map((row) => (
            <li className="grid grid-cols-1 items-center gap-sm md:grid-cols-[180px_1fr_200px]" data-action={row.action} key={row.action}>
              <div className="flex flex-col">
                <span className="font-data-tabular text-data-tabular text-on-surface">{words(row.action)}</span>
                <span className="font-label-caps text-[10px] text-outline">APPLIED {row.applied}×</span>
              </div>
              <StackedBar
                segments={LEGEND.map((segment) => ({ ...segment, count: row[segment.key as 'held' | 'not_held' | 'unchecked'] }))}
                summary={`${words(row.action)}: ${row.held} held, ${row.not_held} did not hold, ${row.unchecked} unchecked`}
              />
              <RateFigure label="held" rate={row.held_rate} />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
