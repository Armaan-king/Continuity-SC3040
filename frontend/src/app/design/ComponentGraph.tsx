import type { Alternative, ConflictEvent, RepairAction, Slot, SupplyNode } from '../lib/types'
import { ComponentNode } from './ComponentNode'
import type { PositionedNode } from './ComponentGraph.shared'
import {
  SUPPLY_NODE_ID,
  TIER_ORDER,
  VIEW_HEIGHT,
  VIEW_WIDTH,
  buildGraphLayout,
  clamp,
  cubicPoint,
  resolveBadgeY,
  tierLabel,
} from './ComponentGraph.shared'
import { RepairCallout } from './RepairCallout'

type SessionConflict =
  | (ConflictEvent & {
      alternatives: Alternative[]
      target_slot?: string | null
      beat?: number
      repair_action?: RepairAction | null
      repair_slot?: string | null
    })
  | null

type ActiveRepair = {
  seq: number
  slot: string
  action: RepairAction
  rationale: string
} | null

type ComponentGraphProps = {
  slots: Slot[]
  animateEdges: boolean
  revealedSlotIds: ReadonlySet<string>
  animatedSlotIds: ReadonlySet<string>
  edges: Array<{
    id: string
    from: string
    to: string
    label: string | null
    kind: 'power' | 'data'
    status: 'pending' | 'pass' | 'conflict' | 'unchecked'
  }>
  conflict: SessionConflict
  activeRepair: ActiveRepair
  slotConflictVariant: Record<string, 'entry' | 'repeat' | 'warmup'>
  onReleaseRepairHold: (slot: string) => void
  /** The board input. Absent on runs recorded before it was part of the contract. */
  supply?: SupplyNode | null
}

function nodeMpn(slot: Slot) {
  if ((slot.status === 'pass' || slot.status === 'conflict') && slot.part?.mpn) {
    return slot.part.mpn
  }

  return undefined
}

function nodeSubtitle(slot: Slot) {
  if (slot.status === 'searching') {
    return slot.part?.category ?? 'Evaluating candidates'
  }

  if (slot.status === 'pending') {
    return 'Pending'
  }

  return slot.part?.category
}

/**
 * Where an edge leaves from. A part-to-part edge leaves the source node's own centre; an
 * edge off the input bus leaves the bus at its *target's* height, so a board driven
 * straight from the supply reads as a bus rather than as a fan out of one point.
 */
function edgeOrigin(fromNode: PositionedNode | undefined, toNode: PositionedNode, busX: number | null) {
  if (fromNode) {
    const leftToRight = fromNode.cx <= toNode.cx
    return {
      x: leftToRight ? fromNode.x + fromNode.width : fromNode.x,
      y: fromNode.cy,
      leftToRight,
    }
  }

  return busX === null ? null : { x: busX, y: toNode.cy, leftToRight: true }
}

function availabilityBadge(slot: Slot, conflict: SessionConflict) {
  if (!conflict || conflict.rule !== 'availability') {
    return null
  }

  const isTarget = (conflict.target_slot ?? conflict.evidence[0]?.slot) === slot.id
  if (!isTarget || slot.status !== 'conflict') {
    return null
  }

  const evidenceValue = conflict.evidence.find((item) => item.slot === slot.id)?.value
  return evidenceValue ?? '0 in stock'
}

export function ComponentGraph({
  slots,
  animateEdges,
  revealedSlotIds,
  animatedSlotIds,
  edges,
  conflict,
  activeRepair,
  slotConflictVariant,
  onReleaseRepairHold,
  supply = null,
}: ComponentGraphProps) {
  const { positionedNodes, nodesById, nodeBandTop, columnWidth, left, supply: fullBar } =
    buildGraphLayout(slots, Boolean(supply))

  // The bus grows with the board rather than reaching its full height immediately: during
  // the one-at-a-time reveal a full-length bar next to a single node reads as a bar to
  // nowhere. It is also drawn only once something exists for it to feed.
  const revealedBottom = positionedNodes
    .filter((node) => revealedSlotIds.has(node.slot.id))
    .map((node) => node.y + node.height)
  const supplyBar =
    fullBar && revealedBottom.length > 0
      ? { ...fullBar, bottom: Math.max(fullBar.top + 24, ...revealedBottom) }
      : null
  const showSupply = supply !== null && supplyBar !== null

  return (
    <section className="flex-1 flex flex-col panel-border rounded-lg overflow-hidden min-w-[400px]" data-tour="graph">
      <header className="h-10 px-md flex items-center justify-between border-b border-outline-variant bg-surface-container-high flex-shrink-0 z-10">
        <div className="flex items-center gap-sm text-on-surface">
          <span className="material-symbols-outlined text-[16px]">account_tree</span>
          <h2 className="font-headline-sm text-[14px] font-semibold tracking-wide">Component Logic Graph</h2>
        </div>
      </header>

      <RepairCallout onRelease={onReleaseRepairHold} repair={activeRepair} />

      {/* No zoom, pan or scale readout. There were three buttons with no handlers and a
          `SCALE: 100% / X: 142 Y: -54` corner readout that was two hardcoded strings — an
          instrument reading that measured nothing, in a product whose whole claim is that
          no number reaches the screen without a source. `cursor-move` went with them: it
          promised a drag that was never implemented. Removed rather than wired, because
          zoom and pan are a feature that does not exist yet, not a disconnected handler. */}
      <div className="flex-1 bg-grid relative overflow-hidden">

        <div className="absolute top-sm right-sm bg-[#16181D] border border-outline-variant rounded p-sm flex flex-col gap-1 z-10 shadow-lg">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-pill bg-[#4ade80]"></span>
            <span className="font-data-tabular text-[9px] text-on-surface">Valid</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-pill bg-error"></span>
            <span className="font-data-tabular text-[9px] text-on-surface">Conflict</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-pill bg-outline-variant"></span>
            <span className="font-data-tabular text-[9px] text-on-surface">Pending</span>
          </div>
        </div>

        {/* `meet`, never `none`. `none` stretches the 900×820 layout to whatever box it
            is given, so the landing hero — 642×388 — scaled x by 0.71 and y by 0.47 and
            drew the whole board at two thirds of its proper height: flattened nodes,
            squashed text, edges leaving at the wrong angles. Fitting without distortion
            costs a little letterboxing when the container's aspect differs, which is the
            right trade for a graph whose geometry is the product. */}
        <svg
          className="w-full h-full absolute inset-0"
          preserveAspectRatio="xMidYMid meet"
          viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        >
          {TIER_ORDER.slice(1).map((_, index) => {
            const x = left + (index + 1) * columnWidth
            return (
              <line
                className="stroke-outline-variant/30 stroke-1 stroke-dasharray-[2,4]"
                key={`divider-${x}`}
                x1={x}
                x2={x}
                y1="0"
                y2={VIEW_HEIGHT}
              ></line>
            )
          })}

          {TIER_ORDER.map((tier, index) => {
            const x = left + index * columnWidth + columnWidth / 2
            return (
              <text
                className="font-label-caps text-[10px] fill-on-surface-variant opacity-60"
                key={`tier-${tier}`}
                textAnchor="middle"
                x={x}
                y={nodeBandTop}
              >
                {tierLabel(tier)}
              </text>
            )
          })}

          {showSupply && supplyBar ? (
            <g className="graph-supply">
              <title>{supply.label}</title>
              <text
                className="font-label-caps text-[10px] fill-on-surface-variant opacity-60"
                textAnchor="middle"
                x={supplyBar.cx}
                y={nodeBandTop}
              >
                INPUT
              </text>
              <rect
                className="fill-outline-variant"
                height={Math.max(supplyBar.bottom - supplyBar.top, 2)}
                rx="3"
                width={supplyBar.width}
                x={supplyBar.x}
                y={supplyBar.top}
              />
              <text
                className="font-data-tabular text-[10px] fill-on-surface-variant"
                textAnchor="middle"
                x={supplyBar.cx}
                y={supplyBar.top - 8}
              >
                {`${supply.voltage} V`}
              </text>
            </g>
          ) : null}

          {edges.map((edge, edgeIndex) => {
            // The supply is a node, not a slot, so it is never in `revealedSlotIds` — it
            // appears with the first part it feeds.
            const fromSupply = edge.from === SUPPLY_NODE_ID
            if (
              edge.status === 'pending' ||
              (fromSupply ? !showSupply : !revealedSlotIds.has(edge.from)) ||
              !revealedSlotIds.has(edge.to)
            ) {
              return null
            }

            const toNode = nodesById.get(edge.to)
            if (!toNode) {
              return null
            }

            const origin = edgeOrigin(
              nodesById.get(edge.from),
              toNode,
              fromSupply && supplyBar ? supplyBar.x + supplyBar.width : null,
            )

            if (!origin) {
              return null
            }

            const { leftToRight, ...start } = origin
            const end = {
              x: leftToRight ? toNode.x : toNode.x + toNode.width,
              y: toNode.cy,
            }

            const bend = clamp(Math.abs(end.x - start.x) * 0.35, 40, 120)
            const c1 = { x: start.x + (leftToRight ? bend : -bend), y: start.y }
            const c2 = { x: end.x - (leftToRight ? bend : -bend), y: end.y }
            const mid = cubicPoint(0.5, start, c1, c2, end)

            const laneOffset = ((edgeIndex % 3) - 1) * 10
            const labelText = edge.label
            const labelWidth = labelText ? clamp(labelText.length * 6 + 14, 28, 110) : 0
            // `conflict.edge` names the *net* — normally a rail id like "3V3" — and the
            // whole net is implicated. Comparing it to `edge.id` could never match, since
            // ids are `pwr-{slot}`; only the backend's status patch was highlighting
            // anything. Power edges carry the rail id as their label, so matching that
            // lights every wire on the net instead of one of them.
            // An unchecked stub connects a part the plan put on no rail. It is never a
            // conflict — no rule ran on it — and it must not borrow a rail's highlight.
            const isUnchecked = edge.status === 'unchecked'
            const isConflictEdge =
              !isUnchecked &&
              (edge.status === 'conflict' ||
                (conflict?.edge != null &&
                  (conflict.edge === edge.id || conflict.edge === edge.label)))

            const labelCenterX = labelText
              ? clamp(mid.x, 12 + labelWidth / 2, VIEW_WIDTH - 12 - labelWidth / 2)
              : null
            const labelY = mid.y - 10 + laneOffset
            const badgeText = isConflictEdge ? '⚠' : null
            const badgeWidth = 24
            const badgeHeight = 14
            const badgeCenterX = clamp(mid.x, 12 + badgeWidth / 2, VIEW_WIDTH - 12 - badgeWidth / 2)
            const badgeY = resolveBadgeY(mid.y + 24 + laneOffset, badgeCenterX, badgeWidth, badgeHeight, positionedNodes)

            return (
              <g key={edge.id}>
                {isUnchecked ? <title>Not on a modelled rail — no rule ran on this connection.</title> : null}
                <path
                  className={
                    isUnchecked
                      ? `edge edge-unchecked${animateEdges ? ' edge-reveal' : ''}`
                      : isConflictEdge
                        ? `edge edge-conflict${animateEdges ? ' edge-reveal' : ''}`
                        : `edge${animateEdges ? ' edge-reveal' : ''}`
                  }
                  d={`M ${start.x} ${start.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${end.x} ${end.y}`}
                  pathLength="1"
                ></path>

                {labelText && labelCenterX !== null ? (
                  <>
                    <rect
                      className="badge-bg"
                      height="14"
                      width={labelWidth}
                      x={labelCenterX - labelWidth / 2}
                      y={labelY - 7}
                    ></rect>
                    <text className="badge-text" dominantBaseline="middle" textAnchor="middle" x={labelCenterX} y={labelY}>
                      {labelText}
                    </text>
                  </>
                ) : null}

                {badgeText ? (
                  <>
                    <rect
                      className="badge-error-bg"
                      height="14"
                      width={badgeWidth}
                      x={badgeCenterX - badgeWidth / 2}
                      y={badgeY - 7}
                    ></rect>
                    <text className="badge-error-text" dominantBaseline="middle" textAnchor="middle" x={badgeCenterX} y={badgeY}>
                      {badgeText}
                    </text>
                  </>
                ) : null}
              </g>
            )
          })}

          {positionedNodes
            .filter((node) => revealedSlotIds.has(node.slot.id))
            .map((node) => (
            <ComponentNode
              categoryEmphasis={Boolean(node.slot.part?.category?.toLowerCase().includes('buck'))}
              conflictVariant={slotConflictVariant[node.slot.id]}
              height={node.height}
              key={node.slot.id}
              mpn={nodeMpn(node.slot)}
              reveal={animatedSlotIds.has(node.slot.id)}
              status={node.slot.status}
              stockBadge={availabilityBadge(node.slot, conflict)}
              subtitle={nodeSubtitle(node.slot)}
              title={node.slot.label.toUpperCase()}
              width={node.width}
              x={node.x}
              y={node.y}
            />
            ))}
        </svg>
      </div>
    </section>
  )
}
