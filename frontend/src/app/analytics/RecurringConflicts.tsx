import type { RecurringConflict } from '../lib/api'
import { words } from './format'
import { EmptyNote, Panel } from './Panel'

/** `thermal_dissipation|regulator|linear|pkg:SOT|drop:1-3V` → readable tokens. The first
 *  token is the rule, already shown as the row's heading. */
function tokens(signature: string) {
  return signature.split('|').slice(1).map((token) => {
    const [key, value] = token.split(':')
    if (value === undefined) return words(key)
    const names: Record<string, string> = { pkg: 'package', drop: 'drop', load: 'load' }
    return `${names[key] ?? key} ${value}`
  })
}

export function RecurringConflicts({ signatures }: { signatures: RecurringConflict[] }) {
  return (
    <Panel
      id="recurring-conflicts"
      source="Conflicts with the same engine-recorded shape in at least two of your runs. The shape names the kind of part and its electrical situation, never a specific part, because the same part can pass on one board and fail on another."
      title="RECURRING CONFLICTS"
    >
      {signatures.length === 0 ? (
        <EmptyNote>No conflict has recurred across two runs yet.</EmptyNote>
      ) : (
        <ol className="m-0 flex list-none flex-col gap-sm p-0">
          {signatures.map((row) => (
            <li className="grid grid-cols-1 items-center gap-md bg-surface-container-lowest/60 p-md lg:grid-cols-[minmax(0,1fr)_200px_220px]" data-signature={row.signature} key={row.signature}>
              <div className="flex min-w-0 flex-col gap-xs">
                <span className="font-data-tabular text-data-tabular text-on-surface">{words(row.rule)}</span>
                <div className="flex flex-wrap gap-xs">
                  {tokens(row.signature).map((token) => (
                    <span className="bg-surface-container-highest px-xs py-[2px] font-data-tabular text-[11px] text-on-surface-variant" key={token}>
                      {token}
                    </span>
                  ))}
                </div>
              </div>
              <dl className="m-0 flex gap-lg">
                <Figure label="RUNS" value={row.runs} />
                <Figure label="PROJECTS" value={row.projects} />
                <Figure label="TIMES" value={row.occurrences} />
              </dl>
              <div>
                <span className="font-label-caps text-[10px] text-outline">MOST OFTEN HELD</span>
                {row.best_repair ? (
                  <p className="m-0 flex flex-col">
                    <span className="font-data-tabular text-data-tabular text-on-surface">{words(row.best_repair.action)}</span>
                    <span className="font-data-tabular text-[11px] text-outline">
                      held {row.best_repair.held} of {row.best_repair.applied} times applied
                    </span>
                  </p>
                ) : (
                  <p className="m-0 text-body-sm text-on-surface-variant">No repair has held for this conflict yet.</p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}

function Figure({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col-reverse">
      <dt className="font-label-caps text-[10px] text-outline">{label}</dt>
      <dd className="m-0 font-data-tabular text-[15px] leading-5 text-on-surface">{value}</dd>
    </div>
  )
}
