import type { ReactNode } from 'react'

import type { Lifecycle, SupplyExposure as Supply } from '../lib/api'
import { OUTCOME_COLOURS } from './format'
import { EmptyNote, Panel } from './Panel'
import { RateFigure } from './RateFigure'
import { Legend, StackedBar, type Segment } from './StackedBar'

const STAGES: Array<Segment & { key: Lifecycle }> = [
  { key: 'active', label: 'Active', count: 0, colour: OUTCOME_COLOURS.pass },
  { key: 'nrnd', label: 'Not recommended (NRND)', count: 0, colour: OUTCOME_COLOURS.warn },
  { key: 'obsolete', label: 'Obsolete', count: 0, colour: OUTCOME_COLOURS.fail },
  { key: 'unknown', label: 'Unknown', count: 0, colour: OUTCOME_COLOURS.unchecked, hatched: true },
]

export function SupplyExposure({ supply }: { supply: Supply }) {
  const mix = STAGES.map((stage) => ({ ...stage, count: supply.lifecycle[stage.key] }))
  return (
    <Panel
      id="supply-exposure"
      source="The distinct parts on your finished boards, at the lifecycle status they were selected with. A part with no published status is unknown, not active."
      title="SUPPLY EXPOSURE"
    >
      {supply.parts === 0 ? (
        <EmptyNote>No parts have been placed on a completed board yet.</EmptyNote>
      ) : (
        <div className="flex flex-col gap-lg">
          <div className="flex flex-col gap-sm">
            <StackedBar
              segments={mix}
              summary={`Lifecycle across ${supply.parts} parts: ${mix.map((stage) => `${stage.count} ${stage.label}`).join(', ')}`}
            />
            <Legend segments={STAGES} />
            <dl className="m-0 grid grid-cols-2 gap-sm sm:grid-cols-4">
              {mix.map((stage) => (
                <div className="flex flex-col-reverse" key={stage.key}>
                  <dt className="font-label-caps text-[10px] text-outline">{stage.label.toUpperCase()}</dt>
                  <dd className="m-0 font-data-tabular text-[15px] leading-5 text-on-surface">{stage.count}</dd>
                </div>
              ))}
            </dl>
          </div>
          <div className="grid grid-cols-1 gap-md md:grid-cols-3">
            <Tile label="DISTINCT PARTS">
              <span className="font-display-mono text-display-mono text-on-surface">{supply.parts}</span>
              <span className="font-data-tabular text-[11px] text-outline">{supply.placements} placements across boards</span>
            </Tile>
            <Tile label="AT RISK (NRND OR OBSOLETE)">
              <RateFigure label="parts at risk" rate={supply.at_risk_rate} />
            </Tile>
            <Tile label="SINGLE-SOURCE PARTS">
              <span className="font-label-caps text-label-caps text-on-surface-variant flex items-center gap-xs" data-testid="single-source-withheld">
                <span aria-hidden className="material-symbols-outlined text-[14px]">visibility_off</span>
                WITHHELD
              </span>
              <span className="text-body-sm text-on-surface-variant">{supply.single_source.withheld}</span>
            </Tile>
          </div>
        </div>
      )}
    </Panel>
  )
}

function Tile({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-xs bg-surface-container-lowest/60 p-md">
      <span className="font-label-caps text-[10px] text-outline">{label}</span>
      {children}
    </div>
  )
}
