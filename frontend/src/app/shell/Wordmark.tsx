type WordmarkSize = 'sm' | 'md' | 'lg'

const TEXT_SIZE: Record<WordmarkSize, string> = {
  sm: 'text-[13px]',
  md: 'text-lg',
  lg: 'text-[24px]',
}

const ICON_SIZE: Record<WordmarkSize, string> = {
  sm: 'text-[16px]',
  md: 'text-xl',
  lg: 'text-[26px]',
}

/**
 * The one Continuity wordmark. Every screen renders this and nothing renders its own.
 *
 * There were four before: `developer_board` in copper-container on the projects and design
 * headers, `memory` in copper on the landing and auth pages, and no glyph at all on the
 * brief entry — three glyphs and two tones for one product, which reads as three products
 * to anyone moving between the pages. The header pair was taken as canonical because it is
 * what a user sees for the whole of a run.
 *
 * Keep this the only definition. A wordmark copied inline is a wordmark that drifts.
 */
export function Wordmark({ size = 'md' }: { size?: WordmarkSize }) {
  return (
    <div className="flex items-center gap-sm font-display-mono text-display-mono uppercase tracking-tighter text-primary-container">
      <span
        aria-hidden
        className={`material-symbols-outlined ${ICON_SIZE[size]}`}
        style={{ fontVariationSettings: "'FILL' 1" }}
      >
        developer_board
      </span>
      <span className={TEXT_SIZE[size]}>CONTINUITY</span>
    </div>
  )
}
