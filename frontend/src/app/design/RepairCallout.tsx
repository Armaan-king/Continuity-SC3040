import { useEffect, useMemo, useState } from 'react'

import type { RepairAction } from '../lib/types'

type ActiveRepair = {
  seq: number
  slot: string
  action: RepairAction
  rationale: string
} | null

type RepairCalloutProps = {
  repair: ActiveRepair
  onRelease: (slot: string) => void
}

const TYPE_MS = 18
const HOLD_MS = 2000

function usePrefersReducedMotion() {
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false)

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setPrefersReducedMotion(mediaQuery.matches)

    update()
    mediaQuery.addEventListener('change', update)

    return () => {
      mediaQuery.removeEventListener('change', update)
    }
  }, [])

  return prefersReducedMotion
}

function actionLabel(action: RepairAction) {
  return action
    .split('_')
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join(' ')
}

export function RepairCallout({ repair, onRelease }: RepairCalloutProps) {
  const [visibleText, setVisibleText] = useState('')
  const prefersReducedMotion = usePrefersReducedMotion()

  const actionText = useMemo(() => {
    if (!repair) {
      return ''
    }

    return actionLabel(repair.action)
  }, [repair])

  useEffect(() => {
    if (!repair) {
      setVisibleText('')
      return
    }

    let holdTimer: ReturnType<typeof setTimeout> | null = null
    let typingTimer: ReturnType<typeof setInterval> | null = null
    let released = false

    const safeRelease = () => {
      if (released) {
        return
      }
      released = true
      onRelease(repair.slot)
    }

    try {
      if (prefersReducedMotion) {
        setVisibleText(repair.rationale)
        holdTimer = setTimeout(safeRelease, HOLD_MS)
      } else {
        setVisibleText('')
        let cursor = 0

        typingTimer = setInterval(() => {
          cursor += 1
          setVisibleText(repair.rationale.slice(0, cursor))

          if (cursor >= repair.rationale.length) {
            if (typingTimer) {
              clearInterval(typingTimer)
              typingTimer = null
            }

            holdTimer = setTimeout(safeRelease, HOLD_MS)
          }
        }, TYPE_MS)
      }
    } catch {
      safeRelease()
    }

    return () => {
      if (typingTimer) {
        clearInterval(typingTimer)
      }
      if (holdTimer) {
        clearTimeout(holdTimer)
      }
      safeRelease()
    }
  }, [onRelease, prefersReducedMotion, repair])

  if (!repair) {
    return null
  }

  return (
    <div className="border-b border-error/30 bg-[#16181D] px-md py-sm">
      <div className="flex items-center gap-sm">
        <span className="material-symbols-outlined text-[15px] text-tertiary-container">build</span>
        <span className="font-label-caps text-label-caps text-tertiary-container uppercase-label">Repair Rationale</span>
        <span className="text-on-surface-variant text-[10px]">•</span>
        <span className="font-data-tabular text-[10px] text-on-surface-variant">{actionText}</span>
      </div>
      <p className="mt-1 font-data-tabular text-[12px] text-on-surface leading-relaxed">{visibleText}</p>
    </div>
  )
}
