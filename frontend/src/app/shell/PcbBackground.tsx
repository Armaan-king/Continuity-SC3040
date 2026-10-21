import { useEffect, useRef } from 'react'

type Point = { x: number; y: number }

type Trace = {
  points: Point[]
  length: number
  width: number
  pulseCell: string
}

type Pad = {
  point: Point
  via: boolean
}

type Board = {
  pads: Pad[]
  traces: Trace[]
}

type Pulse = {
  startedAt: number
  trace: Trace
}

const GRID_SIZE = 28
const BOARD_MARGIN = 28
const MAX_TRACE_RUN = 168
const MIN_SEGMENT_COUNT = 110
const MAX_SEGMENT_COUNT = 190
const MAX_DEVICE_PIXEL_RATIO = 1.5
const MAX_ACTIVE_PULSES = 10
const PULSE_SPEED_PX_PER_SECOND = 150
const PULSE_INTERVAL_MS = 520
const PULSE_TAIL_LENGTH = 68
/** Solder-mask green, laid under the copper. Barely there by design — see drawStaticBoard. */
const MASK_TINT = 'rgba(24, 62, 44, 0.30)'
const TRACE_OPACITY = 0.42
const PAD_OPACITY = 0.56
const PULSE_TAIL_OPACITY = 0.62
const RESIZE_DEBOUNCE_MS = 140

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value))
}

function segmentLength(from: Point, to: Point) {
  return Math.hypot(to.x - from.x, to.y - from.y)
}

function traceLength(points: Point[]) {
  return points.slice(1).reduce((total, point, index) => total + segmentLength(points[index], point), 0)
}

function samePoint(first: Point, second: Point) {
  return first.x === second.x && first.y === second.y
}

function withAlpha(color: string, alpha: number) {
  const channels = color.match(/\d+(?:\.\d+)?/g)
  return channels && channels.length >= 3 ? `rgba(${channels[0]}, ${channels[1]}, ${channels[2]}, ${alpha})` : color
}

function pointAtDistance(trace: Trace, distance: number) {
  let travelled = 0

  for (let index = 1; index < trace.points.length; index += 1) {
    const from = trace.points[index - 1]
    const to = trace.points[index]
    const length = segmentLength(from, to)
    // A trace whose corner lands on its own start has a zero-length segment, and
    // `(distance - travelled) / 0` is NaN. That NaN reached `createRadialGradient`, which
    // throws — inside the animation frame, before it requests the next one. One malformed
    // trace therefore killed the whole background permanently.
    if (length === 0) {
      continue
    }
    if (distance <= travelled + length) {
      const progress = (distance - travelled) / length
      return { x: from.x + (to.x - from.x) * progress, y: from.y + (to.y - from.y) * progress }
    }
    travelled += length
  }

  return trace.points[trace.points.length - 1]
}

function drawTrace(context: CanvasRenderingContext2D, trace: Trace) {
  context.beginPath()
  trace.points.forEach((point, index) => {
    if (index === 0) {
      context.moveTo(point.x, point.y)
    } else {
      context.lineTo(point.x, point.y)
    }
  })
  context.stroke()
}

function drawTraceRange(context: CanvasRenderingContext2D, trace: Trace, start: number, end: number) {
  let travelled = 0
  let drawing = false

  context.beginPath()
  for (let index = 1; index < trace.points.length; index += 1) {
    const from = trace.points[index - 1]
    const to = trace.points[index]
    const length = segmentLength(from, to)
    const segmentStart = Math.max(start - travelled, 0)
    const segmentEnd = Math.min(end - travelled, length)

    if (segmentStart < segmentEnd) {
      const first = {
        x: from.x + ((to.x - from.x) * segmentStart) / length,
        y: from.y + ((to.y - from.y) * segmentStart) / length,
      }
      const last = {
        x: from.x + ((to.x - from.x) * segmentEnd) / length,
        y: from.y + ((to.y - from.y) * segmentEnd) / length,
      }
      if (!drawing) {
        context.moveTo(first.x, first.y)
        drawing = true
      } else {
        context.lineTo(first.x, first.y)
      }
      context.lineTo(last.x, last.y)
    }

    travelled += length
    if (travelled >= end) {
      break
    }
  }
  if (drawing) {
    context.stroke()
  }
}

function buildBoard(width: number, height: number): Board {
  const columns = Math.max(3, Math.floor((width - BOARD_MARGIN * 2) / GRID_SIZE))
  const rows = Math.max(3, Math.floor((height - BOARD_MARGIN * 2) / GRID_SIZE))
  const pointFor = (column: number, row: number) => ({
    x: BOARD_MARGIN + column * GRID_SIZE,
    y: BOARD_MARGIN + row * GRID_SIZE,
  })
  const targetSegments = clamp(Math.round((width * height) / 11_000), MIN_SEGMENT_COUNT, MAX_SEGMENT_COUNT)
  const traceCount = Math.ceil(targetSegments / 2.5)
  const originColumns = Math.max(3, Math.ceil(Math.sqrt((traceCount * columns) / rows)))
  const originRows = Math.max(3, Math.ceil(traceCount / originColumns))
  const originOrder = [...Array(originColumns * originRows).keys()].sort(() => Math.random() - 0.5)
  const maxRunCells = Math.max(2, Math.floor(MAX_TRACE_RUN / GRID_SIZE))
  const jitteredOrigin = (index: number) => {
    const cell = originOrder[index % originOrder.length]
    const column = cell % originColumns
    const row = Math.floor(cell / originColumns)
    const minColumn = Math.floor((column * columns) / originColumns)
    const maxColumn = Math.max(minColumn, Math.floor(((column + 1) * columns) / originColumns) - 1)
    const minRow = Math.floor((row * rows) / originRows)
    const maxRow = Math.max(minRow, Math.floor(((row + 1) * rows) / originRows) - 1)
    return pointFor(
      minColumn + Math.floor(Math.random() * (maxColumn - minColumn + 1)),
      minRow + Math.floor(Math.random() * (maxRow - minRow + 1)),
    )
  }
  const chamferIndexes = new Set(
    [...Array(traceCount).keys()]
      .sort(() => Math.random() - 0.5)
      .slice(0, targetSegments - traceCount * 2),
  )
  const traces: Trace[] = []
  const nodes = new Map<string, Point>()
  const addNode = (point: Point) => nodes.set(`${point.x}:${point.y}`, point)

  for (let index = 0; index < traceCount; index += 1) {
    // Most runs begin at a node another run already ended on, so the field reads as one
    // routed network rather than a scattering of unrelated segments. The first few, and a
    // steady minority after, still start fresh so the board covers the whole viewport
    // instead of growing out from a single corner.
    const existing = [...nodes.values()]
    const start = existing.length > 0 && index > 2 && Math.random() < 0.62
      ? existing[Math.floor(Math.random() * existing.length)]
      : jitteredOrigin(index)
    let deltaX = 0
    let deltaY = 0
    while (deltaX === 0 || deltaY === 0) {
      deltaX = (Math.floor(Math.random() * maxRunCells * 2 + 1) - maxRunCells) * GRID_SIZE
      deltaY = (Math.floor(Math.random() * maxRunCells * 2 + 1) - maxRunCells) * GRID_SIZE
    }
    const end = {
      x: clamp(start.x + deltaX, BOARD_MARGIN, BOARD_MARGIN + (columns - 1) * GRID_SIZE),
      y: clamp(start.y + deltaY, BOARD_MARGIN, BOARD_MARGIN + (rows - 1) * GRID_SIZE),
    }
    if (samePoint(start, end)) continue

    const horizontalFirst = Math.random() < 0.5
    const corner = horizontalFirst ? { x: end.x, y: start.y } : { x: start.x, y: end.y }
    const chamfered = chamferIndexes.has(index)
    const horizontalRun = horizontalFirst ? end.x - start.x : end.x - corner.x
    const verticalRun = horizontalFirst ? end.y - corner.y : end.y - start.y
    const chamfer = Math.min(8, Math.abs(horizontalRun) / 3, Math.abs(verticalRun) / 3)
    const points = chamfered && chamfer >= 4
      ? horizontalFirst
        ? [
            start,
            { x: corner.x - Math.sign(horizontalRun) * chamfer, y: corner.y },
            { x: corner.x, y: corner.y + Math.sign(verticalRun) * chamfer },
            end,
          ]
        : [
            start,
            { x: corner.x, y: corner.y - Math.sign(verticalRun) * chamfer },
            { x: corner.x + Math.sign(horizontalRun) * chamfer, y: corner.y },
            end,
          ]
      : [start, corner, end]

    const deduped = points.filter((point, index) => index === 0 || !samePoint(points[index - 1], point))
    if (deduped.length < 2) continue

    traces.push({
      points: deduped,
      length: traceLength(deduped),
      width: Math.random() < 0.18 ? 3.2 : 2,
      pulseCell: `${Math.min(3, Math.floor(((start.x + end.x) / 2 / width) * 4))}:${Math.min(2, Math.floor(((start.y + end.y) / 2 / height) * 3))}`,
    })
    addNode(start)
    addNode(end)
    if (!chamfered || chamfer < 4) {
      addNode(corner)
    }
  }

  return {
    traces,
    pads: [...nodes.values()].map((point) => ({ point, via: Math.random() < 0.27 })),
  }
}

function drawStaticBoard(
  context: CanvasRenderingContext2D,
  board: Board,
  width: number,
  height: number,
  traceColor: string,
) {
  context.clearRect(0, 0, width, height)

  // Solder mask. A real board is green because of the mask laid *between* the copper, so
  // the tint belongs to the ground rather than the traces. Kept at a very low alpha on
  // purpose: it should register as "this surface is a board", never as a green page, and
  // anything strong enough to notice directly would start fighting the text above it.
  context.globalAlpha = 1
  context.fillStyle = MASK_TINT
  context.fillRect(0, 0, width, height)

  context.strokeStyle = traceColor
  context.globalAlpha = TRACE_OPACITY
  board.traces.forEach((trace) => {
    context.lineWidth = trace.width
    drawTrace(context, trace)
  })

  context.globalAlpha = PAD_OPACITY
  board.pads.forEach(({ point, via }) => {
    context.beginPath()
    if (via) {
      context.lineWidth = 1.4
      context.arc(point.x, point.y, 4.4, 0, Math.PI * 2)
      context.stroke()
      context.save()
      context.globalCompositeOperation = 'destination-out'
      context.beginPath()
      context.arc(point.x, point.y, 2, 0, Math.PI * 2)
      context.fill()
      context.restore()
    } else {
      context.arc(point.x, point.y, 3.5, 0, Math.PI * 2)
      context.fillStyle = traceColor
      context.fill()
    }
  })
  context.globalAlpha = 1
}

function pulseLifetime(trace: Trace) {
  return ((trace.length + PULSE_TAIL_LENGTH) / PULSE_SPEED_PX_PER_SECOND) * 1_000
}

function drawPulse(context: CanvasRenderingContext2D, pulse: Pulse, now: number, pulseColor: string) {
  const headDistance = ((now - pulse.startedAt) / 1_000) * PULSE_SPEED_PX_PER_SECOND
  if (headDistance < 0 || headDistance > pulse.trace.length) {
    return
  }

  const tailStart = Math.max(0, headDistance - PULSE_TAIL_LENGTH)
  const slices = 6
  for (let index = 0; index < slices; index += 1) {
    const start = tailStart + ((headDistance - tailStart) * index) / slices
    const end = tailStart + ((headDistance - tailStart) * (index + 1)) / slices
    const strength = (index + 1) / slices
    context.strokeStyle = pulseColor
    context.lineWidth = pulse.trace.width * 3.2
    context.globalAlpha = PULSE_TAIL_OPACITY * strength * 0.14
    drawTraceRange(context, pulse.trace, start, end)
    context.lineWidth = pulse.trace.width + 1
    context.globalAlpha = PULSE_TAIL_OPACITY * strength * 0.72
    drawTraceRange(context, pulse.trace, start, end)
  }

  const head = pointAtDistance(pulse.trace, headDistance)
  const radius = pulse.trace.width * 7
  const glow = context.createRadialGradient(head.x, head.y, 0, head.x, head.y, radius)
  glow.addColorStop(0, 'rgba(255, 233, 210, 0.72)')
  glow.addColorStop(0.13, pulseColor)
  glow.addColorStop(0.42, withAlpha(pulseColor, 0.3))
  glow.addColorStop(1, withAlpha(pulseColor, 0))
  context.globalAlpha = 1
  context.fillStyle = glow
  context.beginPath()
  context.arc(head.x, head.y, radius, 0, Math.PI * 2)
  context.fill()
  context.fillStyle = 'rgba(255, 236, 214, 0.7)'
  context.beginPath()
  context.arc(head.x, head.y, Math.max(1.4, pulse.trace.width * 0.85), 0, Math.PI * 2)
  context.fill()
}

/** A cached, grid-routed circuit-board texture for ordinary app pages. */
export function PcbBackground({ pauseAnimation = false }: { pauseAnimation?: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const tokenRef = useRef<HTMLSpanElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const token = tokenRef.current
    if (!canvas || !token) {
      return
    }

    const context = canvas.getContext('2d')
    const boardCanvas = document.createElement('canvas')
    const boardContext = boardCanvas.getContext('2d')
    if (!context || !boardContext) {
      return
    }

    const styles = window.getComputedStyle(token)
    const traceColor = styles.backgroundColor
    const pulseColor = styles.color
    let board: Board = { pads: [], traces: [] }
    let pulses: Pulse[] = []
    let frame = 0
    let resizeTimer = 0
    let nextPulseAt = 0
    let visible = document.visibilityState === 'visible'
    let reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let width = 1
    let height = 1

    const addPulse = (now: number, age = 0) => {
      const active = new Set(pulses.map((pulse) => pulse.trace))
      const candidates = board.traces.filter((trace) => !active.has(trace))
      const activeByCell = new Map<string, number>()
      pulses.forEach((pulse) => activeByCell.set(pulse.trace.pulseCell, (activeByCell.get(pulse.trace.pulseCell) ?? 0) + 1))
      const leastActive = Math.min(...candidates.map((trace) => activeByCell.get(trace.pulseCell) ?? 0))
      const spreadCandidates = candidates.filter((trace) => (activeByCell.get(trace.pulseCell) ?? 0) === leastActive)
      const trace = spreadCandidates[Math.floor(Math.random() * spreadCandidates.length)]
      if (trace) {
        pulses.push({ startedAt: now - age, trace })
      }
    }

    const draw = (now: number) => {
      context.clearRect(0, 0, width, height)
      context.drawImage(boardCanvas, 0, 0, boardCanvas.width, boardCanvas.height, 0, 0, width, height)

      if (reducedMotion || pauseAnimation) {
        return
      }

      pulses = pulses.filter((pulse) => now - pulse.startedAt < pulseLifetime(pulse.trace))
      if (now >= nextPulseAt && pulses.length < MAX_ACTIVE_PULSES) {
        addPulse(now)
        nextPulseAt = now + PULSE_INTERVAL_MS * (0.75 + Math.random() * 0.75)
      }
      // Defence in depth: a throw here used to end the animation for the life of the page.
      pulses.forEach((pulse) => {
        try {
          drawPulse(context, pulse, now, pulseColor)
        } catch {
          pulses = pulses.filter((candidate) => candidate !== pulse)
        }
      })
      context.globalAlpha = 1
    }

    const rebuild = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, MAX_DEVICE_PIXEL_RATIO)
      width = window.innerWidth
      height = window.innerHeight
      canvas.width = Math.max(1, Math.floor(width * ratio))
      canvas.height = Math.max(1, Math.floor(height * ratio))
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      boardCanvas.width = canvas.width
      boardCanvas.height = canvas.height
      context.setTransform(ratio, 0, 0, ratio, 0, 0)
      boardContext.setTransform(ratio, 0, 0, ratio, 0, 0)
      board = buildBoard(width, height)
      drawStaticBoard(boardContext, board, width, height, traceColor)
      const now = performance.now()
      pulses = []
      for (let index = 0; index < Math.min(MAX_ACTIVE_PULSES - 1, board.traces.length); index += 1) {
        const candidate = board.traces[Math.floor(Math.random() * board.traces.length)]
        addPulse(now, candidate ? (candidate.length / PULSE_SPEED_PX_PER_SECOND) * 1_000 * Math.random() * 0.82 : 0)
      }
      nextPulseAt = now + PULSE_INTERVAL_MS * (0.4 + Math.random() * 0.8)
      draw(now)
    }

    const animate = (now: number) => {
      draw(now)
      if (visible && !reducedMotion && !pauseAnimation) {
        frame = requestAnimationFrame(animate)
      } else {
        frame = 0
      }
    }

    const start = () => {
      if (!visible || reducedMotion || pauseAnimation || frame) {
        return
      }
      frame = requestAnimationFrame(animate)
    }

    const stop = () => {
      if (frame) {
        cancelAnimationFrame(frame)
        frame = 0
      }
    }

    const onVisibilityChange = () => {
      visible = document.visibilityState === 'visible'
      if (visible) {
        nextPulseAt = performance.now() + PULSE_INTERVAL_MS
        start()
      } else {
        stop()
      }
    }

    const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onMotionChange = (event: MediaQueryListEvent) => {
      reducedMotion = event.matches
      if (reducedMotion) {
        stop()
        draw(performance.now())
      } else {
        start()
      }
    }

    const onResize = () => {
      window.clearTimeout(resizeTimer)
      resizeTimer = window.setTimeout(rebuild, RESIZE_DEBOUNCE_MS)
    }

    rebuild()
    start()
    window.addEventListener('resize', onResize)
    document.addEventListener('visibilitychange', onVisibilityChange)
    motion.addEventListener('change', onMotionChange)

    return () => {
      stop()
      window.clearTimeout(resizeTimer)
      window.removeEventListener('resize', onResize)
      document.removeEventListener('visibilitychange', onVisibilityChange)
      motion.removeEventListener('change', onMotionChange)
    }
  }, [pauseAnimation])

  return (
    <>
      <canvas aria-hidden="true" className="pointer-events-none fixed inset-0 z-0" ref={canvasRef} />
      <span aria-hidden="true" className="fixed invisible bg-[#6b4a2f] text-primary-container" ref={tokenRef} />
    </>
  )
}
