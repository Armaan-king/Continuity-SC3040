import { useCallback, useEffect, useRef, useState } from 'react'

import type { ThreadBoard } from '../lib/api'
import { sseClient } from '../lib/sseClient'
import type {
  Alternative,
  BomEvent,
  CandidateEvent,
  CheckEvent,
  ConflictEvent,
  DesignEvent,
  DoneEvent,
  Edge,
  EdgePatch,
  ErrorEvent,
  QuestionEvent,
  ReasoningEvent,
  RepairEvent,
  SelectionEvent,
  SessionStatus,
  Slot,
  SupplyNode,
} from '../lib/types'

export type EventSource = {
  speed: 1 | 2 | 4
  start: (prompt: string, onEvent: (event: DesignEvent) => void, projectId?: string, requestId?: string) => void
  startBom: (
    bom: string,
    onEvent: (event: DesignEvent) => void,
    prompt?: string,
    projectId?: string,
    requestId?: string,
  ) => void
  startDemo: (onEvent: (event: DesignEvent) => void) => void
  answer: (text: string, question?: QuestionEvent) => void
  continueRun: (threadId: string, onEvent: (event: DesignEvent) => void) => void
  restore: (threadId: string, onEvent: (event: DesignEvent) => void, lastSeq?: number) => void
  reconnect: (threadId: string, onEvent: (event: DesignEvent) => void) => void
  disconnect: () => void
  cancel: () => Promise<void>
}

const source: EventSource = sseClient

type ReasoningItem = ReasoningEvent | CheckEvent | RepairEvent | ErrorEvent

type SlotConflictVariant = 'entry' | 'repeat' | 'warmup'

type ActiveRepair = Pick<RepairEvent, 'seq' | 'slot' | 'action' | 'rationale'> | null

type SessionConflict = ConflictEvent & {
  alternatives: Alternative[]
  repair_slot: string | null
  repair_action: RepairEvent['action'] | null
  target_slot: string | null
  beat: number
}

type QueuedSlotEvent = CandidateEvent | SelectionEvent

const MIN_SEARCH_VISIBLE_MS = 600
const REPAIR_HOLD_TIMEOUT_MS = 6000

/** Draining can enqueue more work; bounded so a cycle cannot hang the tab. */
const MAX_FLUSH_PASSES = 8

function mergeEdgePatches(previous: Edge[], patches: EdgePatch[] | undefined): Edge[] {
  if (!patches || patches.length === 0) {
    return previous
  }

  const indexById = new Map(previous.map((edge, index) => [edge.id, index]))
  const next = [...previous]

  for (const patch of patches) {
    const index = indexById.get(patch.id)
    if (index === undefined) {
      continue
    }

    next[index] = {
      ...next[index],
      ...patch,
    }
  }

  return next
}

function mergeEdgesById(previous: Edge[], additions: Edge[]): Edge[] {
  const indexById = new Map(previous.map((edge, index) => [edge.id, index]))
  const next = [...previous]

  for (const edge of additions) {
    const index = indexById.get(edge.id)
    if (index === undefined) {
      indexById.set(edge.id, next.length)
      next.push(edge)
      continue
    }

    next[index] = { ...next[index], ...edge }
  }

  return next
}

/**
 * A `plan` event is additive, per the contract: a slot the client already holds keeps its
 * status and its chosen part. `replan` re-announces the whole board to correct the supply
 * node after the user redirects the input, and a wholesale replace would blank every part
 * on screen mid-run.
 */
function mergePlanSlots(previous: Slot[], declared: Slot[]): Slot[] {
  const existingById = new Map(previous.map((slot) => [slot.id, slot]))
  const merged = declared.map((slot) => {
    const existing = existingById.get(slot.id)
    existingById.delete(slot.id)
    return existing
      ? { ...existing, label: slot.label, tier: slot.tier, pinned: slot.pinned }
      : { ...slot, status: 'pending' as const, part: null, constraint: null, repair_count: 0 }
  })

  // Anything the plan no longer names was added mid-run by a repair, not removed by one:
  // the contract has no delete. Keeping them is what stops `slot_added` parts vanishing.
  return [...merged, ...existingById.values()]
}

/** Same rule as the slots: an edge already on screen keeps the status it earned. */
function mergePlanEdges(previous: Edge[], declared: Edge[]): Edge[] {
  const existingById = new Map(previous.map((edge) => [edge.id, edge]))
  return mergeEdgesById(
    previous,
    declared.map((edge) => {
      const existing = existingById.get(edge.id)
      return existing ? { ...edge, status: existing.status, label: edge.label ?? existing.label } : edge
    }),
  )
}

function updateSlotStatus(slots: Slot[], slotId: string, update: (slot: Slot) => Slot): Slot[] {
  return slots.map((slot) => (slot.id === slotId ? update(slot) : slot))
}

function inferConflictSlot(event: ConflictEvent) {
  if (event.subject) return event.subject
  if (event.evidence[0]?.slot) {
    return event.evidence[0].slot
  }

  return event.involved[0] ?? null
}

export function useDesignSession() {
  const [slots, setSlots] = useState<Slot[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [supply, setSupply] = useState<SupplyNode | null>(null)
  const [revealedSlotIds, setRevealedSlotIds] = useState<Set<string>>(() => new Set())
  const [animatedSlotIds, setAnimatedSlotIds] = useState<Set<string>>(() => new Set())
  const [reasoning, setReasoning] = useState<ReasoningItem[]>([])
  const [inFlightReasoningSeq, setInFlightReasoningSeq] = useState<number | null>(null)
  const [bom, setBom] = useState<BomEvent | null>(null)
  const [summary, setSummary] = useState<DoneEvent['summary'] | null>(null)
  const [question, setQuestion] = useState<QuestionEvent | null>(null)
  const [conflict, setConflict] = useState<SessionConflict | null>(null)
  const [status, setStatus] = useState<SessionStatus>('idle')
  const [isHydrated, setIsHydrated] = useState(false)
  const [canContinue, setCanContinue] = useState(false)
  const [activeRepair, setActiveRepair] = useState<ActiveRepair>(null)
  const [slotConflictVariant, setSlotConflictVariant] = useState<Record<string, SlotConflictVariant>>({})
  const [threadId, setThreadId] = useState<string | null>(null)
  const finalBoardRef = useRef(false)
  const answeringRef = useRef(false)
  const cancelRequestedRef = useRef(false)
  const cancelPendingRef = useRef(false)

  const threadRef = useRef<string | null>(null)
  const lastSeqRef = useRef<number>(-1)
  const requestRef = useRef<{ fingerprint: string; id: string; at: number } | null>(null)
  const requestId = useCallback((fingerprint: string) => {
    const previous = requestRef.current
    if (previous?.fingerprint === fingerprint && Date.now() - previous.at < 2000) return previous.id
    const id = crypto.randomUUID()
    requestRef.current = { fingerprint, id, at: Date.now() }
    return id
  }, [])
  const conflictRef = useRef<SessionConflict | null>(null)
  /** Whether this run ever repaired anything. A design run resolves its conflicts and
   *  a lingering `conflict` status at the end is stale presentation state; a BOM
   *  validation *reports* conflicts and repairs nothing, and clearing them would throw
   *  away the entire deliverable. "A repair happened" is what separates the two. */
  const repairedRef = useRef(false)

  const generationRef = useRef(0)
  const heldSlotsRef = useRef(new Set<string>())
  const holdTimerBySlotRef = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const queuedEventsBySlotRef = useRef(new Map<string, QueuedSlotEvent[]>())
  /** Pending presentation-floor work, keyed by its timer so it can be *flushed*
   *  rather than only cancelled. See `flushPresentationFlow`. */
  const deferredSlotTimersRef = useRef(new Map<ReturnType<typeof setTimeout>, () => void>())
  const searchingStartedAtRef = useRef(new Map<string, number>())
  const conflictBeatBySlotRef = useRef(new Map<string, number>())

  const clearHoldTimer = useCallback((slot: string) => {
    const timer = holdTimerBySlotRef.current.get(slot)
    if (!timer) {
      return
    }

    clearTimeout(timer)
    holdTimerBySlotRef.current.delete(slot)
  }, [])

  const clearDeferredTimers = useCallback(() => {
    for (const timer of deferredSlotTimersRef.current.keys()) {
      clearTimeout(timer)
    }
    deferredSlotTimersRef.current.clear()
  }, [])

  const disposePresentationFlow = useCallback(() => {
    generationRef.current += 1

    for (const timer of holdTimerBySlotRef.current.values()) {
      clearTimeout(timer)
    }

    holdTimerBySlotRef.current.clear()
    heldSlotsRef.current.clear()
    queuedEventsBySlotRef.current.clear()
    searchingStartedAtRef.current.clear()
    conflictBeatBySlotRef.current.clear()
    clearDeferredTimers()

    setActiveRepair(null)
    setSlotConflictVariant({})
  }, [clearDeferredTimers])

  const runWithSearchingFloor = useCallback((slotId: string, apply: () => void) => {
    const startedAt = searchingStartedAtRef.current.get(slotId)

    if (startedAt === undefined) {
      apply()
      return
    }

    const elapsed = Date.now() - startedAt
    const remaining = MIN_SEARCH_VISIBLE_MS - elapsed

    if (remaining <= 0) {
      apply()
      return
    }

    const generation = generationRef.current
    const timer = setTimeout(() => {
      deferredSlotTimersRef.current.delete(timer)
      if (generationRef.current !== generation) {
        return
      }
      apply()
    }, remaining)

    deferredSlotTimersRef.current.set(timer, apply)
  }, [])

  const revealSlot = useCallback((slotId: string, animate = true) => {
    setRevealedSlotIds((previous) => {
      if (previous.has(slotId)) {
        return previous
      }

      return new Set(previous).add(slotId)
    })

    if (!animate) {
      return
    }

    setAnimatedSlotIds((previous) => {
      if (previous.has(slotId)) {
        return previous
      }

      return new Set(previous).add(slotId)
    })
  }, [])

  const applyCandidateEvent = useCallback((event: CandidateEvent) => {
    revealSlot(event.slot)
    searchingStartedAtRef.current.set(event.slot, Date.now())

    setSlots((previous) =>
      updateSlotStatus(previous, event.slot, (slot) => ({
        ...slot,
        status: 'searching',
        part: event.part,
      })),
    )

    setActiveRepair((previous) => (previous?.slot === event.slot ? null : previous))
  }, [revealSlot])

  const applySelectionEvent = useCallback(
    (event: SelectionEvent) => {
      revealSlot(event.slot)

      runWithSearchingFloor(event.slot, () => {
        searchingStartedAtRef.current.delete(event.slot)

        setSlots((previous) =>
          updateSlotStatus(previous, event.slot, (slot) => ({
            ...slot,
            status: event.status,
            part: event.part,
          })),
        )
        setEdges((previous) => mergeEdgePatches(previous, event.edges))
        setConflict((previous) => {
          if (!previous) {
            return previous
          }

          return previous.repair_slot === event.slot ? null : previous
        })
        setActiveRepair((previous) => (previous?.slot === event.slot ? null : previous))
      })
    },
    [revealSlot, runWithSearchingFloor],
  )

  const releaseRepairHold = useCallback(
    (slotId?: string) => {
      const targets = slotId ? [slotId] : Array.from(heldSlotsRef.current)

      for (const slot of targets) {
        clearHoldTimer(slot)
        heldSlotsRef.current.delete(slot)

        const queue = queuedEventsBySlotRef.current.get(slot)
        if (!queue || queue.length === 0) {
          queuedEventsBySlotRef.current.delete(slot)
          continue
        }

        queuedEventsBySlotRef.current.delete(slot)

        for (const queued of queue.sort((a, b) => a.seq - b.seq)) {
          if (queued.type === 'candidate') {
            applyCandidateEvent(queued)
            continue
          }

          applySelectionEvent(queued)
        }
      }
    },
    [applyCandidateEvent, applySelectionEvent, clearHoldTimer],
  )

  const acquireRepairHold = useCallback(
    (slotId: string) => {
      heldSlotsRef.current.add(slotId)
      clearHoldTimer(slotId)

      const generation = generationRef.current
      const timer = setTimeout(() => {
        if (generationRef.current !== generation) {
          return
        }

        releaseRepairHold(slotId)
      }, REPAIR_HOLD_TIMEOUT_MS)

      holdTimerBySlotRef.current.set(slotId, timer)
    },
    [clearHoldTimer, releaseRepairHold],
  )

  /**
   * Land every piece of outstanding presentation work immediately.
   *
   * The pacing floor defers a slot's `selection` for up to MIN_SEARCH_VISIBLE_MS so a
   * search is visible rather than a flicker. That was safe while the only event source
   * was the mock, whose run took ~32s — every deferral had long since fired by the time
   * `done` arrived. A real backend finishes in a fraction of that, so `done` now lands
   * while deferrals are still pending, and disposing them silently would leave the graph
   * showing parts that were superseded seconds earlier while the BOM shows the real ones.
   *
   * Releasing a repair hold can enqueue further deferred work, so this drains in passes.
   */
  const flushPresentationFlow = useCallback(() => {
    releaseRepairHold()

    for (let pass = 0; pass < MAX_FLUSH_PASSES; pass += 1) {
      const pending = Array.from(deferredSlotTimersRef.current.entries())
      if (pending.length === 0) {
        return
      }

      deferredSlotTimersRef.current.clear()
      for (const [timer, apply] of pending) {
        clearTimeout(timer)
        apply()
      }
    }
  }, [releaseRepairHold])

  const queueHeldEvent = useCallback((event: QueuedSlotEvent) => {
    const queue = queuedEventsBySlotRef.current.get(event.slot) ?? []
    queue.push(event)
    queuedEventsBySlotRef.current.set(event.slot, queue)
  }, [])

  const resetSessionState = useCallback(() => {
    disposePresentationFlow()
    setSlots([])
    setEdges([])
    setSupply(null)
    setRevealedSlotIds(new Set())
    setAnimatedSlotIds(new Set())
    setReasoning([])
    setInFlightReasoningSeq(null)
    setSummary(null)
    setThreadId(null)
    finalBoardRef.current = false
    answeringRef.current = false
    cancelRequestedRef.current = false
    repairedRef.current = false
    setBom(null)
    setQuestion(null)
    setConflict(null)
    setIsHydrated(false)
    setCanContinue(false)
    conflictRef.current = null
    threadRef.current = null
    lastSeqRef.current = -1
  }, [disposePresentationFlow])

  const handleEvent = useCallback(
    (event: DesignEvent) => {
      if (cancelRequestedRef.current) return
      if (event.type === 'error' && event.seq < 0) {
        answeringRef.current = false
        setReasoning((previous) => [...previous, event])
        setQuestion(event.retry_question ?? null)
        setStatus(event.retry_question ? 'paused' : 'error')
        setCanContinue(!event.retry_question && event.recoverable && threadRef.current !== null)
        flushPresentationFlow()
        disposePresentationFlow()
        return
      }
      if (threadRef.current === null) {
        threadRef.current = event.thread_id
        setThreadId(event.thread_id)
      }

      if (event.thread_id !== threadRef.current) {
        return
      }

      if (event.seq <= lastSeqRef.current) {
        return
      }

      lastSeqRef.current = event.seq
      answeringRef.current = false

      // Replaying a resumed run can include a question that was already answered.
      // A later event supersedes that historical pause.
      if (event.type !== 'question') {
        setQuestion(null)
        setStatus((current) => current === 'paused' ? 'running' : current)
      }

      if (event.type !== 'reasoning') {
        setInFlightReasoningSeq(null)
      }

      switch (event.type) {
        case 'board': {
          flushPresentationFlow()
          disposePresentationFlow()
          finalBoardRef.current = true
          setSlots(event.slots)
          setEdges(event.edges)
          setSupply(event.supply)
          setRevealedSlotIds(new Set(event.slots.map((slot) => slot.id)))
          const detail = event.conflicts[0]
          setConflict(detail ? {
            ...detail, type: 'conflict', seq: event.seq, thread_id: event.thread_id,
            alternatives: [], repair_slot: null, repair_action: null,
            target_slot: detail.subject ?? detail.involved[0] ?? null, beat: 1,
          } : null)
          return
        }
        case 'plan': {
          setSlots((previous) => mergePlanSlots(previous, event.slots))
          setEdges((previous) => mergePlanEdges(previous, event.edges))
          if (event.supply) {
            setSupply(event.supply)
          }
          setStatus('running')
          return
        }

        case 'slot_added': {
          setSlots((previous) => {
            if (previous.some((slot) => slot.id === event.slot.id)) {
              return previous
            }

            return [
              ...previous,
              {
                ...event.slot,
                status: 'pending',
                part: null,
                constraint: null,
                repair_count: 0,
              },
            ]
          })
          setEdges((previous) => mergeEdgesById(previous, event.edges))
          return
        }

        case 'reasoning': {
          setReasoning((previous) => [...previous, event])
          setInFlightReasoningSeq(event.seq)
          return
        }

        case 'candidate': {
          if (heldSlotsRef.current.has(event.slot)) {
            queueHeldEvent(event)
            return
          }

          applyCandidateEvent(event)
          return
        }

        case 'check': {
          revealSlot(event.slot)
          setReasoning((previous) => [...previous, event])
          return
        }

        case 'conflict': {
          const targetSlot = inferConflictSlot(event)
          const affectedSlots = new Set(event.involved)
          if (targetSlot) {
            affectedSlots.add(targetSlot)
          }
          affectedSlots.forEach((slotId) => revealSlot(slotId))
          const previousBeat = targetSlot ? conflictBeatBySlotRef.current.get(targetSlot) ?? 0 : 0
          const beat = targetSlot ? previousBeat + 1 : 1

          if (targetSlot) {
            conflictBeatBySlotRef.current.set(targetSlot, beat)
          }

          setConflict({
            ...event,
            alternatives: [],
            repair_slot: null,
            repair_action: null,
            target_slot: targetSlot,
            beat,
          })
          setEdges((previous) => mergeEdgePatches(previous, event.edges))

          if (targetSlot) {
            const variant: SlotConflictVariant =
              event.rule === 'availability' ? 'warmup' : beat > 1 ? 'repeat' : 'entry'
            setSlotConflictVariant((previous) => ({
              ...previous,
              [targetSlot]: variant,
            }))

            runWithSearchingFloor(targetSlot, () => {
              searchingStartedAtRef.current.delete(targetSlot)
              setSlots((previous) =>
                updateSlotStatus(previous, targetSlot, (slot) => ({
                  ...slot,
                  status: 'conflict',
                })),
              )
            })
          }
          return
        }

        case 'repair': {
          revealSlot(event.slot)
          repairedRef.current = true
          setReasoning((previous) => [...previous, event])
          setConflict((previous) => {
            if (!previous) {
              return previous
            }

            if (!previous.involved.includes(event.slot)) {
              return previous
            }

            return {
              ...previous,
              alternatives: event.alternatives,
              repair_slot: event.slot,
              repair_action: event.action,
            }
          })
          setSlots((previous) =>
            updateSlotStatus(previous, event.slot, (slot) => ({
              ...slot,
              constraint: event.constraint,
              repair_count: slot.repair_count + 1,
              status: 'conflict',
            })),
          )
          setActiveRepair({
            seq: event.seq,
            slot: event.slot,
            action: event.action,
            rationale: event.rationale,
          })
          acquireRepairHold(event.slot)
          return
        }

        case 'selection': {
          if (heldSlotsRef.current.has(event.slot)) {
            queueHeldEvent(event)
            return
          }

          applySelectionEvent(event)
          return
        }

        case 'question': {
          setQuestion(event)
          setStatus('paused')
          return
        }

        case 'bom': {
          setBom(event)
          return
        }

        case 'done': {
          // `placed` exists so the summary can disagree with itself when a board is short
          // a part. It has been on the wire since the vanishing-slot fix and nothing read
          // it, so a board missing a slot still reported "0 pending".
          setSummary(event.summary)
          if (finalBoardRef.current) {
            setQuestion(null)
            setStatus('done')
            setCanContinue(false)
            return
          }
          const activeConflict = conflictRef.current
          const keepEscalatedConflict = activeConflict?.repair_action === 'escalate'

          setQuestion(null)
          setStatus('done')
          setConflict(keepEscalatedConflict || !repairedRef.current ? activeConflict : null)
          setSlots((previous) =>
            previous.map((slot) => {
              if (slot.status !== 'conflict') {
                return slot
              }

              if (keepEscalatedConflict && activeConflict?.repair_slot === slot.id) {
                return slot
              }

              // Nothing was repaired, so nothing superseded this conflict — it is the
              // finding, not a leftover. A BOM validation ends here every time, and
              // clearing these turned two real temperature failures into "0 conflict".
              if (!repairedRef.current) {
                return slot
              }

              return {
                ...slot,
                status: slot.part ? 'pass' : 'pending',
              }
            }),
          )
          flushPresentationFlow()
          disposePresentationFlow()
          return
        }

        case 'error': {
          // The event has always carried a `message` and it used to be dropped here, so a
          // run that died showed one word and no cause. A 422 from `/design` and a
          // distributor timeout looked identical, and the only way to tell them apart was
          // the server log.
          setReasoning((previous) => [...previous, event])
          setStatus('error')
          setCanContinue(event.recoverable)
          flushPresentationFlow()
          disposePresentationFlow()
        }
      }
    },
    [
      acquireRepairHold,
      applyCandidateEvent,
      applySelectionEvent,
      disposePresentationFlow,
      flushPresentationFlow,
      queueHeldEvent,
      revealSlot,
      runWithSearchingFloor,
    ],
  )

  const start = useCallback(
    (prompt: string, projectId?: string) => {
      resetSessionState()
      setStatus('running')
      source.start(prompt, handleEvent, projectId, requestId(`design:${projectId}:${prompt}`))
    },
    [handleEvent, resetSessionState, requestId],
  )

  const startBom = useCallback(
    (bom: string, prompt?: string, projectId?: string) => {
      resetSessionState()
      setStatus('running')
      source.startBom(bom, handleEvent, prompt, projectId, requestId(`bom:${projectId}:${prompt}:${bom}`))
    },
    [handleEvent, resetSessionState, requestId],
  )

  const startWalkthrough = useCallback(() => {
    resetSessionState()
    setStatus('running')
    source.startDemo(handleEvent)
  }, [handleEvent, resetSessionState])

  const hydrate = useCallback(
    (board: ThreadBoard, threadId: string) => {
      resetSessionState()
      threadRef.current = threadId
      setThreadId(threadId)
      lastSeqRef.current = board.trace.reduce((lastSeq, event) => Math.max(lastSeq, event.seq), -1)
      source.restore(threadId, handleEvent, lastSeqRef.current)
      setSlots(board.slots)
      setEdges(board.edges)
      setSupply(board.supply ?? null)
      const detail = board.conflicts?.[0] ?? [...board.trace].reverse().find(
        (event): event is ConflictEvent => event.type === 'conflict' && board.slots.some(
          (slot) => slot.status === 'conflict' && slot.id === inferConflictSlot(event),
        ),
      )
      if (detail) setConflict({
        ...detail, type: 'conflict', seq: lastSeqRef.current, thread_id: threadId,
        alternatives: [], repair_slot: null, repair_action: null,
        target_slot: detail.subject ?? detail.involved[0] ?? null, beat: 1,
      })
      // Only what the run actually reached. Revealing every declared slot is right for a
      // *finished* board, and wrong for one that stopped part-way: continuing an
      // interrupted run redrew all five empty frames at once, which is exactly the
      // scaffolding the progressive reveal exists to avoid. The rule matches the `plan`
      // handler's — a slot is visible once it has a part or has been settled by a check.
      setRevealedSlotIds(
        new Set(
          board.slots
            .filter((slot) => board.status === 'done' || slot.part !== null || slot.status === 'pass' || slot.status === 'conflict')
            .map((slot) => slot.id),
        ),
      )
      setAnimatedSlotIds(new Set())
      setReasoning(
        board.trace.filter(
          (event): event is ReasoningItem =>
            event.type === 'reasoning' ||
            event.type === 'check' ||
            event.type === 'repair' ||
            event.type === 'error',
        ),
      )
      setInFlightReasoningSeq(null)
      setBom(
        board.bom === null
          ? null
          : {
              type: 'bom',
              seq: -1,
              thread_id: '',
              ...board.bom,
            },
      )
      setSummary(board.summary)
      setQuestion(board.question)
      setStatus(
        board.status === 'done'
          ? 'done'
          : board.status === 'awaiting'
            ? 'paused'
            : 'error',
      )
      setCanContinue(board.resumable && (board.status === 'abandoned' || board.status === 'error'))
      setIsHydrated(true)
    },
    [handleEvent, resetSessionState],
  )

  const answer = useCallback(
    (text: string) => {
      if (status !== 'paused' || !question || !text.trim() || answeringRef.current) return
      answeringRef.current = true
      setStatus('running')
      setQuestion(null)
      source.answer(text, question)
    },
    [status, question],
  )

  const reconnect = useCallback((threadId: string) => {
    resetSessionState()
    threadRef.current = threadId
    setThreadId(threadId)
    setStatus('running')
    source.reconnect(threadId, handleEvent)
  }, [handleEvent, resetSessionState])

  const cancel = useCallback(async () => {
    if (cancelPendingRef.current) return false
    cancelPendingRef.current = true
    cancelRequestedRef.current = true
    disposePresentationFlow()
    const generation = generationRef.current
    setStatus('stopping')
    setCanContinue(false)
    try {
      await source.cancel()
      if (generation === generationRef.current) {
        setQuestion(null)
        setActiveRepair(null)
        setStatus('stopped')
      }
      return true
    } catch (error) {
      if (generation === generationRef.current) {
        setStatus('stop_failed')
        setReasoning((previous) => [...previous, {
          type: 'error', seq: -1, thread_id: threadRef.current ?? '', recoverable: false,
          message: `Stop was not confirmed; the run may still be active. ${error instanceof Error ? error.message : 'Check your connection and retry Stop.'}`,
        }])
      }
      return false
    } finally {
      cancelPendingRef.current = false
    }
  }, [disposePresentationFlow])

  const continueRun = useCallback(() => {
    const threadId = threadRef.current
    if (!threadId || !canContinue) {
      return
    }
    setCanContinue(false)
    setStatus('running')
    source.continueRun(threadId, handleEvent)
  }, [canContinue, handleEvent])

  useEffect(() => {
    conflictRef.current = conflict
  }, [conflict])

  useEffect(() => {
    return () => {
      disposePresentationFlow()
      source.disconnect()
    }
  }, [disposePresentationFlow])

  return {
    threadId,
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
    acquireRepairHold,
    releaseRepairHold,
    start,
    startBom,
    startWalkthrough,
    hydrate,
    answer,
    continueRun,
    reconnect,
    cancel,
  }
}
