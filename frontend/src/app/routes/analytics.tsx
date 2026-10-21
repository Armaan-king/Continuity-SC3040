import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router'

import { RecurringConflicts } from '../analytics/RecurringConflicts'
import { RepairEffectiveness } from '../analytics/RepairEffectiveness'
import { RuleOutcomes } from '../analytics/RuleOutcomes'
import { SupplyExposure } from '../analytics/SupplyExposure'
import { percent } from '../analytics/format'
import { ApiError, getAnalytics, type AnalyticsResponse } from '../lib/api'
import { Wordmark } from '../shell/Wordmark'

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; reason: 'unauthenticated' | 'request' }
  | { kind: 'ready'; report: AnalyticsResponse }

/**
 * UC-16 Review Validation Analytics. Read-only: nothing on this page changes a run, a
 * board or a finding, and every figure is recomputed by the server on each visit.
 */
export default function AnalyticsRoute() {
  const [state, setState] = useState<LoadState>({ kind: 'loading' })

  const load = useCallback(async () => {
    setState({ kind: 'loading' })
    try {
      setState({ kind: 'ready', report: await getAnalytics() })
    } catch (caught) {
      // UC-16.0.E.1 — say it could not load, and show none of it.
      setState({ kind: 'error', reason: caught instanceof ApiError && caught.status === 401 ? 'unauthenticated' : 'request' })
    }
  }, [])

  useEffect(() => {
    load().catch(() => undefined)
  }, [load])

  return (
    <div className="min-h-screen bg-transparent font-body-md text-on-background antialiased">
      <header className="flex h-12 w-full items-center bg-surface-container-low px-lg border-b border-outline-variant">
        <Wordmark />
      </header>
      <main className="mx-auto flex max-w-[1200px] flex-col gap-lg px-lg py-xl">
        <div className="flex flex-wrap items-end justify-between gap-sm border-b border-outline-variant pb-sm">
          <div>
            <h1 className="m-0 font-label-caps text-label-caps uppercase tracking-[0.1em] text-on-surface">VALIDATION ANALYTICS</h1>
            <p className="m-0 mt-xs text-body-sm text-outline">How the engine has behaved across your completed designs.</p>
          </div>
          {state.kind === 'ready' ? (
            <button
              className="flex items-center gap-xs font-label-caps text-label-caps text-primary-container hover:text-primary-fixed"
              onClick={() => load().catch(() => undefined)}
              type="button"
            >
              <span aria-hidden className="material-symbols-outlined text-[16px]">refresh</span>
              REFRESH
            </button>
          ) : null}
        </div>
        <Body onRetry={() => load().catch(() => undefined)} state={state} />
      </main>
    </div>
  )
}

function Body({ state, onRetry }: { state: LoadState; onRetry: () => void }) {
  if (state.kind === 'loading') {
    return (
      <div aria-busy="true" aria-label="Loading analysis" className="flex flex-col gap-lg">
        {[0, 1, 2].map((key) => (
          <div className="h-40 animate-pulse bg-surface-container-low" key={key} />
        ))}
      </div>
    )
  }

  if (state.kind === 'error') {
    const unauthenticated = state.reason === 'unauthenticated'
    return (
      <div className="max-w-md bg-error-container/20 p-lg" role="alert">
        <p className="m-0 font-headline-sm text-headline-sm text-error">
          {unauthenticated ? 'SIGN IN REQUIRED' : 'ANALYSIS COULD NOT LOAD'}
        </p>
        <p className="mt-sm text-on-surface-variant">
          {unauthenticated
            ? 'Your session has expired.'
            : 'The records it is computed from could not be read. Nothing has been changed. Try again.'}
        </p>
        {unauthenticated ? (
          <Link className="font-label-caps text-label-caps text-primary-container" to="/login">SIGN IN →</Link>
        ) : (
          <button className="font-label-caps text-label-caps text-primary-container" onClick={onRetry} type="button">RETRY</button>
        )}
      </div>
    )
  }

  const { report } = state
  if (report.runs.completed === 0) {
    return (
      <div className="flex flex-col items-center gap-sm bg-surface-container-low px-lg py-xl text-center" data-testid="analytics-empty">
        <h2 className="m-0 font-display-mono text-display-mono text-on-surface">NO COMPLETED RUNS YET</h2>
        <p className="m-0 max-w-[520px] text-on-surface-variant">
          The analysis is computed from designs that ran to completion. Finish a design and its rule outcomes, repairs and parts appear here.
        </p>
        <Link className="mt-sm font-label-caps text-label-caps text-primary-container" to="/projects">GO TO PROJECTS →</Link>
      </div>
    )
  }

  return (
    <>
      <Overview report={report} />
      <RuleOutcomes rules={report.rules} />
      <div className="grid grid-cols-1 gap-lg xl:grid-cols-2">
        <RepairEffectiveness repairs={report.repairs} />
        <SupplyExposure supply={report.supply} />
      </div>
      <RecurringConflicts signatures={report.signatures} />
    </>
  )
}

function Overview({ report }: { report: AnalyticsResponse }) {
  const { completed, excluded } = report.runs
  const evaluations = report.rules.reduce((sum, row) => sum + row.evaluations, 0)
  const unchecked = report.rules.reduce((sum, row) => sum + row.unchecked, 0)
  const withheld = report.rules.filter((row) => row.failure_rate.withheld !== undefined).length
  return (
    <section aria-label="Overview" className="grid grid-cols-2 gap-md lg:grid-cols-4" data-testid="analytics-overview">
      <Stat label="COMPLETED RUNS" value={completed}>
        <span>
          excluded: {excluded.abandoned} abandoned · {excluded.error} errored · {excluded.in_progress} in progress
        </span>
      </Stat>
      <Stat label="RULE EVALUATIONS" value={evaluations}>
        <span>{unchecked} could not be evaluated ({evaluations ? percent(unchecked / evaluations) : '0%'})</span>
      </Stat>
      <Stat label="REPAIRS APPLIED" value={report.repairs.reduce((sum, row) => sum + row.applied, 0)}>
        <span>{report.signatures.length} recurring conflict shapes</span>
      </Stat>
      <Stat label="RATES WITHHELD" value={withheld}>
        <span>a rate is withheld when over {percent(report.threshold)} of its evaluations are unchecked</span>
      </Stat>
    </section>
  )
}

function Stat({ label, value, children }: { label: string; value: number; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-xs bg-surface-container-low p-md">
      <span className="font-label-caps text-[10px] text-outline">{label}</span>
      <span className="font-display-mono text-display-mono text-on-surface">{value.toLocaleString()}</span>
      <span className="text-body-sm text-on-surface-variant">{children}</span>
    </div>
  )
}
