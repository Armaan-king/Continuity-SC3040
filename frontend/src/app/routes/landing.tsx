import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'

import { ComponentGraph } from '../design/ComponentGraph'
import { LandingMemoryGraph } from '../design/LandingMemoryGraph'
import { walkthroughReplayEvents } from '../lib/walkthroughReplay'
import type { DesignEvent, Edge, Slot, SupplyNode } from '../lib/types'
import { Wordmark } from '../shell/Wordmark'
import { useAuth } from '../hooks/useAuth'

type ReplayConflict = Extract<DesignEvent, { type: 'conflict' }> & {
  alternatives: []
  repair_slot: string | null
  repair_action: null
  target_slot: string | null
  beat: number
}

type ReplayState = {
  slots: Slot[]
  edges: Edge[]
  revealedSlotIds: Set<string>
  animatedSlotIds: Set<string>
  conflict: ReplayConflict | null
  lastVerdict: string | null
  supply: SupplyNode | null
}

const initialReplayState: ReplayState = {
  slots: [],
  edges: [],
  revealedSlotIds: new Set(),
  animatedSlotIds: new Set(),
  conflict: null,
  lastVerdict: null,
  supply: null,
}

const replayFrames = walkthroughReplayEvents.filter(
  (event) => event.type === 'plan' || event.type === 'candidate' || event.type === 'selection' || event.type === 'conflict' || event.type === 'repair' || event.type === 'done',
)

function mergeEdges(previous: Edge[], patches: Array<Partial<Edge> & Pick<Edge, 'id'>>) {
  const byId = new Map(previous.map((edge) => [edge.id, edge]))
  patches.forEach((patch) => {
    const current = byId.get(patch.id)
    if (current) byId.set(patch.id, { ...current, ...patch })
  })
  return previous.map((edge) => byId.get(edge.id) ?? edge)
}

function applyReplayFrame(state: ReplayState, event: DesignEvent): ReplayState {
  if (event.type === 'plan') return { ...initialReplayState, slots: event.slots, edges: event.edges, supply: event.supply ?? null }
  if (event.type === 'candidate') {
    return {
      ...state,
      slots: state.slots.map((slot) => slot.id === event.slot ? { ...slot, part: event.part, status: 'searching' } : slot),
      revealedSlotIds: new Set(state.revealedSlotIds).add(event.slot),
      animatedSlotIds: new Set(state.animatedSlotIds).add(event.slot),
    }
  }
  if (event.type === 'selection') {
    return {
      ...state,
      slots: state.slots.map((slot) => slot.id === event.slot ? { ...slot, part: event.part, status: 'pass' } : slot),
      edges: mergeEdges(state.edges, event.edges ?? []),
      conflict: null,
    }
  }
  if (event.type === 'conflict') {
    const targetSlot = event.evidence[0]?.slot ?? event.involved[0] ?? null
    const revealedSlotIds = new Set(state.revealedSlotIds)
    event.involved.forEach((slotId) => revealedSlotIds.add(slotId))
    if (targetSlot) revealedSlotIds.add(targetSlot)
    return {
      ...state,
      slots: state.slots.map((slot) => slot.id === targetSlot ? { ...slot, status: 'conflict' } : slot),
      edges: mergeEdges(state.edges, event.edges ?? []),
      revealedSlotIds,
      conflict: { ...event, alternatives: [], repair_slot: null, repair_action: null, target_slot: targetSlot, beat: 1 },
      lastVerdict: event.message,
    }
  }
  return event.type === 'repair' ? { ...state, lastVerdict: event.rationale } : state
}

function finishedReplayState() {
  return replayFrames.reduce(applyReplayFrame, initialReplayState)
}

/** Time between beats. The two constants here and `DWELL_AFTER` are the whole pacing:
 *  at the flat 420 ms this replaced, all twenty frames were gone in eight seconds. */
const BEAT_MS = 800

/** Extra time held *after* a frame, before the next one starts.
 *
 *  The beats are not equally interesting. A part landing on the graph reads at a glance;
 *  a conflict and its repair both rewrite the caption underneath with a sentence of real
 *  reasoning, and at one flat interval those sentences were replaced before they could be
 *  read. The plan gets a moment too, because the whole board appears at once there. */
const DWELL_AFTER: Partial<Record<DesignEvent['type'], number>> = {
  plan: 600,
  conflict: 1500,
  repair: 1500,
}

/** Cumulative start time per frame, so a dwell pushes everything after it along. */
const FRAME_START = replayFrames.reduce<number[]>((starts, _frame, index) => {
  if (index === 0) return [0]
  const previous = replayFrames[index - 1]
  starts.push(starts[index - 1] + BEAT_MS + (DWELL_AFTER[previous.type] ?? 0))
  return starts
}, [])

const REPLAY_RESTART_MS = 4_000

function useHeroReplay() {
  const [state, setState] = useState<ReplayState>(initialReplayState)
  const [finished, setFinished] = useState(false)
  const timers = useRef<number[]>([])

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    let visible = document.visibilityState === 'visible'
    let reducedMotion = media.matches
    let nextFrame = 0

    const clearTimers = () => {
      timers.current.forEach(window.clearTimeout)
      timers.current = []
    }
    const start = (from = 0) => {
      clearTimers()
      if (reducedMotion || !visible) return
      if (from === 0) setState(initialReplayState)
      setFinished(false)
      replayFrames.slice(from).forEach((frame, offset) => {
        const index = from + offset
        timers.current.push(window.setTimeout(() => {
          setState((current) => applyReplayFrame(current, frame))
          nextFrame = index + 1
          if (index === replayFrames.length - 1) {
            setFinished(true)
            nextFrame = 0
            timers.current.push(window.setTimeout(() => start(), REPLAY_RESTART_MS))
          }
          // Relative to where this run picked up, so resuming after a hidden tab does not
          // fire every remaining frame at once.
        }, FRAME_START[index] - FRAME_START[from]))
      })
    }
    const onVisibilityChange = () => {
      visible = document.visibilityState === 'visible'
      if (!visible) {
        clearTimers()
      } else {
        start(nextFrame >= replayFrames.length ? 0 : nextFrame)
      }
    }
    const onMotionChange = (event: MediaQueryListEvent) => {
      reducedMotion = event.matches
      clearTimers()
      if (reducedMotion) {
        setState(finishedReplayState())
        setFinished(true)
      } else {
        nextFrame = 0
        start()
      }
    }

    if (reducedMotion) {
      setState(finishedReplayState())
      setFinished(true)
    } else {
      start()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    media.addEventListener('change', onMotionChange)
    return () => {
      clearTimers()
      document.removeEventListener('visibilitychange', onVisibilityChange)
      media.removeEventListener('change', onMotionChange)
    }
  }, [])

  return { state, finished }
}

function Reveal({ children, className = '' }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [reducedMotion, setReducedMotion] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )
  const [shown, setShown] = useState(reducedMotion)

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const updateMotion = () => setReducedMotion(media.matches)
    updateMotion()
    media.addEventListener('change', updateMotion)
    return () => media.removeEventListener('change', updateMotion)
  }, [])
  useEffect(() => {
    if (reducedMotion || !ref.current || !('IntersectionObserver' in window)) {
      setShown(true)
      return undefined
    }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) {
        setShown(true)
        observer.disconnect()
      }
    }, { threshold: 0.14 })
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [reducedMotion])

  return <div className={`${className} transition-[opacity,transform] duration-[400ms] ease-out ${shown ? 'translate-y-0 opacity-100' : 'translate-y-2.5 opacity-0'}`} ref={ref}>{children}</div>
}

// The published walkthrough: youtu.be/bX3DZ_12Y54. Embedded via youtube-nocookie, so a
// visitor who never presses play is not handed a tracking cookie by the landing page.
const WALKTHROUGH_VIDEO_ID = 'bX3DZ_12Y54'

export default function LandingRoute() {
  const navigate = useNavigate()
  const { user, signOut } = useAuth()
  const [email, setEmail] = useState('')
  const [scrolled, setScrolled] = useState(false)
  const replay = useHeroReplay()

  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 8)
    update()
    window.addEventListener('scroll', update, { passive: true })
    return () => window.removeEventListener('scroll', update)
  }, [])

  const submitEmail = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    navigate('/signup', { state: { email } })
  }

  return (
    <div className="min-h-screen bg-transparent font-body-md text-on-background antialiased">
      <header className={`sticky top-0 z-50 transition-[background-color,border-color,backdrop-filter] duration-300 ${scrolled ? 'border-b border-outline-variant/70 bg-background/80 backdrop-blur-md' : 'bg-background'}`}>
        <div className="mx-auto flex w-full max-w-7xl items-center justify-between px-lg py-md">
          <Wordmark />
          <div className="flex items-center gap-md">
            {/* Every CTA on this page goes to /signup, which sends an already-signed-in
                visitor straight through to their projects. That is right for a visitor and
                baffling while you are testing the signed-out page, because nothing on
                screen said you were signed in. Now it does, and one click undoes it. */}
            {user ? (
              <div className="hidden items-center gap-sm font-data-tabular text-[11px] text-on-surface-variant sm:flex">
                <span className="max-w-[200px] truncate" title={user.email}>{user.email}</span>
                <span className="text-outline">·</span>
                <button
                  className="font-label-caps text-label-caps text-primary-container transition-colors hover:text-primary-fixed"
                  onClick={() => { signOut().catch(() => undefined) }}
                  type="button"
                >
                  SIGN_OUT
                </button>
              </div>
            ) : null}
            <Link className="bg-primary-container px-lg py-sm font-label-caps text-label-caps text-on-primary-fixed hover:bg-primary-fixed" to="/signup">GET_STARTED</Link>
          </div>
        </div>
      </header>

      <main>
        <Reveal>
          <section className="mx-auto grid w-full max-w-7xl grid-cols-1 gap-xl px-lg py-[104px] lg:grid-cols-[minmax(0,0.9fr)_minmax(460px,1.1fr)] lg:items-center lg:gap-[48px] lg:py-[148px]">
            <div>
              <div className="mb-lg inline-flex bg-surface-container-high px-sm py-xs font-data-tabular text-data-tabular text-surface-tint">SYS.STATUS: ONLINE</div>
              <h1 className="max-w-xl font-display-mono text-[44px] font-bold leading-[1.02] tracking-[-0.045em] text-on-surface sm:text-[58px] lg:text-[64px]">Find out your regulator overheats before you order 5,000 boards.</h1>
              <p className="mt-lg max-w-lg text-[18px] leading-7 text-on-surface-variant">Describe the board in plain language. Continuity sources real JLCPCB parts, checks the whole board — voltage, current, thermal, interfaces, pin budget, stock — and repairs what fails.</p>
              <form className="mt-xl flex max-w-md flex-col gap-sm sm:flex-row" onSubmit={submitEmail}>
                <div className="relative min-w-0 flex-1"><input className="h-full w-full border border-outline-variant bg-surface-container-lowest px-md py-md pr-[52px] font-data-tabular text-data-tabular text-on-background placeholder:text-outline focus:border-primary-container focus:outline-none" onChange={(event) => setEmail(event.target.value)} placeholder="ENTER_WORK_EMAIL" type="email" value={email} /><span className="pointer-events-none absolute right-md top-1/2 -translate-y-1/2 font-data-tabular text-data-tabular text-outline">.com</span></div>
                <button className="bg-primary-container px-xl py-md font-label-caps text-label-caps text-on-primary-fixed hover:bg-primary-fixed" type="submit">GET_STARTED</button>
              </form>
            </div>
            <div className="min-w-0">
              <div className="bg-surface-container p-sm">
                {/* Sized near the graph's own 900×820 aspect. The svg fits with `meet`
                    now rather than stretching, so a box much wider than it is tall would
                    letterbox the board into a narrow strip down the middle. */}
                <div className="flex h-[420px] flex-col overflow-hidden sm:h-[500px] lg:h-[560px]"><ComponentGraph activeRepair={null} animateEdges={!replay.finished} animatedSlotIds={replay.state.animatedSlotIds} conflict={replay.state.conflict} edges={replay.state.edges} onReleaseRepairHold={() => undefined} revealedSlotIds={replay.state.revealedSlotIds} slotConflictVariant={{}} slots={replay.state.slots} supply={replay.state.supply} /></div>
                <p className="mt-sm min-h-10 font-data-tabular text-[10px] leading-4 text-on-surface-variant">{replay.state.lastVerdict ?? 'Assembling the recorded board run.'}</p>
              </div>
            </div>
          </section>
        </Reveal>

        <Reveal>
          <section className="mx-auto w-full max-w-7xl px-lg py-[132px]">
            <p className="font-label-caps text-label-caps text-surface-tint">REPAIR / LOOP</p>
            <h2 className="mt-md max-w-2xl font-display-mono text-[34px] leading-tight text-on-surface sm:text-[42px]">{/* One clause per line above sm, so the two halves of the loop read as a
              pair rather than breaking mid-sentence on "The". Left inline on mobile, where
              each clause is already too wide for the column. */}
              <span className="sm:block">The model decides.</span>{' '}
              <span className="sm:block">The engine proves.</span></h2>
            <div className="mt-xl grid grid-cols-1 gap-x-xl gap-y-xl md:grid-cols-3">
              {[['01', 'PROVE', 'The engine evaluates the board graph and records the evidence for what is broken.'], ['02', 'TRY', 'The model makes the judgement call — which repair is worth trying: swap, topology, part, rail, requirement, or escalate.'], ['03', 'RE-CHECK', 'The engine runs the rules again on the changed board. Every verdict is computed, not asserted.']].map(([number, label, description]) => <article className="pl-lg" key={number}><span className="font-data-tabular text-[11px] text-tertiary-fixed">{number}</span><h3 className="mt-md font-data-tabular text-[15px] text-on-surface">{label}</h3><p className="mt-sm text-[15px] leading-6 text-on-surface-variant">{description}</p></article>)}
            </div>
          </section>
        </Reveal>

        <Reveal>
          <section className="bg-surface-container-low py-[112px]">
            <div className="mx-auto grid w-full max-w-7xl gap-xl px-lg lg:grid-cols-[0.7fr_1.3fr]">
              <div><p className="font-label-caps text-label-caps text-surface-tint">MEMORY</p><h2 className="mt-md font-display-mono text-[30px] leading-tight text-on-surface">Where else did I use this?</h2><p className="mt-md max-w-md text-[16px] leading-7 text-on-surface-variant">Continuity remembers parts and decisions across boards, so you can trace where a part was used and whether it ever caused a finding.</p></div>
              <div><LandingMemoryGraph /><p className="mt-sm font-data-tabular text-[10px] text-outline">ILLUSTRATIVE SAMPLE / RECORDED WALKTHROUGH PARTS, NOT YOUR DATA</p></div>
            </div>
          </section>
        </Reveal>

        <Reveal>
          <section className="mx-auto w-full max-w-7xl px-lg py-[132px]">
            <p className="font-label-caps text-label-caps text-surface-tint">WALKTHROUGH / VIDEO</p>
            <div className="mt-md bg-surface-container-lowest p-xl">{WALKTHROUGH_VIDEO_ID ? <iframe allowFullScreen className="aspect-video w-full" src={`https://www.youtube-nocookie.com/embed/${WALKTHROUGH_VIDEO_ID}`} title="Continuity walkthrough" /> : <p className="font-data-tabular text-data-tabular text-on-surface-variant">VIDEO_PENDING / YOUTUBE_ID_NOT_SET</p>}</div>
          </section>
        </Reveal>

        <Reveal>
          <section className="bg-surface-container-low py-[112px]">
            <div className="mx-auto flex w-full max-w-7xl flex-col items-start justify-between gap-xl px-lg md:flex-row md:items-center"><div><p className="font-label-caps text-label-caps text-surface-tint">START A BOARD</p><h2 className="mt-sm font-display-mono text-[30px] text-on-surface">Bring the brief.</h2><p className="mt-sm max-w-md text-on-surface-variant">Describe what you need; the first run sources, checks, and assembles a board you can inspect.</p></div><Link className="bg-primary-container px-xl py-md font-label-caps text-label-caps text-on-primary-fixed hover:bg-primary-fixed" to="/signup">DESIGN A BOARD — ABOUT TWO MINUTES</Link></div>
          </section>
        </Reveal>
      </main>

      <footer className="bg-surface-container-lowest"><div className="mx-auto flex w-full max-w-7xl items-center justify-between px-lg py-lg font-data-tabular text-[11px] text-on-surface-variant"><Wordmark size="sm" /><span>© 2026</span></div></footer>
    </div>
  )
}
