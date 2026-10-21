import { useEffect, useMemo, useRef } from 'react'

import type {
  Alternative,
  CheckEvent,
  ConflictEvent,
  Edge,
  ReasoningEvent,
  RepairEvent,
  Slot,
  ErrorEvent,
} from '../lib/types'

type ReasoningItem = ReasoningEvent | CheckEvent | RepairEvent | ErrorEvent

type SessionConflict = (ConflictEvent & { alternatives: Alternative[] }) | null

type ConflictPanelProps = {
  open: boolean
  onClose: () => void
  conflict: SessionConflict
  slots: Slot[]
  edges: Edge[]
  reasoning: ReasoningItem[]
}

function toTitleCase(token: string) {
  return token
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function stockChip(alt: Alternative) {
  if ((alt.stock ?? 0) > 0) {
    // In stock is a good state, so it takes the valid green rather than the brand hue.
    // Painting it with the accent made availability look like a feature, not a verdict.
    return { label: 'In Stock', className: 'bg-[#4ade80]' }
  }

  const days = alt.lead_time_days ?? 7
  const weeks = Math.max(1, Math.ceil(days / 7))
  return { label: `${weeks}-week lead`, className: 'bg-tertiary-container' }
}

export function ConflictPanel({ open, onClose, conflict, slots, edges, reasoning }: ConflictPanelProps) {
  const slotsById = useMemo(() => new Map(slots.map((slot) => [slot.id, slot])), [slots])
  const terminalRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open || !conflict) {
      return
    }

    const terminal = terminalRef.current
    if (!terminal) {
      return
    }

    terminal.scrollTop = terminal.scrollHeight
  }, [open, conflict, reasoning])

  if (!open || !conflict) {
    return null
  }

  const edge = edges.find((candidate) => candidate.id === conflict.edge)
  const connectorLabel = edge?.label ?? conflict.edge

  const involvedSlots = conflict.involved
    .map((slotId) => slotsById.get(slotId))
    .filter((slot): slot is Slot => Boolean(slot))

  const visibleInvolved = involvedSlots.slice(0, 2)
  const overflowInvolved = involvedSlots.slice(2)

  const terminalLines = reasoning
    .filter((item): item is CheckEvent => item.type === 'check')
    .filter((item) => conflict.involved.includes(item.slot))
    .slice(-8)
    .map((item) => `> ${item.rule} [${item.status.toUpperCase()}] ${item.detail}`)

  const price = (alt: Alternative) =>
    alt.unit_price == null ? '—' : `${alt.currency} ${alt.unit_price.toFixed(2)}`

  return (
    <aside className="w-[480px] flex-shrink-0 bg-surface-container-low border-l border-surface-bright flex flex-col shadow-[-4px_0_12px_rgba(0,0,0,0.5)] animate-[slideInRight_0.3s_ease-out]" data-tour="drawer">
      <div className="p-lg border-b border-surface-bright flex flex-col space-y-md">
        <div className="flex justify-between items-start">
          <div className="flex items-center space-x-sm">
            <span className="material-symbols-outlined text-error text-[20px]">warning</span>
            <h2 className="font-headline-sm text-headline-sm text-on-surface">{conflict.message}</h2>
          </div>
          <button
            className="text-on-surface-variant hover:text-on-surface transition-colors"
            onClick={onClose}
            type="button"
          >
            <span className="material-symbols-outlined text-[20px]">close</span>
          </button>
        </div>
        <div className="flex items-center">
          <span className="px-sm py-xs bg-error-container text-on-error-container font-label-caps text-label-caps rounded-DEFAULT border border-error opacity-90 flex items-center">
            CRITICAL
          </span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-lg flex flex-col space-y-xl">
        <div className="flex flex-col space-y-sm">
          <h3 className="font-label-caps text-label-caps text-on-surface-variant uppercase tracking-wider">
            Affected Components
          </h3>
          <div className="flex items-stretch justify-between bg-[#0B0C0E] border border-surface-bright rounded-DEFAULT p-md relative gap-sm">
            {visibleInvolved.map((slot, index) => {
              const evidence = conflict.evidence.find((item) => item.slot === slot.id)

              return (
                <div className="flex flex-col w-[40%]" key={slot.id}>
                  <span className="font-data-tabular text-data-tabular text-primary font-bold mb-xs">
                    {slot.part?.mpn ?? slot.label}
                  </span>
                  <span className="font-body-sm text-body-sm text-on-surface-variant mb-md">{slot.label}</span>
                  <div
                    className={`p-sm rounded-DEFAULT border flex justify-between items-center ${
                      index === 1 ? 'bg-error-container/20 border-error/50' : 'bg-surface-container border-surface-bright'
                    }`}
                  >
                    <span className="font-body-sm text-body-sm text-on-surface-variant">
                      {evidence?.field ?? 'Constraint'}
                    </span>
                    <span className={`font-data-tabular text-data-tabular ${index === 1 ? 'text-error font-bold' : 'text-on-surface'}`}>
                      {evidence?.value ?? '—'}
                    </span>
                  </div>
                </div>
              )
            })}

            <div className="flex flex-col items-center justify-center w-[20%] relative">
              <div className="h-[2px] w-full bg-error absolute top-1/2 -translate-y-1/2 -z-10"></div>
              <div className="bg-[#0B0C0E] p-xs rounded-pill border-2 border-error z-10 flex items-center justify-center">
                <span className="material-symbols-outlined text-error text-[16px]">bolt</span>
              </div>
              <span className="font-label-caps text-[9px] text-error mt-sm bg-[#0B0C0E] px-xs rounded-pill">
                {connectorLabel}
              </span>
            </div>
          </div>

          {overflowInvolved.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {overflowInvolved.map((slot) => (
                <span
                  className="px-xs py-[2px] bg-surface-container border border-surface-bright rounded text-[10px] font-data-tabular"
                  key={slot.id}
                >
                  + {slot.part?.mpn ?? slot.label}
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="flex flex-col space-y-sm">
          <h3 className="font-label-caps text-label-caps text-on-surface-variant uppercase tracking-wider">
            Trace Evidence
          </h3>
          <div className="bg-surface-container rounded-DEFAULT border border-surface-bright overflow-hidden">
            {conflict.evidence.map((item, index) => (
              <a
                className={`flex items-center px-md py-sm hover:bg-surface-variant transition-colors min-h-[32px] ${
                  index > 0 ? 'border-t border-surface-bright' : ''
                }`}
                href={item.source}
                key={`${item.slot}-${item.field}-${index}`}
                rel="noopener noreferrer"
                target="_blank"
              >
                <span className="material-symbols-outlined text-on-surface-variant text-[14px] mr-sm">
                  description
                </span>
                <span className="font-body-sm text-body-sm text-on-surface flex-1">{item.field}</span>
                <span className="font-data-tabular text-[11px] text-primary">{item.value}</span>
              </a>
            ))}
          </div>
        </div>

        <div className="flex flex-col space-y-sm">
          <h3 className="font-label-caps text-label-caps text-on-surface-variant uppercase tracking-wider">
            Suggested Replacements
          </h3>
          <div className="flex flex-col space-y-sm">
            {conflict.alternatives.map((alternative) => {
              const chip = stockChip(alternative)

              return (
                <div
                  className="bg-surface-container border border-surface-bright rounded-DEFAULT p-md flex flex-col hover:border-outline-variant transition-colors"
                  key={alternative.mpn}
                >
                  <div className="flex justify-between items-start mb-sm">
                    <div className="flex items-center space-x-sm">
                      <span className="font-data-tabular text-data-tabular text-on-surface font-bold">
                        {alternative.mpn}
                      </span>
                      {alternative.recommended ? (
                        <span className="px-xs py-[2px] bg-primary-fixed-dim bg-opacity-20 text-primary-fixed-dim font-label-caps text-[9px] rounded-sm border border-primary-fixed-dim border-opacity-30">
                          Recommended
                        </span>
                      ) : null}
                    </div>
                    <span className="font-data-tabular text-data-tabular text-on-surface">
                      {price(alternative)}
                    </span>
                  </div>
                  <div className="flex justify-between items-end">
                    <div className="flex flex-col space-y-xs">
                      <div className="flex items-center space-x-xs">
                        <div className={`w-2 h-2 rounded-pill ${chip.className}`}></div>
                        <span className="font-body-sm text-[11px] text-on-surface-variant">{chip.label}</span>
                      </div>
                      <a
                        className="font-body-sm text-[11px] text-on-surface-variant italic hover:text-on-surface"
                        href={alternative.datasheet ?? '#'}
                        rel="noopener noreferrer"
                        target="_blank"
                      >
                        {alternative.reason}
                      </a>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      <div
        className="max-h-[140px] bg-[#0B0C0E] border-t border-surface-bright p-sm font-data-tabular text-[10px] text-on-surface-variant opacity-80 flex flex-col leading-tight overflow-y-auto"
        data-tour="conflict-check-log"
        ref={terminalRef}
      >
        {terminalLines.map((line, index) => (
          <div key={`term-${index}`}>{line}</div>
        ))}
        <div className="text-error">&gt; conflict [{toTitleCase(conflict.rule)}] {conflict.message}</div>
      </div>
    </aside>
  )
}
