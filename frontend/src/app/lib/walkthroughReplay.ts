import type { CandidateEvent, ConflictEvent, DesignEvent, PlanEvent, Slot } from './types'

type ReplayFrame = { type: string; [key: string]: unknown }

const frames = __WALKTHROUGH_REPLAY__
  .split('\n')
  .filter(Boolean)
  .map((line) => JSON.parse(line) as ReplayFrame)

const prompt = frames.find((frame) => frame.type === 'prompt')

if (typeof prompt?.text !== 'string') {
  throw new Error('The walkthrough recording has no prompt.')
}

export const walkthroughBrief = prompt.text

/**
 * The complete recorded run is bundled by Vite, so consumers can replay it without
 * making a request to the design service. The JSONL predates wire sequence ids; they
 * are presentation metadata and are not needed by a local replay.
 */
export const walkthroughReplayEvents = frames.filter(
  (frame) => frame.type !== 'prompt',
) as unknown as DesignEvent[]

export function walkthroughConflictSnapshot() {
  const conflictIndex = frames.findIndex(
    (frame) => frame.type === 'conflict' && frame.rule === 'availability',
  )
  const conflict = frames[conflictIndex] as unknown as ConflictEvent | undefined
  const plan = frames.find((frame) => frame.type === 'plan') as unknown as PlanEvent | undefined

  if (!conflict || !plan) {
    throw new Error('The walkthrough recording has no availability conflict.')
  }

  const candidates = new Map<string, CandidateEvent>()
  for (const frame of frames.slice(0, conflictIndex)) {
    if (frame.type === 'candidate') {
      const candidate = frame as unknown as CandidateEvent
      candidates.set(candidate.slot, candidate)
    }
  }

  const slots: Slot[] = conflict.involved.flatMap((slotId) => {
    const slot = plan.slots.find((candidate) => candidate.id === slotId)
    if (!slot) {
      return []
    }

    return [{ ...slot, status: 'conflict' as const, part: candidates.get(slotId)?.part ?? null, constraint: null, repair_count: 0 }]
  })

  return {
    conflict: {
      ...conflict,
      alternatives: [],
      repair_slot: null,
      repair_action: null,
      target_slot: conflict.evidence[0]?.slot ?? conflict.involved[0] ?? null,
      beat: 1,
    },
    slots,
  }
}
