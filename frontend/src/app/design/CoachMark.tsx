import { useLayoutEffect, useRef, useState } from 'react'

type CoachMarkProps = {
  step: number
  total: number
  heading: string
  body: string
  targetRect: DOMRect | null
  onNext: () => void
  onSkip: () => void
  nextLabel?: string
  nextDisabled?: boolean
}

type Position = {
  left: number
  top: number
}

const VIEWPORT_GUTTER = 12
const TARGET_GAP = 12

export function CoachMark({
  step,
  total,
  heading,
  body,
  targetRect,
  onNext,
  onSkip,
  nextLabel,
  nextDisabled = false,
}: CoachMarkProps) {
  const cardRef = useRef<HTMLDivElement | null>(null)
  const [position, setPosition] = useState<Position>({
    left: VIEWPORT_GUTTER,
    top: VIEWPORT_GUTTER,
  })
  /** The first placement is a jump from the gutter to wherever the card belongs, and
   *  animating that reads as the card flying in from the corner. Glide only after it. */
  const [placed, setPlaced] = useState(false)

  useLayoutEffect(() => {
    const card = cardRef.current
    if (!card) {
      return
    }

    const { width, height } = card.getBoundingClientRect()
    const maxLeft = Math.max(VIEWPORT_GUTTER, window.innerWidth - width - VIEWPORT_GUTTER)
    const maxTop = Math.max(VIEWPORT_GUTTER, window.innerHeight - height - VIEWPORT_GUTTER)

    if (!targetRect) {
      setPosition({
        left: Math.max(VIEWPORT_GUTTER, (window.innerWidth - width) / 2),
        top: Math.max(VIEWPORT_GUTTER, (window.innerHeight - height) / 2),
      })
      return
    }

    const right = targetRect.right + TARGET_GAP
    const left = targetRect.left - width - TARGET_GAP
    const preferredLeft = right + width <= window.innerWidth - VIEWPORT_GUTTER ? right : left

    setPosition({
      left: Math.min(maxLeft, Math.max(VIEWPORT_GUTTER, preferredLeft)),
      top: Math.min(
        maxTop,
        Math.max(VIEWPORT_GUTTER, targetRect.top + targetRect.height / 2 - height / 2),
      ),
    })
    setPlaced(true)
  }, [targetRect, step])

  return (
    // Positioned by transform rather than by `left`/`top`: the browser can carry a
    // transform on the compositor, and the card is moving across a full-screen dimmed
    // overlay where a layout-driven move judders.
    <div
      className={`fixed left-0 top-0 z-[70] w-[280px] max-w-[280px] rounded-DEFAULT border border-tertiary-container bg-surface-container-high p-md shadow-lg will-change-transform motion-reduce:transition-none ${
        placed ? 'transition-transform duration-[320ms] ease-glide' : ''
      }`}
      ref={cardRef}
      style={{ transform: `translate3d(${position.left}px, ${position.top}px, 0)` }}
    >
      <div className="flex flex-col gap-sm">
        {/* Keyed on the step so the copy fades in rather than swapping under a card that
            is still moving — the two together are what made "Next" feel like a cut. The
            controls stay outside the key so the button the reader is clicking is not
            replaced underneath them. */}
        <div className="coach-copy flex flex-col gap-sm" key={step}>
          <span className="font-label-caps text-label-caps text-tertiary-fixed-dim">
            STEP {step + 1} OF {total}
          </span>
          <h2 className="font-headline-sm text-headline-sm text-on-surface">{heading}</h2>
          <p className="font-body-sm text-body-sm text-on-surface-variant">{body}</p>
        </div>

        <div className="flex items-center justify-between gap-md pt-xs">
          <button
            className="font-label-caps text-label-caps text-on-surface-variant transition-colors hover:text-on-surface"
            onClick={onSkip}
            type="button"
          >
            Skip
          </button>
          <button
            className="rounded-DEFAULT bg-primary-container px-md py-xs font-label-caps text-label-caps text-on-primary-fixed transition-colors hover:bg-primary-fixed disabled:cursor-wait disabled:opacity-40"
            disabled={nextDisabled}
            onClick={onNext}
            type="button"
          >
            {nextLabel ?? 'NEXT'}
          </button>
        </div>

        <div className="flex items-center gap-xs" aria-label={`Step ${step + 1} of ${total}`}>
          {Array.from({ length: total }, (_, index) => (
            <span
              className={`h-1.5 w-1.5 rounded-pill transition-colors duration-[320ms] ${
                index === step ? 'bg-tertiary-container' : 'bg-outline'
              }`}
              key={index}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
