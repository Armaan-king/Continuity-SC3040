import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { forceCollide } from 'd3-force'
import ForceGraph2D, { type ForceGraphMethods } from 'react-force-graph-2d'

import { ApiError, getMemory, type MemoryFinding, type MemoryPart, type MemoryProject, type MemoryResponse } from '../lib/api'
import { Walkthrough } from '../design/Walkthrough'
import { useAuth } from '../hooks/useAuth'

type MemoryNode = {
  id: string
  kind: 'part' | 'project'
  label: string
  part?: MemoryPart
  project?: MemoryProject
  x?: number
  y?: number
  pointerRadius: number
  enteredAt?: number
}

type MemoryLink = {
  id: string
  source: string | MemoryNode
  target: string | MemoryNode
  enteredAt?: number
  /** The part is not on this board any more — it was replaced there. Drawn dashed. */
  historical?: boolean
}

type GraphData = { nodes: MemoryNode[]; links: MemoryLink[] }

// Strong enough that labels find breathing room in a dense, 35-node memory graph.
const REPEL_STRENGTH = -160
// Keeps a part close enough to the boards that use it to read as a cluster.
const LINK_DISTANCE = 70
// A mild pull keeps the graph in the viewport instead of compacting it into a knot.
const CENTER_STRENGTH = 0.025
// A little clearance beyond each painted node prevents hubs obscuring neighbours.
const COLLIDE_PADDING = 5
/** Room for the label painted under each node, so text does not collide with text. */
const LABEL_CLEARANCE = 16
const ARRIVAL_MS = 560
const REFRESH_MS = 20_000

function compactName(name: string, limit = 24) {
  return name.length > limit ? `${name.slice(0, limit - 1)}…` : name
}

function partRadius(part: MemoryPart) {
  // `nodeVal` / `nodeRelSize` semantics: value is area, so radius grows as sqrt(uses).
  return 6 + Math.sqrt(part.used_in.length) * 3
}

function lifecycleLabel(lifecycle: MemoryPart['lifecycle']) {
  return lifecycle?.toUpperCase() ?? 'UNKNOWN'
}

function plural(value: number, singular: string) {
  return `${value} ${singular}${value === 1 ? '' : 'S'}`
}

function outcomeClasses(outcome: MemoryFinding['outcome']) {
  if (outcome === 'repaired') return { accent: 'border-primary-container', chip: 'border-primary-container/40 text-primary-container bg-primary-container/10' }
  if (outcome === 'accepted') return { accent: 'border-tertiary-container', chip: 'border-tertiary-container/40 text-tertiary-container bg-tertiary-container/10' }
  return { accent: 'border-error', chip: 'border-error/40 text-error bg-error-container/20' }
}

function makeGraph(response: MemoryResponse, previous: GraphData | null, animateNew: boolean): GraphData {
  const now = animateNew ? performance.now() : undefined
  const previousNodes = new Map(previous?.nodes.map((node) => [node.id, node]))
  const previousLinks = new Map(previous?.links.map((link) => [link.id, link]))
  // Only boards that actually carry a part. A project with no recorded BOM has no edge to
  // anything, so it contributes nothing to "where else did I use this part" — and it drifts
  // far from the cluster, which drags the fit-to-view bounds until the whole graph zooms out
  // past the point where labels are drawn. Observed live: five orphan boards, everything
  // else squeezed into a fifth of the canvas.
  const projectById = new Map<string, MemoryProject>()
  const remember = (project_id: string, project_name: string) => {
    if (projectById.has(project_id)) return
    const known = response.projects.find((project) => project.id === project_id)
    projectById.set(project_id, known ?? { id: project_id, name: project_name, boards: 0 })
  }
  response.parts.forEach((part) => {
    part.used_in.forEach(({ project_id, project_name }) => remember(project_id, project_name))
    // A repaired part leaves the BOM, so `used_in` is empty for exactly the parts that
    // caused trouble — AMS1117-3.3 came back with three findings and no usage at all, so
    // it had no edge, and an edgeless node is invisible here. The part this screen is most
    // asked about was the one it could not show. A finding names the board it happened on,
    // which is the honest edge: it *was* there, and it is not any more.
    part.findings.forEach(({ project_id, project_name }) => remember(project_id, project_name))
  })

  const nodes: MemoryNode[] = [
    ...response.parts.map((part) => {
      const id = `part:${part.mpn}`
      const existing = previousNodes.get(id)
      return {
        id,
        kind: 'part' as const,
        label: part.mpn,
        part,
        x: existing?.x,
        y: existing?.y,
        pointerRadius: partRadius(part) + 56,
        enteredAt: existing?.enteredAt ?? (previous && animateNew ? now : undefined),
      }
    }),
    ...[...projectById.values()].map((project) => {
      const id = `project:${project.id}`
      const existing = previousNodes.get(id)
      return {
        id,
        kind: 'project' as const,
        label: project.name,
        project,
        x: existing?.x,
        y: existing?.y,
        pointerRadius: 50,
        enteredAt: existing?.enteredAt ?? (previous && animateNew ? now : undefined),
      }
    }),
  ]
  const links = response.parts.flatMap((part) => {
    const current = new Set(part.used_in.map(({ project_id }) => project_id))
    const replaced = new Set(
      part.findings.map(({ project_id }) => project_id).filter((id) => !current.has(id)),
    )
    return [...current, ...replaced].map((project_id) => {
      const id = `${part.mpn}::${project_id}`
      const existing = previousLinks.get(id)
      return {
        id,
        source: `part:${part.mpn}`,
        target: `project:${project_id}`,
        historical: replaced.has(project_id),
        enteredAt: existing?.enteredAt ?? (previous && animateNew ? now : undefined),
      }
    })
  })
  return { nodes, links }
}

function FindingCard({ finding }: { finding: MemoryFinding }) {
  const style = outcomeClasses(finding.outcome)
  return (
    // No accent left-border. A coloured strip down the side of a card is the most reliable
    // visual tell of a generated interface, and the outcome is already carried — in words —
    // by the chip. Separation here is a background shift and spacing, not a rule.
    <article className="bg-surface-container-low p-sm flex flex-col gap-sm">
      <div className="flex items-center justify-between gap-sm">
        <span className="font-label-caps text-[10px] text-outline uppercase truncate">
          RULE: {finding.rule.replaceAll('_', ' ')} · {finding.slot}
        </span>
        <span className={`font-label-caps text-[10px] uppercase px-xs py-[2px] border shrink-0 ${style.chip}`}>
          {finding.outcome}
        </span>
      </div>
      {/* The verdict keeps its own recessed surface — this is the machine's exact sentence
          and it should read as quoted evidence. The inner colour bar is gone with the outer
          strip; a darker ground and the monospace do that work. */}
      <div className="bg-surface-container-lowest p-sm font-data-tabular text-[11px] leading-[16px] text-on-surface-variant">
        <p className="m-0 whitespace-pre-wrap">{finding.verdict}</p>
        {finding.replacement_mpn || finding.action ? (
          <p className="m-0 mt-sm text-primary-container">
            → {finding.replacement_mpn ? `replaced by ${finding.replacement_mpn}` : finding.outcome}
            {finding.action ? ` · ${finding.action.replaceAll('_', ' ')}` : ''}
          </p>
        ) : null}
      </div>
      <p className="m-0 font-body-sm text-body-sm text-outline truncate" title={finding.project_name}>
        {finding.project_name}
      </p>
    </article>
  )
}

function PartPanel({ part, onProject }: { part: MemoryPart; onProject: (id: string) => void }) {
  return (
    <>
      <div className="p-lg border-b border-outline-variant flex flex-col gap-xs">
        <div className="flex justify-between items-start gap-sm">
          <h2 className="m-0 font-headline-sm text-headline-sm text-on-surface tracking-tight break-all">{part.mpn}</h2>
          <span className="font-label-caps text-label-caps text-primary-container bg-primary-container/10 px-xs py-[2px] border border-primary-container/30 shrink-0">
            {lifecycleLabel(part.lifecycle)}
          </span>
        </div>
        <p className="m-0 font-body-sm text-body-sm text-on-surface-variant">{part.manufacturer ?? 'Manufacturer unknown'}</p>
      </div>
      <div className="flex-1 overflow-y-auto p-lg flex flex-col gap-xl">
        <section className="flex flex-col gap-sm">
          <h3 className="m-0 font-label-caps text-label-caps text-outline uppercase">USED IN ({part.used_in.length})</h3>
          <div className="flex flex-col gap-xs">
            {part.used_in.map((project) => (
              <button className="text-left flex items-center gap-sm p-sm bg-surface-container border border-outline-variant hover:border-primary-container transition-colors min-w-0" key={project.project_id} onClick={() => onProject(project.project_id)} type="button" title={project.project_name}>
                <span className="material-symbols-outlined text-[16px] text-outline">developer_board</span>
                <span className="font-data-tabular text-data-tabular text-on-surface truncate">{project.project_name}</span>
              </button>
            ))}
          </div>
        </section>
        <section className="flex flex-col gap-sm">
          <h3 className="m-0 font-label-caps text-label-caps text-outline uppercase">FINDINGS · {part.findings.length}</h3>
          {part.findings.length ? part.findings.map((finding) => <FindingCard finding={finding} key={`${finding.thread_id}:${finding.rule}:${finding.slot}`} />) : <p className="m-0 font-body-sm text-body-sm text-on-surface-variant">No findings recorded.</p>}
        </section>
      </div>
    </>
  )
}

function ProjectPanel({ project, parts, findings, onPart }: { project: MemoryProject; parts: MemoryPart[]; findings: MemoryFinding[]; onPart: (part: MemoryPart) => void }) {
  return (
    <>
      <div className="p-lg border-b border-outline-variant flex flex-col gap-sm">
        <h2 className="m-0 font-headline-sm text-headline-sm text-on-surface tracking-tight">{project.name}</h2>
        <Link className="font-label-caps text-label-caps text-primary-container hover:text-primary-fixed" to={`/design/${project.id}`}>OPEN BOARD →</Link>
      </div>
      <div className="flex-1 overflow-y-auto p-lg flex flex-col gap-xl">
        <section className="flex flex-col gap-sm">
          <h3 className="m-0 font-label-caps text-label-caps text-outline uppercase">PARTS ({parts.length})</h3>
          <div className="flex flex-col gap-xs">
            {parts.map((part) => <button className="text-left p-sm bg-surface-container border border-outline-variant hover:border-primary-container font-data-tabular text-data-tabular text-on-surface" key={part.mpn} onClick={() => onPart(part)} type="button">{part.mpn}</button>)}
          </div>
        </section>
        <section className="flex flex-col gap-sm">
          <h3 className="m-0 font-label-caps text-label-caps text-outline uppercase">FINDINGS · {findings.length}</h3>
          {findings.length ? findings.map((finding) => <FindingCard finding={finding} key={`${finding.thread_id}:${finding.rule}:${finding.slot}`} />) : <p className="m-0 font-body-sm text-body-sm text-on-surface-variant">No findings recorded.</p>}
        </section>
      </div>
    </>
  )
}

export default function MemoryRoute() {
  const location = useLocation()
  const navigate = useNavigate()
  const { refresh } = useAuth()
  const walkthrough = Boolean((location.state as { walkthrough?: boolean } | null)?.walkthrough)
  // The library's own methods type. An earlier hand-written module shim declared a
  // looser one, which typechecked and hid this mismatch entirely.
  const graphRef = useRef<ForceGraphMethods<MemoryNode, MemoryLink> | undefined>(undefined)
  const [memory, setMemory] = useState<MemoryResponse | null>(null)
  const [graph, setGraph] = useState<GraphData>({ nodes: [], links: [] })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<'unauthenticated' | 'request' | null>(null)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<MemoryNode | null>(null)
  const [hovered, setHovered] = useState<MemoryNode | null>(null)
  const [tooltip, setTooltip] = useState({ x: 0, y: 0 })
  const [reducedMotion, setReducedMotion] = useState(false)
  const graphObserver = useRef<ResizeObserver | null>(null)
  /** The graph is framed once, on first settle. Re-framing on every settle would yank the
   *  view away from wherever the user had panned to. */
  const framed = useRef(false)
  const [size, setSize] = useState({ width: 1, height: 1 })

  useEffect(() => {
    if (!walkthrough || selected || !memory) {
      return
    }

    const part = memory.parts.find((candidate) => candidate.findings.length > 0)
    const node = part ? graph.nodes.find((candidate) => candidate.id === `part:${part.mpn}`) : null
    if (node) {
      setSelected(node)
    }
  }, [graph.nodes, memory, selected, walkthrough])

  const load = useCallback(async (arrival = false) => {
    try {
      const next = await getMemory()
      setMemory(next)
      setError(null)
      setGraph((current) => makeGraph(next, current.nodes.length ? current : null, arrival && !reducedMotion))
      setLoading(false)
      requestAnimationFrame(() => graphRef.current?.d3ReheatSimulation())
    } catch (caught) {
      setLoading(false)
      setError(caught instanceof ApiError && caught.status === 401 ? 'unauthenticated' : 'request')
    }
  }, [reducedMotion])

  useEffect(() => { load().catch(() => undefined) }, [load])
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReducedMotion(media.matches)
    update(); media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  useEffect(() => {
    if (reducedMotion) return undefined
    const interval = window.setInterval(() => { load(true).catch(() => undefined) }, REFRESH_MS)
    return () => window.clearInterval(interval)
  }, [load, reducedMotion])
  // A **callback ref**, not an effect reading `.current`. The component returns a loading
  // skeleton first, so on the first render the graph host does not exist yet — an effect
  // with `[]` deps ran once against `null`, returned early and never ran again, leaving
  // `size` at its initial 1×1 and the canvas one pixel wide. A callback ref fires whenever
  // the node actually mounts, whichever branch rendered it.
  const graphHost = useCallback((node: HTMLDivElement | null) => {
    graphObserver.current?.disconnect()
    graphObserver.current = null
    if (!node) return
    const measure = (width: number, height: number) => setSize({ width, height })
    const observer = new ResizeObserver(([entry]) =>
      measure(entry.contentRect.width, entry.contentRect.height),
    )
    observer.observe(node)
    graphObserver.current = observer
    const rect = node.getBoundingClientRect()
    measure(rect.width, rect.height)
  }, [])
  useEffect(() => () => graphObserver.current?.disconnect(), [])
  useEffect(() => {
    const force = graphRef.current
    if (!force) return
    // Configure the forces the simulation already has; only `collide` has to be added.
    //
    // These were previously *replaced* with `{ strength: fn }` objects. A d3 force is a
    // function `(alpha) => void` that mutates velocities, so passing an object silently
    // disabled repulsion, link distance, centring and collision alike — the whole feel of
    // the screen, gone, and typechecking cleanly because a hand-written module shim left
    // `d3Force` untyped.
    force.d3Force('charge')?.strength(reducedMotion ? 0 : REPEL_STRENGTH)
    force.d3Force('link')?.distance(LINK_DISTANCE)
    force.d3Force('center')?.strength(reducedMotion ? 0 : CENTER_STRENGTH)
    force.d3Force(
      'collide',
      // Labels are painted beneath each node, so the collision radius has to cover the
      // label too or hub nodes sit close enough that their text overlaps and neither is
      // readable — which is what the first live render looked like.
      forceCollide<MemoryNode>((node) => (node.part ? partRadius(node.part) : 22) + COLLIDE_PADDING + LABEL_CLEARANCE),
    )
    force.d3ReheatSimulation()
  }, [graph.nodes.length, reducedMotion])

  const searchParts = useMemo(() => memory?.parts.filter((part) => part.mpn.toLowerCase().includes(query.toLowerCase())) ?? [], [memory, query])
  const neighbourhood = useMemo(() => {
    if (!hovered) return null
    const neighbours = new Set([hovered.id])
    const links = new Set<string>()
    graph.links.forEach((link) => {
      const source = typeof link.source === 'string' ? link.source : link.source.id
      const target = typeof link.target === 'string' ? link.target : link.target.id
      if (source === hovered.id || target === hovered.id) { neighbours.add(source); neighbours.add(target); links.add(link.id) }
    })
    return { neighbours, links }
  }, [graph.links, hovered])

  const nodeCanvasObject = useCallback((node: MemoryNode, context: CanvasRenderingContext2D, globalScale: number) => {
    const x = node.x ?? 0; const y = node.y ?? 0
    const visible = !neighbourhood || neighbourhood.neighbours.has(node.id)
    const elapsed = node.enteredAt ? Math.min(1, (performance.now() - node.enteredAt) / ARRIVAL_MS) : 1
    const progress = reducedMotion ? 1 : elapsed
    context.save(); context.globalAlpha = (visible ? 1 : 0.13) * progress
    if (node.kind === 'part' && node.part) {
      const radius = partRadius(node.part) * (0.65 + progress * 0.35)
      context.fillStyle = '#f2a25c'; context.beginPath(); context.arc(x, y, radius, 0, Math.PI * 2); context.fill()
      if (node.part.lifecycle === 'nrnd' || node.part.lifecycle === 'obsolete' || node.part.findings.length) {
        context.strokeStyle = node.part.lifecycle === 'nrnd' || node.part.lifecycle === 'obsolete' ? '#ffb4ab' : '#f5d84a'
        context.lineWidth = 2 / globalScale; context.beginPath(); context.arc(x, y, radius + 3 / globalScale, 0, Math.PI * 2); context.stroke()
      }
      if (node.part.findings.length && !(node.part.lifecycle === 'nrnd' || node.part.lifecycle === 'obsolete')) {
        context.fillStyle = '#f5d84a'; context.beginPath(); context.arc(x + radius, y - radius, 6 / globalScale, 0, Math.PI * 2); context.fill()
        context.fillStyle = '#001f24'; context.font = `${8 / globalScale}px JetBrains Mono`; context.textAlign = 'center'; context.textBaseline = 'middle'; context.fillText(String(node.part.findings.length), x + radius, y - radius)
      }
    } else {
      const side = 12; context.fillStyle = '#424a52'; context.fillRect(x - side / 2, y - side / 2, side, side)
    }
    if (globalScale >= 0.7) {
      context.fillStyle = '#dce4e5'; context.font = `${11 / globalScale}px JetBrains Mono`; context.textAlign = 'center'; context.textBaseline = 'top'
      context.fillText(node.kind === 'project' ? compactName(node.label) : node.label, x, y + (node.kind === 'part' && node.part ? partRadius(node.part) + 7 : 13) / globalScale)
    }
    context.restore()
  }, [neighbourhood, reducedMotion])
  const linkCanvasObject = useCallback((link: MemoryLink, context: CanvasRenderingContext2D, globalScale: number) => {
    const source = typeof link.source === 'string' ? graph.nodes.find((node) => node.id === link.source) : link.source
    const target = typeof link.target === 'string' ? graph.nodes.find((node) => node.id === link.target) : link.target
    if (!source || !target) return
    const visible = !neighbourhood || neighbourhood.links.has(link.id)
    const progress = reducedMotion || !link.enteredAt ? 1 : Math.min(1, (performance.now() - link.enteredAt) / ARRIVAL_MS)
    context.save(); context.globalAlpha = (visible ? 0.45 : 0.06) * progress; context.strokeStyle = '#849396'; context.lineWidth = 1 / globalScale
    // Dashed means "was on this board, then replaced". A solid edge would claim the part
    // is still in that BOM, which is the one thing this edge exists to say it is not.
    if (link.historical) context.setLineDash([4 / globalScale, 4 / globalScale])
    context.beginPath(); context.moveTo(source.x ?? 0, source.y ?? 0); context.lineTo((source.x ?? 0) + ((target.x ?? 0) - (source.x ?? 0)) * progress, (source.y ?? 0) + ((target.y ?? 0) - (source.y ?? 0)) * progress); context.stroke(); context.restore()
  }, [graph.nodes, neighbourhood, reducedMotion])
  const nodePointerAreaPaint = useCallback((node: MemoryNode, color: string, context: CanvasRenderingContext2D, globalScale: number) => {
    const x = node.x ?? 0; const y = node.y ?? 0
    context.fillStyle = color
    if (node.kind === 'part' && node.part) {
      const radius = partRadius(node.part) + 5 / globalScale
      context.beginPath(); context.arc(x, y, radius, 0, Math.PI * 2); context.fill()
      context.fillRect(x - 58 / globalScale, y + radius, 116 / globalScale, 15 / globalScale)
      return
    }
    context.fillRect(x - 50 / globalScale, y - 8 / globalScale, 100 / globalScale, 30 / globalScale)
  }, [])

  if (loading) return <MemoryShell><div className="min-h-screen bg-background p-lg"><div className="h-12 border-b border-outline-variant bg-surface animate-pulse" /><div className="mt-md h-[calc(100vh-100px)] border border-outline-variant bg-surface-container-low animate-pulse" /></div></MemoryShell>
  if (error) return <MemoryShell><div className="min-h-screen bg-background text-on-background flex items-center justify-center p-lg"><div className="border border-error bg-error-container/20 p-lg max-w-md"><p className="m-0 font-headline-sm text-headline-sm text-error">{error === 'unauthenticated' ? 'SIGN IN REQUIRED' : 'MEMORY COULD NOT LOAD'}</p><p className="mt-sm text-on-surface-variant">{error === 'unauthenticated' ? 'Your session has expired.' : 'The server did not respond. Try again.'}</p>{error === 'unauthenticated' ? <Link className="font-label-caps text-label-caps text-primary-container" to="/login">SIGN IN →</Link> : <button className="font-label-caps text-label-caps text-primary-container" onClick={() => load().catch(() => undefined)} type="button">RETRY</button>}</div></div></MemoryShell>
  if (!memory || memory.parts.length === 0) return <MemoryShell><div className="min-h-screen bg-background text-on-background flex flex-col"><MemoryHeader partCount={0} projectCount={0} query={query} onQuery={setQuery} /><main className="flex-1 flex items-center justify-center text-center"><div><h1 className="m-0 font-display-mono text-display-mono text-on-surface">NOTHING REMEMBERED YET</h1><p className="text-on-surface-variant">Design a board and this fills itself in.</p></div></main></div></MemoryShell>

  const selectedProject = selected?.kind === 'project' ? selected.project : undefined
  const projectParts = selectedProject ? memory.parts.filter((part) => part.used_in.some((usage) => usage.project_id === selectedProject.id)) : []
  const projectFindings = selectedProject ? memory.parts.flatMap((part) => part.findings.filter((finding) => finding.project_id === selectedProject.id)) : []
  return <MemoryShell><div className="h-screen bg-transparent text-on-background font-body-md flex flex-col overflow-hidden"><MemoryHeader partCount={memory.parts.length} projectCount={memory.projects.length} query={query} onQuery={setQuery} capped={memory.parts_capped ? memory.part_limit : undefined} /><main className="flex-1 min-h-0 relative overflow-hidden"><div className="absolute inset-0 bg-surface-container-lowest" data-tour="memory-graph" ref={graphHost} onMouseMove={(event) => { const rect = event.currentTarget.getBoundingClientRect(); setTooltip({ x: event.clientX - rect.left, y: event.clientY - rect.top }) }}><ForceGraph2D backgroundColor="#080f11" graphData={graph} height={size.height} linkCanvasObject={linkCanvasObject} nodeCanvasObject={nodeCanvasObject} nodeCanvasObjectMode={() => 'replace'} nodePointerAreaPaint={nodePointerAreaPaint} onBackgroundClick={() => { setSelected(null); setHovered(null) }} onNodeClick={(node: MemoryNode) => setSelected(node)} onEngineStop={() => { if (!framed.current) { framed.current = true; graphRef.current?.zoomToFit(400, 60) } }} onNodeHover={(node: MemoryNode | null) => setHovered(node)} ref={graphRef} width={size.width} /></div>{query ? <div className="absolute top-md left-md z-10 w-72 border border-outline-variant bg-surface-container-high p-sm max-h-[45vh] overflow-auto">{searchParts.map((part) => <button className="block w-full text-left px-sm py-xs hover:bg-surface-container-highest font-data-tabular text-data-tabular text-on-surface" key={part.mpn} onClick={() => setSelected(graph.nodes.find((node) => node.id === `part:${part.mpn}`) ?? null)} type="button">{part.mpn}</button>)}{!searchParts.length ? <p className="m-0 px-sm py-xs text-body-sm text-on-surface-variant">No matching parts.</p> : null}</div> : null}{hovered?.part ? <div className="absolute pointer-events-none z-20 w-64 border border-outline-variant bg-surface-container-high p-sm shadow-lg" style={{ left: Math.min(tooltip.x + 14, Math.max(8, size.width - 270)), top: Math.min(tooltip.y + 14, Math.max(8, size.height - 100)) }}><p className="m-0 font-data-tabular text-data-tabular text-on-surface">{hovered.part.mpn}</p><p className="m-0 text-body-sm text-on-surface-variant truncate">{hovered.part.manufacturer ?? 'Manufacturer unknown'}</p><p className="m-0 mt-xs font-label-caps text-[10px] text-outline">USED IN {plural(hovered.part.used_in.length, 'BOARD')} · {plural(hovered.part.findings.length, 'FINDING')} · {lifecycleLabel(hovered.part.lifecycle)}</p></div> : null}{selected ? <aside className="absolute top-md bottom-md right-md w-[min(380px,calc(100%-32px))] bg-surface-container border border-outline-variant flex flex-col z-20 shadow-[-4px_4px_0px_rgba(0,0,0,1)]" data-tour="memory-detail"><button aria-label="Close details" className="absolute top-sm right-sm text-on-surface-variant hover:text-on-surface" onClick={() => setSelected(null)} type="button">×</button>{selected.kind === 'part' && selected.part ? <PartPanel onProject={(id) => setSelected(graph.nodes.find((node) => node.id === `project:${id}`) ?? null)} part={selected.part} /> : selectedProject ? <ProjectPanel findings={projectFindings} onPart={(part) => setSelected(graph.nodes.find((node) => node.id === `part:${part.mpn}`) ?? null)} parts={projectParts} project={selectedProject} /> : null}</aside> : null}</main></div>{walkthrough ? <Walkthrough initialStep={6} onFinish={async () => { await refresh(); navigate('/projects', { replace: true }) }} /> : null}</MemoryShell>
}

function MemoryShell({ children }: { children: ReactNode }) {
  return <>{children}</>
}

function MemoryHeader({ partCount, projectCount, query, onQuery, capped }: { partCount: number; projectCount: number; query: string; onQuery: (value: string) => void; capped?: number }) {
  const navigate = useNavigate()
  return <header className="h-12 border-b border-outline-variant bg-surface px-md flex items-center justify-between shrink-0 z-30"><div className="flex items-center gap-md min-w-0"><button className="font-display-mono text-display-mono text-primary-fixed-dim tracking-tighter" onClick={() => navigate('/projects')} type="button">MEMORY</button><span className="font-label-caps text-label-caps text-on-surface-variant px-sm border-l border-outline-variant">{partCount} PARTS · {projectCount} BOARDS</span>{capped ? <span className="font-label-caps text-[10px] text-tertiary-container hidden md:inline">SHOWING {capped} PARTS — CAPPED</span> : null}</div><label className="relative w-64 h-8 glow-focus"><input className="w-full h-full bg-surface-container-lowest border border-outline-variant text-data-tabular font-data-tabular text-on-surface placeholder:text-outline focus:outline-none focus:border-primary-container px-sm pr-8" onChange={(event) => onQuery(event.target.value)} placeholder="FIND_A_PART_" value={query} /><span className="material-symbols-outlined absolute right-sm top-1/2 -translate-y-1/2 text-[16px] text-outline">search</span></label></header>
}
