import { forceCollide, forceY } from 'd3-force'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D, { type ForceGraphMethods } from 'react-force-graph-2d'

/**
 * A small live instance of the `/memory` graph, drawn with the same renderer, the same
 * forces and the same paint — so the landing page shows the real thing rather than a
 * picture of it. Nodes settle, drag, and highlight their neighbourhood exactly as they do
 * in the product; only the data is a fixed sample, and only the wheel is left alone so
 * scrolling the page over the graph does not zoom it.
 *
 * The container matters more than any of that: `min-w-0` plus an absolutely-positioned
 * canvas break a feedback loop where the canvas took its width from the measured box and,
 * as a grid item sized to its own content, made the box wider on every measurement. It
 * reached 19,397px. That — not the simulation — was the jumping, the long pauses and the
 * dark rectangle over the background.
 */

// The same constants the product graph runs, so the sample settles with the same feel.
const REPEL_STRENGTH = -160
const CENTER_STRENGTH = 0.025
const COLLIDE_PADDING = 5
// Two that are larger here than in `/memory`, and for a reason that is about this data
// rather than about taste: eight nodes carrying full part numbers means the *labels* are
// what crowd, not the nodes, and there are too few neighbours for repulsion to separate
// them on its own. `/memory` has 35 nodes pushing each other apart.
const LINK_DISTANCE = 78
/** Room for the label painted under each node, so text does not collide with text. */
const LABEL_CLEARANCE = 28
const ARRIVAL_MS = 560
/** Kept generous: `zoomToFit` measures node centres and never the labels under them. */
const FRAME_PADDING = 44

type SamplePart = {
  mpn: string
  /** Boards this part appears on. Drives the node radius, as it does in `/memory`. */
  usedIn: number
  findings: number
}

type SampleNode = {
  id: string
  kind: 'part' | 'project'
  label: string
  part?: SamplePart
  x?: number
  y?: number
}

type SampleLink = {
  id: string
  source: string | SampleNode
  target: string | SampleNode
  /** "Was on this board, then replaced" — drawn dashed, as in `/memory`. */
  historical?: boolean
}

function part(mpn: string, usedIn: number, findings = 0): SampleNode {
  return { id: `part:${mpn}`, kind: 'part', label: mpn, part: { mpn, usedIn, findings } }
}

function project(id: string, label: string): SampleNode {
  return { id: `project:${id}`, kind: 'project', label }
}

const graph: { nodes: SampleNode[]; links: SampleLink[] } = {
  nodes: [
    part('TPS62825DMQR', 2),
    part('ESP32-S3-WROOM-1-N8R2', 2),
    part('SHT40-AD1B-R2', 1, 1),
    part('ER-OLED013-1', 1),
    part('AMS1117-3.3', 0, 1),
    project('field-node', 'Sample field node'),
    project('display-node', 'Sample display node'),
    project('repair-study', 'Sample repair study'),
  ],
  links: [
    { id: 'tps:field', source: 'part:TPS62825DMQR', target: 'project:field-node' },
    { id: 'esp:field', source: 'part:ESP32-S3-WROOM-1-N8R2', target: 'project:field-node' },
    { id: 'sht:field', source: 'part:SHT40-AD1B-R2', target: 'project:field-node' },
    { id: 'tps:display', source: 'part:TPS62825DMQR', target: 'project:display-node' },
    { id: 'esp:display', source: 'part:ESP32-S3-WROOM-1-N8R2', target: 'project:display-node' },
    { id: 'oled:display', source: 'part:ER-OLED013-1', target: 'project:display-node' },
    { id: 'sht:repair', source: 'part:SHT40-AD1B-R2', target: 'project:repair-study' },
    // Replaced during a repair, so it is drawn but not claimed as still in the BOM.
    { id: 'ams:repair', source: 'part:AMS1117-3.3', target: 'project:repair-study', historical: true },
  ],
}

const nodesById = new Map(graph.nodes.map((node) => [node.id, node]))

function partRadius(sample: SamplePart) {
  // Value is area, so radius grows as sqrt(uses) — the product's rule, unchanged.
  return 6 + Math.sqrt(sample.usedIn) * 3
}

function nodeRadius(node: SampleNode) {
  return node.part ? partRadius(node.part) : 11
}

function endpointId(end: string | SampleNode) {
  return typeof end === 'string' ? end : end.id
}

export function LandingMemoryGraph() {
  const graphRef = useRef<ForceGraphMethods<SampleNode, SampleLink> | undefined>(undefined)
  /** Set as soon as the reader touches the graph. Re-framing after that would yank the
   *  view away from wherever they dragged a node to, which is the product's rule too. */
  const touched = useRef(false)
  const enteredAt = useRef(0)
  const [size, setSize] = useState({ width: 1, height: 1 })
  const [hovered, setHovered] = useState<SampleNode | null>(null)
  const [reducedMotion, setReducedMotion] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReducedMotion(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  const host = useCallback((node: HTMLDivElement | null) => {
    if (!node) return undefined
    const measure = () => {
      const rect = node.getBoundingClientRect()
      setSize({ width: Math.max(1, rect.width), height: Math.max(1, rect.height) })
    }
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    measure()
    return () => observer.disconnect()
  }, [])

  // Re-frame whenever the box changes size, until the reader takes over. Framing only on
  // the first engine stop left the graph small and off to one side: the simulation can
  // settle before the `ResizeObserver` has reported a real width, and `zoomToFit` against
  // a 1×1 canvas fits nothing.
  useEffect(() => {
    if (touched.current || size.width < 2) return
    graphRef.current?.zoomToFit(0, FRAME_PADDING)
  }, [size])

  useEffect(() => {
    const force = graphRef.current
    if (!force) return
    // Configure the forces the simulation already has; only `collide` has to be added.
    // These are methods on a d3 force, not properties — replacing a force with an object
    // silently disables it, which is how the product graph once lost its whole feel.
    force.d3Force('charge')?.strength(reducedMotion ? 0 : REPEL_STRENGTH)
    force.d3Force('link')?.distance(LINK_DISTANCE)
    force.d3Force('center')?.strength(reducedMotion ? 0 : CENTER_STRENGTH)
    force.d3Force(
      'collide',
      forceCollide<SampleNode>((node) => nodeRadius(node) + COLLIDE_PADDING + LABEL_CLEARANCE),
    )
    // Not in `/memory`, and only because of the shape of the hole: that graph gets a
    // near-square viewport, this one a 2:1 letterbox. An isotropic layout in a letterbox
    // settles as a tall chain, and the fit then shrinks everything to make the height
    // work while two thirds of the width goes unused. A gentle pull toward the centre
    // line flattens it into the space that actually exists.
    force.d3Force('flatten', forceY<SampleNode>(0).strength(reducedMotion ? 0 : 0.12))
    enteredAt.current = performance.now()
    force.d3ReheatSimulation()
    // Deliberately runs once. The version that also re-ran on every measured width change
    // re-heated the simulation continuously while the box was growing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reducedMotion])

  const neighbourhood = useMemo(() => {
    if (!hovered) return null
    const neighbours = new Set([hovered.id])
    const links = new Set<string>()
    graph.links.forEach((link) => {
      const source = endpointId(link.source)
      const target = endpointId(link.target)
      if (source === hovered.id || target === hovered.id) {
        neighbours.add(source)
        neighbours.add(target)
        links.add(link.id)
      }
    })
    return { neighbours, links }
  }, [hovered])

  const arrival = useCallback(() => {
    if (reducedMotion || !enteredAt.current) return 1
    return Math.min(1, (performance.now() - enteredAt.current) / ARRIVAL_MS)
  }, [reducedMotion])

  const nodeCanvasObject = useCallback(
    (node: SampleNode, context: CanvasRenderingContext2D, globalScale: number) => {
      const x = node.x ?? 0
      const y = node.y ?? 0
      const visible = !neighbourhood || neighbourhood.neighbours.has(node.id)
      const progress = arrival()
      context.save()
      context.globalAlpha = (visible ? 1 : 0.13) * progress
      if (node.part) {
        const radius = partRadius(node.part) * (0.65 + progress * 0.35)
        context.fillStyle = '#f2a25c'
        context.beginPath()
        context.arc(x, y, radius, 0, Math.PI * 2)
        context.fill()
        if (node.part.findings) {
          context.strokeStyle = '#f5d84a'
          context.lineWidth = 2 / globalScale
          context.beginPath()
          context.arc(x, y, radius + 3 / globalScale, 0, Math.PI * 2)
          context.stroke()
          context.fillStyle = '#f5d84a'
          context.beginPath()
          context.arc(x + radius, y - radius, 6 / globalScale, 0, Math.PI * 2)
          context.fill()
          context.fillStyle = '#001f24'
          context.font = `${8 / globalScale}px JetBrains Mono`
          context.textAlign = 'center'
          context.textBaseline = 'middle'
          context.fillText(String(node.part.findings), x + radius, y - radius)
        }
      } else {
        const side = 12
        context.fillStyle = '#424a52'
        context.fillRect(x - side / 2, y - side / 2, side, side)
      }
      // `/memory` hides labels below 0.7 zoom, because 35 of them at once is noise. Eight
      // are not, and the fit that keeps this sample inside a short box lands under 0.7 —
      // so keeping that gate here deleted every label instead of decluttering anything.
      // The text is sized in screen pixels either way, so it stays legible.
      context.fillStyle = '#dce4e5'
      context.font = `${11 / globalScale}px JetBrains Mono`
      context.textAlign = 'center'
      context.textBaseline = 'top'
      context.fillText(node.label, x, y + (nodeRadius(node) + 7) / globalScale)
      context.restore()
    },
    [arrival, neighbourhood],
  )

  const linkCanvasObject = useCallback(
    (link: SampleLink, context: CanvasRenderingContext2D, globalScale: number) => {
      const source = nodesById.get(endpointId(link.source)) ?? (link.source as SampleNode)
      const target = nodesById.get(endpointId(link.target)) ?? (link.target as SampleNode)
      if (!source || !target) return
      const visible = !neighbourhood || neighbourhood.links.has(link.id)
      const progress = arrival()
      context.save()
      context.globalAlpha = (visible ? 0.45 : 0.06) * progress
      context.strokeStyle = '#849396'
      context.lineWidth = 1 / globalScale
      // Dashed means "was on this board, then replaced". A solid edge would claim the part
      // is still in that BOM, which is the one thing this edge exists to say it is not.
      if (link.historical) context.setLineDash([4 / globalScale, 4 / globalScale])
      const fromX = source.x ?? 0
      const fromY = source.y ?? 0
      context.beginPath()
      context.moveTo(fromX, fromY)
      context.lineTo(
        fromX + ((target.x ?? 0) - fromX) * progress,
        fromY + ((target.y ?? 0) - fromY) * progress,
      )
      context.stroke()
      context.restore()
    },
    [arrival, neighbourhood],
  )

  const nodePointerAreaPaint = useCallback(
    (node: SampleNode, color: string, context: CanvasRenderingContext2D, globalScale: number) => {
      const x = node.x ?? 0
      const y = node.y ?? 0
      const radius = nodeRadius(node) + 5 / globalScale
      context.fillStyle = color
      context.beginPath()
      context.arc(x, y, radius, 0, Math.PI * 2)
      context.fill()
      context.fillRect(x - 58 / globalScale, y + radius, 116 / globalScale, 15 / globalScale)
    },
    [],
  )

  return (
    <div
      className="relative h-[330px] min-w-0 overflow-hidden border border-outline-variant/40 sm:h-[380px]"
      ref={host}
    >
      <div className="absolute inset-0">
        <ForceGraph2D
          // Transparent, so the page's own background reads through rather than a black
          // panel sitting on top of it.
          backgroundColor="rgba(0,0,0,0)"
          cooldownTicks={reducedMotion ? 0 : undefined}
          enableNodeDrag={!reducedMotion}
          enablePanInteraction
          // The one departure from `/memory`: the wheel belongs to the page here, so
          // scrolling past the section must not zoom the graph instead.
          enableZoomInteraction={false}
          graphData={graph}
          height={size.height}
          linkCanvasObject={linkCanvasObject}
          nodeCanvasObject={nodeCanvasObject}
          nodeCanvasObjectMode={() => 'replace'}
          nodePointerAreaPaint={nodePointerAreaPaint}
          // Re-framed on every tick rather than once at the end. The layout expands as it
          // settles, so a single fit — whenever it fired — left nodes outside the box.
          onEngineTick={() => {
            if (touched.current) return
            graphRef.current?.zoomToFit(0, FRAME_PADDING)
          }}
          onNodeDrag={() => {
            touched.current = true
          }}
          onNodeHover={(node: SampleNode | null) => setHovered(node)}
          ref={graphRef}
          width={size.width}
        />
      </div>
    </div>
  )
}
