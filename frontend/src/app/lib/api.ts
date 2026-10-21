import type { BomRow, DesignEvent, Edge, QuestionEvent, Slot, SupplyNode } from './types'

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export type PublicUser = {
  id: string
  email: string
  onboarded: boolean
}

export type Project = {
  id: string
  name: string
  created_at: string
  updated_at: string
}

export type ThreadSummary = {
  slots: number
  placed: number
  conflicts_resolved: number
  elapsed_s: number
}

export type ProjectThread = {
  id: string
  prompt: string
  status: string
  summary: ThreadSummary | null
}

export type ThreadBoard = {
  status: string
  summary: ThreadSummary | null
  slots: Slot[]
  edges: Edge[]
  /** Null when the restored board predates the supply node, or its source is unknown. */
  supply?: SupplyNode | null
  bom: {
    rows: BomRow[]
    total: number
    currency: string
  } | null
  checkpoint: 'available' | 'unavailable' | 'not_loaded'
  trace: DesignEvent[]
  question: QuestionEvent | null
  resumable: boolean
}

export type MemoryProject = {
  id: string
  name: string
  boards: number
}

export type MemoryFinding = {
  thread_id: string
  project_id: string
  project_name: string
  rule: string
  slot: string
  verdict: string
  outcome: 'repaired' | 'accepted' | 'unresolved'
  action: string | null
  replacement_mpn: string | null
}

export type MemoryPart = {
  mpn: string
  manufacturer: string | null
  lifecycle: 'active' | 'nrnd' | 'obsolete' | 'unknown' | null
  used_in: Array<{ project_id: string; project_name: string }>
  findings: MemoryFinding[]
}

export type MemoryResponse = {
  projects: MemoryProject[]
  parts: MemoryPart[]
  parts_capped: boolean
  part_limit: number
}

/** Every rate carries its counts. A withheld one has a reason and no `value` at all —
 *  never a null that could be drawn as zero (SRS 6.12, 7.6). */
export type Rate = {
  numerator: number
  denominator: number
  unchecked: number
} & ({ value: number; withheld?: never } | { withheld: string; value?: never })

export type RuleOutcome = {
  rule: string
  pass: number
  warn: number
  fail: number
  unchecked: number
  evaluations: number
  failure_rate: Rate
}

export type RepairOutcome = {
  action: string
  applied: number
  held: number
  not_held: number
  unchecked: number
  held_rate: Rate
}

export type RecurringConflict = {
  signature: string
  rule: string
  occurrences: number
  runs: number
  projects: number
  best_repair: { action: string; held: number; applied: number } | null
}

export type Lifecycle = 'active' | 'nrnd' | 'obsolete' | 'unknown'

export type SupplyExposure = {
  parts: number
  placements: number
  lifecycle: Record<Lifecycle, number>
  at_risk_rate: Rate
  single_source: { distributors: string[]; withheld: string }
}

export type AnalyticsResponse = {
  threshold: number
  runs: {
    completed: number
    excluded: { abandoned: number; error: number; in_progress: number }
  }
  rules: RuleOutcome[]
  repairs: RepairOutcome[]
  signatures: RecurringConflict[]
  supply: SupplyExposure
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message?: string) {
    super(message ?? `Request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
  }
}

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body } = options

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    credentials: 'include',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (!response.ok) {
    throw new ApiError(response.status)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

export function register(email: string, password: string) {
  return request<PublicUser>('/auth/register', {
    method: 'POST',
    body: { email, password },
  })
}

export function login(email: string, password: string) {
  return request<PublicUser>('/auth/login', {
    method: 'POST',
    body: { email, password },
  })
}

export function logout() {
  return request<void>('/auth/logout', {
    method: 'POST',
  })
}

export function me() {
  return request<PublicUser>('/auth/me')
}

export function listProjects() {
  return request<Project[]>('/projects')
}

export function getMemory() {
  return request<MemoryResponse>('/memory')
}

export function getAnalytics() {
  return request<AnalyticsResponse>('/analytics')
}

export function getThreadBoard(threadId: string) {
  return request<ThreadBoard>(`/threads/${encodeURIComponent(threadId)}/board`)
}

export function createProject(name: string) {
  return request<Project>('/projects', {
    method: 'POST',
    body: { name },
  })
}

export function getProject(projectId: string) {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}`)
}

export function listProjectThreads(projectId: string) {
  return request<ProjectThread[]>(`/projects/${encodeURIComponent(projectId)}/threads`)
}

export function updateProject(projectId: string, name: string) {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH',
    body: { name },
  })
}

export function deleteProject(projectId: string) {
  return request<void>(`/projects/${encodeURIComponent(projectId)}`, {
    method: 'DELETE',
  })
}
