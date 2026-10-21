import type { ReactNode } from 'react'

type PanelProps = {
  id: string
  title: string
  /** Where the figures come from, in one sentence. Every view states its source. */
  source: string
  aside?: ReactNode
  children: ReactNode
}

export function Panel({ id, title, source, aside, children }: PanelProps) {
  return (
    <section aria-labelledby={`${id}-title`} className="bg-surface-container-low p-lg flex flex-col gap-md" data-testid={id}>
      <header className="flex flex-wrap items-start justify-between gap-sm">
        <div className="flex flex-col gap-xs">
          <h2 className="m-0 font-label-caps text-label-caps tracking-[0.1em] text-on-surface" id={`${id}-title`}>
            {title}
          </h2>
          <p className="m-0 text-body-sm text-outline max-w-[640px]">{source}</p>
        </div>
        {aside}
      </header>
      {children}
    </section>
  )
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="m-0 bg-surface-container-lowest p-md text-body-md text-on-surface-variant">{children}</p>
}
