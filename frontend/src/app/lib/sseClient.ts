import type { DesignEvent } from './types'
import { API_BASE, responseError } from './config'
import { getThreadBoard } from './api'

/**
 * Real backend client. Drop-in replacement for `mockEmitter`.
 *
 * ## Why fetch and not the browser's `EventSource`
 *
 * Native `EventSource` can only issue a GET with no body. `/design` and `/resume` both
 * take a JSON payload, so the stream is read off `fetch` instead. We lose the browser's
 * automatic reconnect, which we would have had to disable anyway — replaying a design
 * run from the top on a dropped connection would restart the graph, not resume it.
 *
 * ## Pacing
 *
 * Presentation timing lives in the UI, never in the event source. The mock's `speed`
 * scaled its own fake delays; there is nothing here to scale, because the delays are
 * now real backend work. The property stays so the two sources share a type, and so
 * that setting it can never silently do half of what it used to.
 */


/** The client treats this much silence as a dead stream. Contract §1. */
const STALL_TIMEOUT_MS = 30_000

type EventListener = (event: DesignEvent) => void

export type WalkthroughReplayMilestone = 'plan' | 'trace' | 'conflict' | 'bom' | 'complete'

export type DatasheetResponse = {
  mpn: string
  theta_ja: number | null
  source_line: string | null
  reason: string | null
}

export type DatasheetUploadResult =
  | { ok: true; data: DatasheetResponse }
  | { ok: false; status: number; message: string }

let controller: AbortController | null = null
let threadId: string | null = null
let listener: EventListener | null = null
let lastSequence = -1
let terminal = false
let walkthroughQueue: DesignEvent[] = []
let walkthroughMilestone: WalkthroughReplayMilestone | null = null
/** The last milestone asked for, satisfied or not. A milestone is released once. */
let walkthroughReleased: WalkthroughReplayMilestone | null = null
let walkthroughReplayActive = false

function reset() {
  controller?.abort()
  controller = null
  walkthroughQueue = []
  walkthroughMilestone = null
  walkthroughReleased = null
  walkthroughReplayActive = false
}

function emit(event: DesignEvent) {
  if (walkthroughReplayActive && event.type === 'error') {
    walkthroughReplayActive = false
    walkthroughQueue = []
    walkthroughMilestone = null
    listener?.(event)
    return
  }

  if (walkthroughReplayActive) {
    walkthroughQueue.push(event)
    flushWalkthroughReplay()
    return
  }
  listener?.(event)
}

function reachesWalkthroughMilestone(event: DesignEvent, milestone: WalkthroughReplayMilestone) {
  if (milestone === 'plan') return event.type === 'plan'
  if (milestone === 'trace') return event.type === 'selection' && event.slot === 'mcu'
  if (milestone === 'conflict') return event.type === 'conflict' && event.rule === 'availability'
  if (milestone === 'bom') return event.type === 'bom'
  return event.type === 'done'
}

function flushWalkthroughReplay() {
  if (walkthroughMilestone === null) {
    return
  }

  while (walkthroughQueue.length > 0) {
    const event = walkthroughQueue.shift()
    if (!event) {
      return
    }

    const reached = reachesWalkthroughMilestone(event, walkthroughMilestone)
    listener?.(event)
    if (reached) {
      walkthroughMilestone = null
      return
    }
  }
}

/** Release exactly one narrated portion of the otherwise fast recorded walkthrough.
 *
 * Asking twice for the same milestone is a no-op, and that matters more than it looks:
 * once a milestone has been reached its event will not appear again, so a repeat request
 * finds no stopping point and drains every frame that is left. That is precisely how a
 * caller re-firing this on each render turned a seven-step tour into a finished board by
 * its second card. The gate now holds regardless of how often it is asked.
 */
export function releaseWalkthroughReplay(milestone: WalkthroughReplayMilestone) {
  if (walkthroughReleased === milestone) {
    return
  }

  walkthroughReleased = milestone
  walkthroughMilestone = milestone
  flushWalkthroughReplay()
}

/**
 * Synthesise an error frame the UI can render.
 *
 * The stream is the only channel this component has. A fetch that rejects without
 * producing a frame would leave the session stuck on `running` with nothing on screen
 * explaining why, which is worse than a visible failure.
 */
function emitError(message: string, recoverable = false) {
  emit({
    type: 'error',
    seq: -1,
    thread_id: threadId ?? '',
    message,
    recoverable,
  } as DesignEvent)
}

/**
 * Read `data:` frames out of an SSE body.
 *
 * Frames are separated by a blank line and may be split across chunk boundaries, so the
 * tail of the buffer is held back until its terminator arrives. `:` comment lines —
 * the heartbeat — are skipped.
 */
async function readStream(response: Response, signal: AbortSignal) {
  if (!response.body) throw new Error('The server returned no stream.')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let lastFrameAt = Date.now()
  let stalled = false
  const stall = setInterval(() => {
    if (Date.now() - lastFrameAt > STALL_TIMEOUT_MS) {
      stalled = true
      reader.cancel().catch(() => undefined)
    }
  }, 5_000)
  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read()
      if (done || signal.aborted) break
      lastFrameAt = Date.now()
      buffer += decoder.decode(value, { stream: true })
      if (buffer.length > 4_000_000) throw new Error('The server sent an oversized event.')
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() ?? ''
      for (const block of blocks) {
        const data = block.split(/\r?\n/).filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart()).join('\n')
        if (!data) continue
        const event = JSON.parse(data) as DesignEvent
        if (!Number.isInteger(event.seq) || !event.thread_id || typeof event.type !== 'string') {
          throw new Error('The server sent an invalid design event.')
        }
        if (event.seq <= lastSequence) continue
        threadId = event.thread_id
        lastSequence = event.seq
        terminal = event.type === 'done' || event.type === 'question' || event.type === 'error'
        emit(event)
      }
    }
    if (stalled) throw new Error('The connection went quiet.')
  } finally {
    clearInterval(stall)
    reader.releaseLock()
  }
}

async function reconnectStream(signal: AbortSignal) {
  for (let attempt = 0; attempt < 4 && !signal.aborted && !terminal; attempt += 1) {
    if (attempt > 0) await new Promise((resolve) => setTimeout(resolve, 1000 * 2 ** (attempt - 1)))
    if (signal.aborted || !threadId) return
    try {
      const response = await fetch(
        `${API_BASE}/threads/${encodeURIComponent(threadId)}/events?after=${lastSequence}`,
        { credentials: 'include', signal },
      )
      if (!response.ok) {
        const message = await responseError(response)
        if ([401, 403, 404].includes(response.status)) {
          emitError(message)
          return
        }
        throw new Error(message)
      }
      await readStream(response, signal)
    } catch (error) {
      if (signal.aborted) return
      if (attempt === 3) throw error
    }
  }
  if (!signal.aborted && !terminal) {
    emitError('Connection interrupted. Reopen this project to continue the saved run.', true)
  }
}

async function post(path: string, payload: unknown, signal: AbortSignal) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload), signal,
  })
  if (signal.aborted) return
  if (!response.ok) {
    emitError(await responseError(response), response.status >= 500)
    return
  }
  threadId = response.headers.get('X-Thread-ID') ?? threadId
  terminal = false
  try {
    await readStream(response, signal)
  } catch (error) {
    if (signal.aborted) return
    if (!threadId) throw error
  }
  if (!signal.aborted && !terminal && threadId) await reconnectStream(signal)
}

function responseMessage(payload: unknown, fallback: string) {
  if (typeof payload !== 'object' || payload === null) return fallback

  const body = payload as Record<string, unknown>
  for (const key of ['detail', 'message', 'error']) {
    if (typeof body[key] === 'string' && body[key].trim()) return body[key]
  }

  if (Array.isArray(body.detail)) {
    const messages = body.detail
      .map((detail) => {
        if (typeof detail === 'string') return detail
        if (typeof detail === 'object' && detail !== null && 'msg' in detail) {
          const message = (detail as Record<string, unknown>).msg
          return typeof message === 'string' ? message : null
        }
        return null
      })
      .filter((message): message is string => message !== null)

    if (messages.length > 0) return messages.join(' ')
  }

  return fallback
}

export const sseClient = {
  /** Present so the real and mock sources share one type. Deliberately inert — see above. */
  speed: 1 as 1 | 2 | 4,

  start(prompt: string, onEvent: EventListener, projectId?: string, requestId?: string) {
    reset()
    threadId = null
    lastSequence = -1
    terminal = false
    listener = onEvent
    controller = new AbortController()
    const { signal } = controller

    const payload = projectId
      ? { prompt, project_id: projectId }
      : { prompt }

    post('/design', { ...payload, request_id: requestId ?? crypto.randomUUID() }, signal).catch((error: unknown) => {
      if (signal.aborted) return
      emitError(
        error instanceof Error ? error.message : 'Could not reach the design service.',
      )
    })
  },

  startBom(bom: string, onEvent: EventListener, prompt?: string, projectId?: string, requestId?: string) {
    reset()
    threadId = null
    lastSequence = -1
    terminal = false
    listener = onEvent
    controller = new AbortController()
    const { signal } = controller

    const payload = {
      bom,
      ...(prompt ? { prompt } : {}),
      ...(projectId ? { project_id: projectId } : {}),
    }

    post('/bom/validate', { ...payload, request_id: requestId ?? crypto.randomUUID() }, signal).catch((error: unknown) => {
      if (signal.aborted) return
      emitError(
        error instanceof Error ? error.message : 'Could not reach the design service.',
      )
    })
  },

  startDemo(onEvent: EventListener) {
    reset()
    threadId = null
    lastSequence = -1
    terminal = false
    listener = onEvent
    // The server correctly streams the recording as fast as it can. The tour owns the
    // visible pacing, so hold frames until its next narrated milestone asks for them.
    walkthroughReplayActive = true
    controller = new AbortController()
    const { signal } = controller

    post('/design/demo', {}, signal).catch((error: unknown) => {
      if (signal.aborted) return
      emitError(
        error instanceof Error ? error.message : 'Could not reach the design service.',
      )
    })
  },

  /**
   * Answer an open question.
   *
   * `/resume` opens a *second* HTTP response carrying the rest of the same logical
   * stream — the sequence continues where the first left off rather than restarting,
   * which is why the hook's seq gate does not discard everything after a resume.
   */
  answer(text: string) {
    if (!threadId) return
    reset()
    controller = new AbortController()
    const { signal } = controller

    post('/resume', { thread_id: threadId, answer: text }, signal).catch(
      (error: unknown) => {
        if (signal.aborted) return
        emitError(error instanceof Error ? error.message : 'Could not send the answer.')
      },
    )
  },

  continueRun(savedThreadId: string, onEvent: EventListener) {
    reset()
    threadId = savedThreadId
    listener = onEvent
    controller = new AbortController()
    const { signal } = controller

    terminal = false
    getThreadBoard(savedThreadId).then((board) => {
      if (signal.aborted) return
      if (board.status === 'running') return reconnectStream(signal)
      return post(`/threads/${encodeURIComponent(savedThreadId)}/continue`, {}, signal)
    }).catch(
      (error: unknown) => {
        if (signal.aborted) return
        emitError(error instanceof Error ? error.message : 'Could not continue the run.')
      },
    )
  },

  reconnect(savedThreadId: string, onEvent: EventListener) {
    reset()
    threadId = savedThreadId
    lastSequence = -1
    terminal = false
    listener = onEvent
    controller = new AbortController()
    const { signal } = controller
    reconnectStream(signal).catch((error: unknown) => {
      if (!signal.aborted) emitError(error instanceof Error ? error.message : 'Could not reconnect.', true)
    })
  },

  restore(savedThreadId: string, onEvent: EventListener, lastSeq = -1) {
    lastSequence = lastSeq
    threadId = savedThreadId
    listener = onEvent
  },

  disconnect() {
    reset()
    listener = null
  },

  cancel() {
    const id = threadId
    reset()
    listener = null
    if (id) fetch(`${API_BASE}/threads/${encodeURIComponent(id)}/cancel`, {
      method: 'POST', credentials: 'include',
    }).catch(() => undefined)
  },

  /**
   * Upload a part-specific PDF for thermal extraction. This is intentionally a
   * regular request: it must never share or reset the live SSE stream controller.
   */
  async uploadDatasheet(
    mpn: string,
    packageName: string,
    document: string,
  ): Promise<DatasheetUploadResult> {
    const response = await fetch(`${API_BASE}/datasheet`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mpn, package: packageName, document }),
    })

    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        message: responseMessage(body, `The server refused the datasheet (${response.status}).`),
      }
    }

    return { ok: true, data: body as DatasheetResponse }
  },

  /** Where `/export/{thread}.csv` lives for the current run, or null before one starts. */
  exportUrl(): string | null {
    return threadId ? `${API_BASE}/export/${threadId}.csv` : null
  },
}
