import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { CoachMark } from './CoachMark'

type WalkthroughProps = {
  onFinish: () => void
  initialStep?: number
  onStepChange?: (step: number) => void
  briefSubmitted?: boolean
}

type WalkthroughStep = {
  selector: string
  heading: string
  body: string
  nextLabel?: string
}

const SPOTLIGHT_PADDING = 6
const TRACE_SELECTOR = '[data-tour="trace"]'

const steps: WalkthroughStep[] = [
  {
    selector: '[data-tour="brief-entry"]',
    heading: 'Start with the brief',
    body: 'Describe the board in plain language. This is the same input you will use for every project.',
  },
  {
    selector: '[data-tour="graph"]',
    heading: 'The board, as a graph',
    body: 'Every component and every connection between them. Parts arrive one at a time as the run works through the board.',
  },
  {
    selector: '[data-tour="trace"]',
    heading: 'It shows its working',
    body: 'Each line is a real check against a real part — voltage, current, interfaces, stock.',
  },
  {
    selector: '[data-tour="drawer"]',
    heading: 'A conflict has a verdict',
    body: 'This part fails the stock requirement. The drawer shows the exact evidence behind that verdict.',
  },
  {
    selector: '[data-tour="conflict-check-log"]',
    heading: 'The check log is still here',
    body: 'These are the rules that passed on the failing part. It is the proof that the engine checked the whole board, not only what broke.',
  },
  {
    selector: '[data-tour="bom-export"]',
    heading: 'Parts, prices, and export',
    body: 'The bill of materials is made of real selected parts and current prices. Export it when the board is ready to hand off.',
  },
  {
    selector: '[data-tour="memory-detail"]',
    heading: 'Memory keeps the finding',
    body: 'This graph connects parts to boards. Open a part to see the finding it carried, even after the board was repaired.',
    nextLabel: 'START_BUILDING',
  },
]

export function Walkthrough({ initialStep = 0, onFinish, onStepChange, briefSubmitted = false }: WalkthroughProps) {
  const [step, setStep] = useState(initialStep)
  const [targetRect, setTargetRect] = useState<DOMRect | null>(null)
  const currentStep = steps[step]

  useEffect(() => {
    const measure = () => {
      const element = document.querySelector<HTMLElement>(currentStep.selector)
      const nextRect = element?.getBoundingClientRect() ?? null

      setTargetRect((previous) => {
        if (
          previous &&
          nextRect &&
          previous.left === nextRect.left &&
          previous.top === nextRect.top &&
          previous.width === nextRect.width &&
          previous.height === nextRect.height
        ) {
          return previous
        }

        return nextRect
      })
    }

    let frame = requestAnimationFrame(function trackTarget() {
      measure()
      frame = requestAnimationFrame(trackTarget)
    })

    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)

    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [currentStep.selector])

  // Reported once per *step*, not once per render. `onStepChange` is a fresh closure on
  // every render of the route that owns it, so listing it as a dependency re-fired this
  // effect continuously — and each fire released another narrated milestone, which drained
  // the entire recorded run before the tour reached its third card. The tour is advanced by
  // the user; the side effect of arriving at a step must happen exactly as often.
  const onStepChangeRef = useRef(onStepChange)
  onStepChangeRef.current = onStepChange

  useEffect(() => {
    onStepChangeRef.current?.(step)
  }, [step])

  useEffect(() => {
    if (briefSubmitted && step === 0) {
      setStep(1)
    }
  }, [briefSubmitted, step])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onFinish()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [onFinish])

  /** Advance on the user's click, never on the run's timing. */
  const handleNext = () => {
    if (step === 0 && !briefSubmitted) {
      return
    }

    if (step === steps.length - 1) {
      onFinish()
      return
    }

    setStep((current) => current + 1)
  }

  useEffect(() => {
    const selector = step === 2 ? TRACE_SELECTOR : step === 3 ? '[data-tour="drawer"]' : step === 4 ? '[data-tour="conflict-check-log"]' : null
    if (!selector) {
      return
    }

    document.querySelector(selector)?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      block: 'center',
    })
  }, [step])

  const spotlightStyle = targetRect
    ? {
        left: Math.max(0, targetRect.left - SPOTLIGHT_PADDING),
        top: Math.max(0, targetRect.top - SPOTLIGHT_PADDING),
        width: targetRect.width + SPOTLIGHT_PADDING * 2,
        height: targetRect.height + SPOTLIGHT_PADDING * 2,
        boxShadow: '0 0 0 9999px rgba(0,0,0,0.55)',
      }
    : undefined

  return createPortal(
    <>
      {targetRect ? (
        // The cut-out is a huge outer box-shadow, so moving it moves the dimming over the
        // whole screen. Transitioning it is what turns "Next" from a cut into a pan.
        // Short, because it also has to keep up with the smooth scroll on steps 3-5.
        <div
          className="pointer-events-none fixed z-[60] rounded-lg transition-[left,top,width,height] duration-[240ms] ease-glide motion-reduce:transition-none"
          style={spotlightStyle}
        />
      ) : null}

      <div className="fixed right-lg top-lg z-[70] flex items-center gap-sm rounded-DEFAULT border border-tertiary-container bg-surface-container-high px-sm py-xs">
        <span className="font-label-caps text-label-caps text-tertiary-fixed-dim">WALKTHROUGH</span>
        <button
          aria-label="Close walkthrough"
          className="font-label-caps text-label-caps text-tertiary-fixed-dim transition-colors hover:text-on-surface"
          onClick={onFinish}
          type="button"
        >
          ✕
        </button>
      </div>

      <CoachMark
        body={currentStep.body}
        heading={currentStep.heading}
        nextLabel={currentStep.nextLabel}
        onNext={handleNext}
        onSkip={onFinish}
        nextDisabled={step === 0 && !briefSubmitted}
        step={step}
        targetRect={targetRect}
        total={steps.length}
      />
    </>,
    document.body,
  )
}
