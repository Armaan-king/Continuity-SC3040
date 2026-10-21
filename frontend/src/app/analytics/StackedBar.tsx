import { useState } from 'react'

import { percent } from './format'

export type Segment = {
  key: string
  label: string
  count: number
  colour: string
  /** Hatched marks carry "not evaluated" without relying on colour. */
  hatched?: boolean
}

const HATCH = 'repeating-linear-gradient(135deg, rgba(8,15,17,0.55) 0 2px, transparent 2px 5px)'

type StackedBarProps = {
  segments: Segment[]
  /** Spoken in place of the bar; the visible counts beside it carry the same figures. */
  summary: string
}

/** One horizontal 100% bar. Segments are separated by a 2px surface gap, never a stroke,
 *  and each shows its count and share on hover. Zero-count segments are not drawn. */
export function StackedBar({ segments, summary }: StackedBarProps) {
  const [hovered, setHovered] = useState<string | null>(null)
  const total = segments.reduce((sum, segment) => sum + segment.count, 0)
  const drawn = segments.filter((segment) => segment.count > 0)

  if (total === 0) {
    return <div aria-label={summary} className="h-3 w-full rounded bg-surface-container-highest" role="img" />
  }

  let offset = 0
  const placed = drawn.map((segment) => {
    const start = offset
    offset += segment.count / total
    return { ...segment, start, share: segment.count / total }
  })
  const active = placed.find((segment) => segment.key === hovered)

  return (
    <div className="relative w-full" onMouseLeave={() => setHovered(null)}>
      <div aria-label={summary} className="flex h-3 w-full gap-[2px]" role="img">
        {placed.map((segment, index) => (
          <div
            className={`h-full transition-opacity duration-150 ${index === 0 ? 'rounded-l' : ''} ${
              index === placed.length - 1 ? 'rounded-r' : ''
            } ${hovered && hovered !== segment.key ? 'opacity-50' : ''}`}
            data-segment={segment.key}
            key={segment.key}
            onMouseEnter={() => setHovered(segment.key)}
            style={{
              flexGrow: segment.count,
              flexBasis: 0,
              minWidth: 3,
              backgroundColor: segment.colour,
              backgroundImage: segment.hatched ? HATCH : undefined,
            }}
          />
        ))}
      </div>
      {active ? (
        <div
          className="pointer-events-none absolute bottom-[calc(100%+6px)] z-20 -translate-x-1/2 whitespace-nowrap bg-surface-container-highest px-sm py-xs shadow-lg"
          role="tooltip"
          style={{ left: `${Math.min(88, Math.max(12, (active.start + active.share / 2) * 100))}%` }}
        >
          <span className="flex items-center gap-xs font-label-caps text-label-caps text-on-surface">
            <span
              aria-hidden
              className="inline-block h-2 w-2 rounded-sm"
              style={{ backgroundColor: active.colour, backgroundImage: active.hatched ? HATCH : undefined }}
            />
            {active.label.toUpperCase()}
          </span>
          <span className="font-data-tabular text-[12px] text-on-surface-variant">
            {active.count} of {total} · {percent(active.share)}
          </span>
        </div>
      ) : null}
    </div>
  )
}

export function Legend({ segments }: { segments: Array<Pick<Segment, 'key' | 'label' | 'colour' | 'hatched'>> }) {
  return (
    <ul className="m-0 flex list-none flex-wrap gap-md p-0">
      {segments.map((segment) => (
        <li className="flex items-center gap-xs font-label-caps text-label-caps text-on-surface-variant" key={segment.key}>
          <span
            aria-hidden
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ backgroundColor: segment.colour, backgroundImage: segment.hatched ? HATCH : undefined }}
          />
          {segment.label.toUpperCase()}
        </li>
      ))}
    </ul>
  )
}
