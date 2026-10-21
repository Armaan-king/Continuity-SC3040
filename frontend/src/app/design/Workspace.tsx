import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'

import type { Alternative, ConflictEvent, RepairAction, Slot } from '../lib/types'
import { useDesignSession } from '../hooks/useDesignSession'
import { BomTable } from './BomTable'
import { ChatPanel } from './ChatPanel'
import { ComponentGraph } from './ComponentGraph'
import { ConflictPanel } from './ConflictPanel'
import { Wordmark } from '../shell/Wordmark'


export type DesignSession = ReturnType<typeof useDesignSession>

type TourConflict = ConflictEvent & {
  alternatives: Alternative[]
  repair_action: RepairAction | null
  repair_slot: string | null
  target_slot: string | null
  beat: number
}

type WorkspaceViewProps = {
  projectId?: string
  initialPrompt?: string
  /** Render the panels but start nothing — whoever owns the session drives the run. */
  walkthrough?: boolean
  walkthroughStep?: number
  session: DesignSession
  tourConflict?: TourConflict
  tourConflictOpen?: boolean
  tourConflictSlots?: Slot[]
}

/**
 * The workspace, owning its own session. This is what every route renders except the
 * walkthrough, which needs to drive the same session it is narrating.
 */
export function Workspace(props: Omit<WorkspaceViewProps, 'session'>) {
  const session = useDesignSession()
  return <WorkspaceView {...props} session={session} />
}

/**
 * The panels, given a session rather than making one.
 *
 * Split out so the session can be owned one level up without the hook ever being called
 * conditionally — a default parameter of `session = useDesignSession()` reads well and is
 * invalid, because the hook then runs only when the prop is absent.
 *
 * It matters more than a lint rule here: `sseClient` is a module-level singleton with one
 * abort controller and one listener, so a second live session does not merely waste a
 * hook — its `start` would `reset()` the first one's stream out from under it. Exactly
 * one `useDesignSession` may be alive per screen, and this split is what guarantees it.
 */
export function WorkspaceView({
  projectId,
  initialPrompt,
  walkthrough = false,
  walkthroughStep,
  session,
  tourConflict,
  tourConflictOpen = false,
  tourConflictSlots,
}: WorkspaceViewProps) {
  const [isConflictOpen, setIsConflictOpen] = useState(false)
  const [didAutoOpenConflict, setDidAutoOpenConflict] = useState(false)
  const {
    slots,
    edges,
    supply,
    reasoning,
    inFlightReasoningSeq,
    bom,
    summary,
    question,
    conflict,
    status,
    isHydrated,
    canContinue,
    revealedSlotIds,
    animatedSlotIds,
    activeRepair,
    slotConflictVariant,
    releaseRepairHold,
    start,
    answer,
    continueRun,
    cancel,
  } = session

  const handleStartRef = useRef<(prompt: string) => void>(() => undefined)

  const conflictCount = useMemo(
    () => slots.filter((slot) => slot.status === 'conflict').length,
    [slots],
  )

  const pendingCount = useMemo(
    () => slots.filter((slot) => slot.status === 'pending' || slot.status === 'searching').length,
    [slots],
  )

  /** A finished board can be short a part: nothing is pending, and a slot was never
   *  filled. `pendingCount` comes from an empty queue and reads `0 pending` either way,
   *  which is how a board missing a sensor reported as complete. `done.summary` has
   *  carried both numbers all along. */
  const missingCount =
    summary?.placed === undefined ? 0 : Math.max(summary.slots - summary.placed, 0)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsConflictOpen(false)
      }
    }

    window.addEventListener('keydown', onKeyDown)

    return () => {
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [])

  useEffect(() => {
    if (!conflict || didAutoOpenConflict) {
      return
    }

    setIsConflictOpen(true)
    setDidAutoOpenConflict(true)
  }, [conflict, didAutoOpenConflict])

  useEffect(() => {
    if (tourConflict) {
      setIsConflictOpen(tourConflictOpen)
    }
  }, [tourConflict, tourConflictOpen])

  useEffect(() => {
    if (walkthrough && walkthroughStep !== undefined && walkthroughStep >= 5) {
      setIsConflictOpen(false)
    }
  }, [walkthrough, walkthroughStep])

  const handleStart = useCallback(
    (prompt: string) => {
      setDidAutoOpenConflict(false)
      setIsConflictOpen(false)
      start(prompt, projectId)
    },
    [projectId, start],
  )

  // Kept in a ref so the effect below can depend on `initialPrompt` alone. `handleStart`
  // changes identity whenever the session's `start` does, and depending on it directly
  // would restart the run on those renders.
  handleStartRef.current = handleStart

  /**
   * Start the run a brief screen handed over.
   *
   * Deliberately *not* guarded by an "already started" ref. Unmounting this cancels
   * the stream — `useDesignSession` aborts the request in its own cleanup — and React
   * mounts, cleans up, then mounts again in development. A ref that remembered "already
   * started" would suppress the second start, leaving the run aborted and the panel
   * stuck on RUNNING with an empty trace and no error anywhere. Restarting is the
   * correct response to having just been cancelled.
   */
  useEffect(() => {
    if (walkthrough || !initialPrompt) {
      return
    }

    handleStartRef.current(initialPrompt)
  }, [initialPrompt, walkthrough])

  return (
    <div className="h-full min-h-0 w-full overflow-hidden flex flex-col font-body-md antialiased selection:bg-primary-container selection:text-on-primary-container bg-background text-on-background">
      <header className="flex justify-between items-center w-full px-lg h-12 z-50 bg-surface-container-low dark:bg-surface-container-low border-b border-outline-variant flex-shrink-0 shadow-[0_1px_0_0_rgba(255,255,255,0.05)]">
        <div className="flex items-center gap-container-margin h-full">
          {projectId ? (
            <Link
              className="h-8 w-8 rounded flex items-center justify-center text-on-surface-variant hover:text-primary-container hover:bg-surface-container-high transition-colors"
              to="/projects"
            >
              <span className="material-symbols-outlined text-[20px]">arrow_back</span>
            </Link>
          ) : null}

          <Wordmark />
        </div>

        <div className="hidden md:flex items-center h-full space-x-md">
          <div className="flex items-center bg-surface-container-high rounded px-sm py-1 border border-outline-variant">
            <span className="font-label-caps text-label-caps text-on-surface-variant uppercase mr-sm">
              CONTINUITY-01
            </span>
            <span className="h-3 w-px bg-outline-variant mx-sm"></span>
            <div className="flex items-center gap-xs">
              <span className="w-2 h-2 rounded-pill bg-error animate-pulse"></span>
              <span className="font-body-sm text-body-sm text-error font-medium">{conflictCount} conflict</span>
            </div>
            <span className="text-on-surface-variant mx-1 text-[10px]">•</span>
            <div className="flex items-center gap-xs">
              <span
                className={`w-2 h-2 rounded-pill ${missingCount > 0 ? 'bg-error' : 'border border-outline-variant'}`}
              ></span>
              <span
                className={`font-body-sm text-body-sm ${missingCount > 0 ? 'text-error' : 'text-on-surface-variant'}`}
              >
                {missingCount > 0
                  ? `short ${missingCount} part${missingCount === 1 ? '' : 's'}`
                  : `${pendingCount} pending`}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-sm h-full">
          <button
            className={`h-8 px-md font-body-sm text-body-sm font-bold rounded transition-colors duration-75 flex items-center gap-sm disabled:cursor-not-allowed ${
              conflictCount === 0
                ? 'bg-surface-container-high text-on-surface-variant border border-outline-variant'
                : 'bg-error text-on-error hover:bg-opacity-90 active:scale-95 glow-error'
            }`}
            disabled={conflictCount === 0}
            onClick={() => setIsConflictOpen((value) => !value)}
            type="button"
          >
            <span className="material-symbols-outlined text-[16px]">warning</span>
            Resolve Conflicts ({conflictCount})
          </button>
        </div>
      </header>

      <div className="flex flex-1 min-h-0 overflow-hidden">
        <main className="flex flex-1 min-h-0 p-gutter gap-gutter bg-background overflow-hidden relative">
          <ChatPanel
            conflict={conflict}
            onAnswer={answer}
            canContinue={canContinue}
            onCancel={cancel}
            onContinue={continueRun}
            onOpenConflict={() => setIsConflictOpen(true)}
            question={question}
            reasoning={reasoning}
            inFlightReasoningSeq={inFlightReasoningSeq}
            isHydrated={isHydrated}
            status={status}
            summary={summary}
          />
          <ComponentGraph
            activeRepair={activeRepair}
            animateEdges={!isHydrated}
            conflict={conflict}
            edges={edges}
            onReleaseRepairHold={releaseRepairHold}
            animatedSlotIds={animatedSlotIds}
            revealedSlotIds={revealedSlotIds}
            slotConflictVariant={slotConflictVariant}
            slots={slots}
            supply={supply}
          />
          {isConflictOpen && (tourConflictOpen ? tourConflict : conflict) ? (
            <ConflictPanel
              conflict={tourConflictOpen ? tourConflict ?? null : conflict}
              edges={edges}
              onClose={() => setIsConflictOpen(false)}
              open={isConflictOpen}
              reasoning={reasoning}
              slots={tourConflictOpen ? tourConflictSlots ?? slots : slots}
            />
          ) : (
            <BomTable bom={bom} slots={slots} />
          )}
        </main>
      </div>
    </div>
  )
}
