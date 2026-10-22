import { API_BASE, responseError } from './config'

export type Counts = { pass: number; fail: number; unknown: number; accepted: number }
export type RevisionSummary = { id: string; thread_id: string; parent_id: string | null; reason: string; created_at: string; counts: Counts; total: number; active: boolean }
export type Finding = { id: string; rule: string; subject: string; scope: string | null; outcome: string; severity: string; accepted: boolean; detail: string; evidence: { field: string; value: string; source: string | null }[] }
export type Requirement = { field: string; value: string | number | number[] | null; basis: string }
export type Revision = RevisionSummary & {
  project_id: string
  content_hash: string
  document: {
    schema_version: number; ruleset_version: string; origin: string; prompt: string
    board: { slots: Record<string, { label: string }>; rails: Record<string, { voltage: number; source: string | null; members: string[] }>; signal_nets?: { id: string; protocol: string; ports: { component: string; role: string }[] }[] }
    requirements: Requirement[]
    report: { findings: Finding[]; counts: Counts; coverage: { rule: string; outcome: string; note: string }[]; limitations: string[] }
    bom: { total: number; currency: string }
    evidence: { slot: string; mpn: string; manufacturer: string; facts: Record<string, { value: unknown; original: string; source: string; method: string }>; documents: { url: string; title: string; text: string; retrieved_at: string; verification: string; question: string }[] }[]
  }
}
export type Comparison = { connections_changed: boolean; components: { slot: string; before: string[] | null; after: string[] | null }[]; requirements: { field: string; before: unknown; after: unknown }[]; cost_delta: number; before: Counts; after: Counts }

async function call<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { credentials: 'include', ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) })
  if (!response.ok) throw new Error(await responseError(response))
  return response.json() as Promise<T>
}

export const listRevisions = (project: string) => call<{ revisions: RevisionSummary[] }>(`/projects/${encodeURIComponent(project)}/revisions`)
export const getRevision = (id: string) => call<Revision>(`/revisions/${encodeURIComponent(id)}`)
export const compareRevisions = (before: string, after: string) => call<Comparison>(`/revisions/${encodeURIComponent(before)}/compare/${encodeURIComponent(after)}`)
export const updateRequirements = (id: string, expected: string, changes: Record<string, unknown>) => call<{ revision_id: string }>(`/revisions/${encodeURIComponent(id)}/requirements`, { expected_revision: expected, ...changes })
export const restoreRevision = (id: string, expected: string) => call<{ revision_id: string }>(`/revisions/${encodeURIComponent(id)}/restore`, { expected_revision: expected })
export type NetInput = { id: string; protocol: string; controller: string; peripherals: string[] }
export const updateConnections = (id: string, nets: NetInput[], confirmPower: boolean) => call<{ revision_id: string }>(`/revisions/${encodeURIComponent(id)}/connections`, { expected_revision: id, nets, confirm_power: confirmPower })
export const revisionExport = (id: string, kind: 'report.json' | 'bom.csv') => `${API_BASE}/revisions/${encodeURIComponent(id)}/${kind}`
