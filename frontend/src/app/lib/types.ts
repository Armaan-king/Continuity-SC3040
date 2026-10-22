export interface PartSpec {
  mpn: string
  manufacturer: string
  description: string
  category: string
  vmin: number | null
  vmax: number | null
  vout: number | null
  i_typ: number | null
  i_peak: number | null
  i_max: number | null
  interfaces: string[]
  role: 'master' | 'peripheral' | 'passive' | null
  pins_required: number | null
  pins_available: number | null
  package: string | null
  theta_ja: number | null
  topology: string | null
  efficiency: number | null
  temp_min: number | null
  temp_max: number | null
  unit_price: number | null
  currency: string
  stock: number | null
  distributor: string
  lifecycle: 'active' | 'nrnd' | 'obsolete' | 'unknown'
  lead_time_days: number | null
  datasheet: string | null
  product_url: string | null
  /** Verbatim distributor parameters. Server-side only — the evidence rows carry
   *  whatever a person actually needs to see, so shipping the full payload on every
   *  `candidate` frame would cost weight for nothing on screen. */
  raw?: Record<string, string>
}

export interface Requirements {
  temp_range: [number, number]
  current_margin: number
  max_package_mm: number | null
  input_source: string
  priority: 'cost' | 'size' | 'availability'
  ambient_c: number
  min_stock: number
  max_lead_days: number
}

export interface Slot {
  id: string
  label: string
  tier: 'core' | 'power' | 'peripherals' | 'passives'
  pinned: boolean
  status: 'pending' | 'searching' | 'pass' | 'conflict'
  part: PartSpec | null
  constraint: object | null
  repair_count: number
}

export interface Edge {
  id: string
  from: string
  to: string
  label: string | null
  kind: 'power' | 'data'
  /** `unchecked`: drawn so the part is not floating, but no rule ran on it. */
  status: 'pending' | 'pass' | 'conflict' | 'unchecked'
}

export type SessionStatus = 'idle' | 'running' | 'paused' | 'stopping' | 'stopped' | 'stop_failed' | 'done' | 'error'

export interface Evidence {
  slot: string
  field: string
  value: string
  source: string
}

export interface Alternative {
  mpn: string
  manufacturer: string
  unit_price: number | null
  currency: string
  stock: number | null
  lead_time_days: number | null
  reason: string
  recommended: boolean
  datasheet: string | null
}

export interface Verdict {
  rule: string
  status: 'pass' | 'warn' | 'fail'
  detail: string
  involved: string[]
  evidence: Evidence[]
}

export type CheckRule =
  | 'voltage_overlap'
  | 'interface_role_match'
  | 'pin_budget'
  | 'current_budget'
  | 'thermal_dissipation'
  | 'availability'
  | 'footprint'
  | 'temperature_rating'
  /** How long the board runs on its supply, against how long the brief asked for.
   *  Scoped to the board rather than a slot, and only present when a lifetime was
   *  stated. `warn` rather than `fail` when the continuous-draw bound falls short —
   *  duty cycling closes that gap and the engine does not model it. */
  | 'energy_budget'
  /** Reports a part the power tree never mentions, and which rail-based checks it
   *  therefore missed. Always `warn` — a gap in what is known, not a fault. */
  | 'rail_coverage'
  | 'data_integrity'
  | 'power_topology'

export type RepairAction =
  | 'swap'
  | 'change_topology'
  | 'add_part'
  | 'change_rail'
  | 'relax_requirement'
  | 'escalate'

export type EventStatus = 'pass' | 'warn' | 'fail'

export type EdgePatch = { id: string } & Partial<Omit<Edge, 'id'>>

type EventBase<TType extends string> = {
  type: TType
  seq: number
  thread_id: string
}

/**
 * The board input, drawn so that whatever it feeds is not left floating.
 *
 * It is a node and never a slot: nothing is sourced for it and it resolves to no part,
 * which is why it arrives in its own field rather than inside `slots`.
 */
export interface SupplyNode {
  id: string
  label: string
  voltage: number
}

export type PlanEvent = EventBase<'plan'> & {
  slots: Slot[]
  edges: Edge[]
  /** Absent on recordings made before the supply node existed. */
  supply?: SupplyNode | null
}

export type SlotAddedEvent = EventBase<'slot_added'> & {
  slot: Pick<Slot, 'id' | 'label' | 'tier' | 'pinned'>
  edges: Edge[]
}

export type ReasoningEvent = EventBase<'reasoning'> & {
  /** null for board-level narration that belongs to no single slot. */
  slot: string | null
  text: string
  level?: 'warning'
}

export type CandidateEvent = EventBase<'candidate'> & {
  slot: string
  part: PartSpec
}

export type CheckEvent = EventBase<'check'> & {
  slot: string
  rule: CheckRule
  /** The net this check is about — a rail id, or null for board-wide rules.
   *
   *  Checks are keyed by (rule, slot, scope), not by rule alone. A regulator draws
   *  from one rail and feeds another, so it produces two `current_budget` results;
   *  without scope one silently overwrites the other in the UI. */
  scope?: string | null
  status: EventStatus
  detail: string
  /** True when a figure the rule needs was unpublished, so it could not decide.
   *  Only ever set alongside `warn`. Absent on frames recorded before the field existed. */
  unchecked?: boolean
}

export type ConflictEvent = EventBase<'conflict'> & {
  rule: CheckRule
  involved: string[]
  /** The implicated net — a rail id such as "3V3", or an edge id. A rail conflict
   *  implicates every edge on that net rather than one of them. */
  edge: string | null
  message: string
  evidence: Evidence[]
  subject?: string
  edges?: EdgePatch[]
}

export type RepairEvent = EventBase<'repair'> & {
  slot: string
  action: RepairAction
  rationale: string
  constraint: Record<string, unknown>
  alternatives: Alternative[]
}

export type SelectionEvent = EventBase<'selection'> & {
  slot: string
  part: PartSpec
  status: 'pass' | 'conflict'
  edges?: EdgePatch[]
}

export type QuestionEvent = EventBase<'question'> & {
  question_id: string
  text: string
  suggestions: string[]
}

export interface BomRow {
  slot: string
  mpn: string
  manufacturer: string
  description: string
  qty: number
  unit_price: number | null
  currency: string
  stock: number | null
  distributor: string
  lead_time_days: number | null
  datasheet: string | null
  product_url: string | null
}

export type BomEvent = EventBase<'bom'> & {
  rows: BomRow[]
  total: number
  currency: string
}

export type BoardConflict = Omit<ConflictEvent, 'type' | 'seq' | 'thread_id'>

export type BoardEvent = EventBase<'board'> & {
  slots: Slot[]
  edges: Edge[]
  supply: SupplyNode | null
  conflicts: BoardConflict[]
}

export type DoneEvent = EventBase<'done'> & {
  summary: {
    slots: number
    /** What actually reached the BOM. Less than `slots` when a search found nothing —
     *  the two were once the same number, so a board that lost a slot reported complete. */
    placed?: number
    conflicts_resolved: number
    elapsed_s: number
    failures?: number
    warnings?: number
    unchecked?: number
    stopped?: boolean
  }
}

export type ErrorEvent = EventBase<'error'> & {
  message: string
  recoverable: boolean
  retry_question?: QuestionEvent
}

export type DesignEvent =
  | PlanEvent
  | SlotAddedEvent
  | ReasoningEvent
  | CandidateEvent
  | CheckEvent
  | ConflictEvent
  | RepairEvent
  | SelectionEvent
  | QuestionEvent
  | BomEvent
  | BoardEvent
  | DoneEvent
  | ErrorEvent
