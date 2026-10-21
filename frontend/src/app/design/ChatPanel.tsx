import { useEffect, useMemo, useRef, useState } from 'react'
import type { ComponentProps } from 'react'

import type {
  Alternative,
  CheckEvent,
  ErrorEvent,
  ConflictEvent,
  QuestionEvent,
  ReasoningEvent,
  RepairAction,
  RepairEvent,
  SessionStatus,
  DoneEvent,
} from '../lib/types'
import { ReasoningLine } from './ReasoningLine'

type ReasoningItem = ReasoningEvent | CheckEvent | RepairEvent | ErrorEvent

type SessionConflict = (ConflictEvent & { alternatives: Alternative[] }) | null

type ChatPanelProps = {
  status: SessionStatus
  summary: DoneEvent['summary'] | null
  reasoning: ReasoningItem[]
  question: QuestionEvent | null
  conflict: SessionConflict
  onAnswer: (text: string) => void
  onContinue: () => void
  canContinue: boolean
  onCancel: () => void
  onOpenConflict: () => void
  inFlightReasoningSeq: number | null
  isHydrated: boolean
}

function toTitleCase(token: string) {
  return token
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function repairActionLabel(action: RepairAction) {
  return toTitleCase(action)
}

/**
 * What one slot's checks add up to, so far.
 *
 * A failure wins over the tally: "4/5 checks passed" beside a line about a part that has
 * just failed thermal is technically true and useless — the reader wants the one that failed.
 */
function captionFor(checks: Map<string, CheckEvent>) {
  const results = [...checks.values()]
  const failed = results.filter((check) => check.status === 'fail')
  if (failed.length > 0) {
    return failed[failed.length - 1].detail
  }

  const passed = results.filter((check) => check.status === 'pass').length
  return `${passed}/${results.length} checks passed.`
}

function scrollBehavior(): ScrollBehavior {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
}

function InFlightReasoningLine({
  active,
  ...props
}: ComponentProps<typeof ReasoningLine> & { active: boolean }) {
  const [elapsedSeconds, setElapsedSeconds] = useState(0)

  useEffect(() => {
    if (!active) {
      setElapsedSeconds(0)
      return
    }

    const startedAt = Date.now()
    let interval: ReturnType<typeof setInterval> | undefined
    const delay = setTimeout(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000))
      interval = setInterval(() => {
        setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000))
      }, 1000)
    }, 2000)

    return () => {
      clearTimeout(delay)
      if (interval) {
        clearInterval(interval)
      }
    }
  }, [active])

  return (
    <ReasoningLine
      {...props}
      text={
        elapsedSeconds >= 2
          ? `${props.text.replace(/\.$/, '')}… ${elapsedSeconds}s`
          : props.text
      }
    />
  )
}

export function ChatPanel({
  status,
  summary,
  reasoning,
  question,
  conflict,
  onAnswer,
  onContinue,
  canContinue,
  onCancel,
  onOpenConflict,
  inFlightReasoningSeq,
  isHydrated,
}: ChatPanelProps) {
  const [answerText, setAnswerText] = useState('')

  const questionRef = useRef<HTMLDivElement>(null)
  const traceRef = useRef<HTMLDivElement>(null)
  const followLatestRef = useRef(true)

  // An open question is the only thing on screen the run is waiting on, and after a
  // long run the stream is hundreds of lines deep. Scroll it into view rather than
  // leaving the user to find it.
  useEffect(() => {
    if (question) {
      questionRef.current?.scrollIntoView({ behavior: scrollBehavior(), block: 'nearest' })
    }
  }, [question])

  /**
   * The narrative lines, each with the caption that was true **at that line**.
   *
   * The caption used to be a fold over the whole stream, applied to every line for that
   * slot — so a finished board captioned eight consecutive regulator lines, back to
   * "Searching JLCPCB for 3.3V LDO regulator", with the thermal verdict that killed a part
   * chosen later. On a repaired board it was worse than noise: that verdict belonged to the
   * AP7361C, which the TPS62825 had replaced, so the trace attributed a failure to a part
   * that was no longer on the board.
   *
   * Three rules make it honest. A line is captioned from the checks that ran **before** it.
   * A `repair` clears its slot's tally, because a repair begins a fresh attempt and the
   * previous attempt's score is not this candidate's — the repair line itself keeps the
   * failure that caused it, being the one line that verdict genuinely describes. And the
   * tally counts **distinct checks, not events**: a check is keyed by (rule, scope) exactly
   * as the contract says, so a rule re-run on a later validate pass replaces its earlier
   * result instead of inflating the denominator.
   *
   * Built in one pass so the captions cannot drift out of step with the lines.
   */
  const narrativeLines = useMemo(() => {
    const running = new Map<string, Map<string, CheckEvent>>()
    // `check` items are folded into the captions and never rendered as lines, so the
    // narrative type excludes them — which is also what lets the renderer read `item.text`
    // without a cast.
    const lines: Array<{ item: Exclude<ReasoningItem, CheckEvent>; caption: string | undefined }> = []

    for (const item of reasoning) {
      if (item.type === 'check') {
        const bySlot = running.get(item.slot) ?? new Map<string, CheckEvent>()
        bySlot.set(`${item.rule}:${item.scope ?? ''}`, item)
        running.set(item.slot, bySlot)
        continue
      }

      const slotOf = item.type === 'error' ? null : item.slot
      const checks = slotOf === null ? undefined : running.get(slotOf)
      lines.push({ item, caption: checks ? captionFor(checks) : undefined })

      if (item.type === 'repair' && slotOf !== null) {
        running.delete(slotOf)
      }
    }

    return lines
  }, [reasoning])

  useEffect(() => {
    if (question || !followLatestRef.current) {
      return
    }

    const trace = traceRef.current
    if (!trace) {
      return
    }

    trace.scrollTo({ top: trace.scrollHeight, behavior: 'auto' })
  }, [narrativeLines.length, question])

  const handleTraceScroll = () => {
    const trace = traceRef.current
    if (!trace) {
      return
    }

    followLatestRef.current = trace.scrollHeight - trace.scrollTop - trace.clientHeight <= 16
  }

  const submitAnswer = () => {
    const response = answerText.trim()
    if (!response) {
      return
    }

    onAnswer(response)
    setAnswerText('')
  }

  const composerRunning = status === 'running'
  const awaitingAnswer = status === 'paused'

  const statusIcon = composerRunning
    ? 'progress_activity'
    : awaitingAnswer
      ? 'pause_circle'
      : status === 'error'
        ? 'error'
        : status === 'done'
          ? 'check_circle'
          : 'radio_button_unchecked'
  const statusIconClassName = composerRunning
    ? 'text-primary-container spinner'
    : awaitingAnswer
      ? 'text-tertiary-container'
      : status === 'error'
        ? 'text-error'
        : status === 'done'
          ? 'text-[#4ade80]'
          : 'text-on-surface-variant'
  const statusText = composerRunning
    ? 'Running…'
    : awaitingAnswer
      ? 'Answer the question below to continue.'
      : status === 'error'
        ? 'The run stopped — see the log below.'
        : status === 'done'
          ? summary
            ? `Complete — ${summary.placed ?? summary.slots}/${summary.slots} placed, ${summary.conflicts_resolved} resolved.`
            : 'Run complete.'
          : 'Ready.'

  return (
    <section className="w-1/4 min-w-[280px] max-w-[400px] flex flex-col panel-border rounded-lg overflow-hidden flex-shrink-0">
      <header className="h-10 px-md flex items-center justify-between border-b border-outline-variant bg-surface-container-high flex-shrink-0">
        <div className="flex items-center gap-sm text-on-surface">
          <span className="material-symbols-outlined text-[16px] text-primary-container">psychology</span>
          <h2 className="font-headline-sm text-[14px] font-semibold tracking-wide">Validation Trace</h2>
        </div>
        <span className="font-data-tabular text-[10px] text-on-surface-variant bg-surface-variant px-1.5 py-0.5 rounded border border-outline-variant">
          {status.toUpperCase()}
        </span>
      </header>

      {/* A status strip, not a composer.
        *
        * This used to be a chat box whose only action was starting a *new* run — which
        * duplicated the brief screen a user has just come from, and silently discarded
        * the board on screen. A chat affordance over a batch job promises steering the
        * system cannot deliver; the honest version is to state what the run is doing.
        *
        * Questions are answered by their own inline input inside the question block, so
        * nothing here was needed to reply. If the mutable-plan work ever lands, mid-run
        * steering becomes real and a composer earns its place back. */}
      <div className="p-sm border-b border-outline-variant bg-surface-container flex-shrink-0" data-tour="run-status">
        <div className="w-full h-[64px] input-field px-sm flex items-center justify-between gap-sm">
          <div className="flex items-center gap-sm min-w-0">
            <span className={`material-symbols-outlined text-[16px] ${statusIconClassName}`}>
              {statusIcon}
            </span>
            <span className="font-data-tabular text-[11px] text-on-surface-variant truncate">
              {statusText}
            </span>
          </div>
          {composerRunning ? (
            <button
              className="h-7 px-sm border border-outline-variant rounded font-data-tabular text-[11px] text-on-surface hover:bg-surface-variant hover:border-error transition-colors"
              onClick={onCancel}
              type="button"
            >
              Stop
            </button>
          ) : canContinue ? (
            <button
              className="h-7 px-sm border border-primary-container rounded font-data-tabular text-[11px] text-primary-container hover:bg-surface-variant transition-colors"
              onClick={onContinue}
              type="button"
            >
              Continue
            </button>
          ) : null}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-sm flex flex-col gap-xs bg-[#0B0C0E]" data-tour="trace" onScroll={handleTraceScroll} ref={traceRef}>
        {narrativeLines.map(({ item, caption: detail }, index) => {
          const isActiveLine =
            index === narrativeLines.length - 1 &&
            status === 'running' &&
            !isHydrated &&
            item.type === 'reasoning' &&
            item.seq === inFlightReasoningSeq
          const icon = isActiveLine
            ? 'progress_activity'
            : item.type === 'error'
              ? 'error'
              : item.type === 'repair'
                ? 'build'
                : 'check_circle'
          const iconClassName = isActiveLine
            ? 'text-primary-container'
            : item.type === 'error'
              ? 'text-error'
              : item.type === 'repair'
                ? 'text-tertiary-container'
                : 'text-[#4ade80]'

          // The run died. `message` says why, and dropping it left one word on screen —
          // a 422 and a distributor timeout looked identical.
          const text =
            item.type === 'error'
              ? item.message
              : item.type === 'repair'
                ? `Repair • ${repairActionLabel(item.action)} — ${item.rationale}`
                : item.text

          return (
            <InFlightReasoningLine
              active={isActiveLine}
              detail={detail}
              icon={icon}
              iconClassName={iconClassName}
              key={`${item.type}-${item.seq}`}
              spinner={isActiveLine}
              text={text}
            />
          )
        })}

        {question ? (
          /* flex-shrink-0 is load-bearing. Inside a flex column this block was being
             compressed into whatever space was left over, and its own overflow-hidden
             then clipped the buttons — leaving nothing for the container to scroll to,
             so the run could not be answered at all. */
          <div
            className="flex flex-col flex-shrink-0 border border-outline-variant bg-[#16181D] rounded mt-sm mb-sm overflow-hidden shadow-[0_4px_12px_rgba(0,0,0,0.5)]"
            ref={questionRef}
          >
            <div className="flex items-start gap-sm px-sm py-2 bg-surface-container-high border-b border-outline-variant">
              <span
                className="material-symbols-outlined text-[14px] text-tertiary-container mt-[2px]"
                style={{ fontVariationSettings: "'FILL' 1" }}
              >
                help
              </span>
              <span className="font-data-tabular text-[12px] text-on-surface font-semibold text-tertiary-container">
                Action Required
              </span>
            </div>
            <div className="p-sm flex flex-col gap-sm">
              <p className="font-body-sm text-body-sm text-on-surface leading-relaxed">{question.text}</p>
              <div className="flex gap-sm flex-wrap">
                {question.suggestions.map((suggestion) => (
                  <button
                    className="py-1 px-2 border border-outline-variant rounded font-data-tabular text-[11px] text-on-surface hover:bg-surface-variant hover:border-primary transition-colors text-center"
                    key={suggestion}
                    onClick={() => onAnswer(suggestion)}
                    type="button"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
              <div className="relative w-full mt-1 flex gap-xs">
                <input
                  className="w-full h-7 input-field px-sm font-data-tabular text-[11px]"
                  onChange={(event) => setAnswerText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      submitAnswer()
                    }
                  }}
                  placeholder="Or type reasoning..."
                  type="text"
                  value={answerText}
                />
                <button
                  className="px-sm border border-outline-variant rounded text-[11px] font-data-tabular hover:border-primary"
                  onClick={submitAnswer}
                  type="button"
                >
                  Send
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {conflict ? (
          <div className="flex flex-col flex-shrink-0 border border-error/30 bg-[#16181D] rounded mb-sm overflow-hidden relative group">
            <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-error"></div>
            <div className="flex items-start gap-sm px-sm py-2">
              <span
                className="material-symbols-outlined text-[14px] text-error mt-[2px]"
                style={{ fontVariationSettings: "'FILL' 1" }}
              >
                error
              </span>
              <div className="flex flex-col">
                <span className="font-data-tabular text-[12px] text-error font-medium">
                  Conflict: {toTitleCase(conflict.rule)}
                </span>
                <span className="font-data-tabular text-[10px] text-on-surface-variant mt-1">
                  {conflict.message}
                </span>
              </div>
            </div>
            <div className="px-sm pb-2 pl-[28px] flex items-center gap-xs">
              <button
                className="text-[10px] font-label-caps uppercase-label text-error hover:text-[#ffdad6] flex items-center gap-1 transition-colors"
                onClick={onOpenConflict}
                type="button"
              >
                Highlight in Graph{' '}
                <span className="material-symbols-outlined text-[12px]">arrow_forward</span>
              </button>
              <button
                className="text-[10px] font-label-caps uppercase-label text-on-surface-variant hover:text-on-surface"
                onClick={onCancel}
                type="button"
              >
                Cancel Run
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  )
}
