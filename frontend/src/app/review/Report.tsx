import { useState } from 'react'
import { Link } from 'react-router'
import { updateRequirements, type Revision, type ReviewQuestion } from '../lib/revisions'

const panel = 'rounded-xl border border-outline-variant bg-surface-container-low'
const button = 'rounded-md border border-outline-variant px-4 py-2 text-body-sm transition-colors hover:border-primary-container hover:text-primary-container disabled:opacity-40'
const words = (value: string) => value.replaceAll('_', ' ')
const safeSource = (value: string | null) => /^https?:\/\//.test(value ?? '') ? value! : undefined
const colors: Record<string, string> = { PASS: 'text-primary-container', FAIL: 'text-error', UNKNOWN: 'text-tertiary-container' }
const icons: Record<string, string> = { PASS: 'check_circle', FAIL: 'cancel', UNKNOWN: 'help' }
const Icon = ({ name }: { name: string }) => <span aria-hidden="true" className="material-symbols-outlined text-[20px]">{name}</span>

export default function Report({ revision, active, onSaved, onRequirements }: {
  revision: Revision; active: string; onSaved: (id: string) => Promise<void>; onRequirements: () => void
}) {
  const { report, evidence, board } = revision.document
  const guidance = revision.guidance
  const [filter, setFilter] = useState('attention')
  const [search, setSearch] = useState('')
  const counts = report.counts
  const total = counts.pass + counts.fail + counts.unknown
  const attention = counts.fail + counts.unknown
  const selected = report.findings.filter(f => (filter === 'all' || (filter === 'attention' ? !attention || f.outcome !== 'PASS' : f.outcome === filter))
    && `${f.subject} ${board.slots[f.subject]?.label ?? ''} ${words(f.rule)} ${f.detail}`.toLowerCase().includes(search.toLowerCase()))
  const subjects = [...new Set(selected.map(f => f.subject))]
  const status = counts.fail ? 'Conflicts to resolve' : guidance?.status === 'needs_review' || counts.unknown ? 'Review still needed' : 'Model checks complete'
  const tasks = guidance?.tasks ?? []
  return <>
    <section className={`${panel} overflow-hidden`} aria-label="Validation counts">
      <div className="grid-bg grid gap-8 p-6 md:grid-cols-[1fr_auto] md:p-8">
        <div>
          <p className="font-label-caps text-label-caps uppercase tracking-widest text-primary-container">Design assurance / saved revision</p>
          <h2 className="mt-3 text-3xl font-headline-sm">{status}</h2>
          <p className="mt-3 max-w-2xl text-body-sm leading-relaxed text-on-surface-variant">{guidance?.note ?? 'These results apply to this saved model. Unknown checks and accepted failures remain visible.'}</p>
          <div className="mt-5 flex flex-wrap gap-2 text-xs text-on-surface-variant"><span className="rounded-full border border-outline-variant bg-surface px-3 py-1">{Object.keys(board.slots).length} components</span><span className="rounded-full border border-outline-variant bg-surface px-3 py-1">{total} reported checks</span><span className="rounded-full border border-outline-variant bg-surface px-3 py-1">{revision.document.ruleset_version}</span></div>
        </div>
        <div className="flex items-center gap-4 md:justify-center">
          <svg width="128" height="128" viewBox="0 0 128 128" aria-hidden="true">
            <circle cx="64" cy="64" r="52" fill="none" stroke="currentColor" className="text-outline-variant" strokeWidth="9" />
            {total > 0 && <g transform="rotate(-90 64 64)">{[
              { count: counts.pass, offset: 0, color: 'text-primary-container' },
              { count: counts.unknown, offset: counts.pass, color: 'text-tertiary-container' },
              { count: counts.fail, offset: counts.pass + counts.unknown, color: 'text-error' },
            ].map((item, i) => <circle key={i} cx="64" cy="64" r="52" fill="none" stroke="currentColor" strokeWidth="9" pathLength="100" className={item.color} strokeDasharray={`${item.count / total * 100} 100`} strokeDashoffset={-item.offset / total * 100} />)}</g>}
            <text x="64" y="62" textAnchor="middle" fill="currentColor" fontSize="28">{counts.unknown}</text><text x="64" y="82" textAnchor="middle" fill="currentColor" fontSize="10">UNKNOWN</text>
          </svg>
          <p className="max-w-28 text-xs leading-relaxed text-on-surface-variant">Check distribution, not a confidence score.</p>
        </div>
      </div>
      <div className="grid grid-cols-2 divide-outline-variant border-t border-outline-variant md:grid-cols-4 md:divide-x">{[
        ['fail', 'Conflicts', 'error'], ['unknown', 'Unknown checks', 'help'], ['pass', 'Passing checks', 'check_circle'], ['accepted', 'Accepted risks', 'flag'],
      ].map(([key, title, icon]) => <div key={key} className="flex items-center gap-3 p-5"><span className={key === 'fail' && counts.fail ? 'text-error' : 'text-on-surface-variant'}><Icon name={icon} /></span><div><strong className="text-2xl tabular-nums">{counts[key as keyof typeof counts]}</strong><p className="text-xs text-on-surface-variant">{title}</p></div></div>)}</div>
    </section>

    {!!guidance?.questions.length && <section aria-labelledby="review-questions">
      <div className="mb-4 flex items-center justify-between gap-3"><div><p className="text-xs uppercase tracking-widest text-primary-container">01 / Your design intent</p><h2 id="review-questions" className="mt-1 text-xl">A few things only you can tell us</h2></div><span className="rounded-full border border-outline-variant px-3 py-1 text-xs">{guidance.questions.length} questions</span></div>
      <p className="mb-4 text-body-sm text-on-surface-variant">Answer what you know. Unanswered items stay open. Each confirmation saves a revalidated revision and returns you to the planning workspace.</p>
      <div className="grid gap-4 md:grid-cols-2">{guidance.questions.map(question => <QuestionCard key={`${revision.id}-${question.field}`} question={question} revision={revision} active={active} onSaved={onSaved} />)}</div>
    </section>}

    {!!tasks.length && <section aria-labelledby="review-actions">
      <p className="text-xs uppercase tracking-widest text-primary-container">02 / Next actions</p><h2 id="review-actions" className="mb-4 mt-1 text-xl">Close the evidence gaps</h2>
      <div className="grid gap-4 md:grid-cols-2">{tasks.map(task => <div key={task.id} className={`${panel} p-5 ${task.id === 'battery-modes' ? 'md:col-span-2 border-tertiary-container/40' : ''}`}>
        <div className="flex items-start gap-3"><span className="text-tertiary-container"><Icon name={task.kind === 'engineering' ? 'account_tree' : 'find_in_page'} /></span><div className="min-w-0 flex-1"><p className="text-xs uppercase tracking-wider text-on-surface-variant">{task.kind === 'engineering' ? 'Circuit review' : 'Source evidence needed'}</p><h3 className="mt-1 break-words">{task.title}</h3></div>{!!task.finding_ids.length && <span className="text-xs text-outline">{task.finding_ids.length} checks</span>}</div>
        <p className="mt-3 text-body-sm leading-relaxed text-on-surface-variant">{task.detail}</p>
        {!!task.fields.length && <div className="mt-3 flex flex-wrap gap-2">{task.fields.map(field => <span key={field} className="rounded bg-surface-container px-2 py-1 text-xs text-on-surface-variant">{field}</span>)}</div>}
        {task.steps && <details className="mt-3 text-body-sm"><summary className="cursor-pointer text-on-surface-variant">What to verify</summary><ul className="mt-2 list-disc space-y-2 pl-5 text-on-surface-variant">{task.steps.map(step => <li key={step}>{step}</li>)}</ul></details>}
        {task.action === 'connections' ? <button className={`${button} mt-4`} onClick={onRequirements}>Review requirements & connections</button> : task.action === 'workspace' ? <Link className={`${button} mt-4 inline-block`} to={`/design/${revision.project_id}`}>Inspect unresolved component</Link> : <a className={`${button} mt-4 inline-block`} href={`#evidence-${task.subjects[0]}`}>Inspect saved sources</a>}
      </div>)}</div>
    </section>}

    <section aria-labelledby="review-checks">
      <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="text-xs uppercase tracking-widest text-primary-container">03 / Validation ledger</p><h2 id="review-checks" className="mt-1 text-xl">Checks by component</h2></div><input className="input-field w-full sm:w-64" aria-label="Search checks" placeholder="Find a component or check…" value={search} onChange={e => setSearch(e.target.value)} /></div>
      <div className="my-4 flex flex-wrap gap-2" aria-label="Filter checks">{[['attention', 'Needs attention'], ['all', 'All checks'], ['UNKNOWN', 'Unknown'], ['FAIL', 'Failed'], ['PASS', 'Passed']].map(([value, text]) => <button key={value} aria-pressed={filter === value} className={`${button} ${filter === value ? 'border-primary-container text-primary-container bg-primary-container/5' : ''}`} onClick={() => setFilter(value)}>{text}</button>)}</div>
      <div className="space-y-4">{subjects.map(subject => <div key={subject} className={`${panel} overflow-hidden`}>
        <div className="flex justify-between gap-3 border-b border-outline-variant bg-surface-container px-5 py-3"><h3>{board.slots[subject]?.label ?? subject}</h3><span className="text-xs text-on-surface-variant">{subject}</span></div>
        {selected.filter(f => f.subject === subject).map(finding => <article key={finding.id} className="border-b border-outline-variant last:border-b-0 p-5">
          <div className="flex items-start justify-between gap-3"><h4 className="text-body-sm capitalize">{words(finding.rule)}{finding.scope && <span className="ml-2 rounded border border-outline-variant px-2 py-0.5 text-xs text-on-surface-variant">{finding.scope}</span>}</h4><span className={`flex items-center gap-1 text-xs ${colors[finding.outcome] ?? ''}`}><Icon name={icons[finding.outcome] ?? 'info'} />{finding.outcome}</span></div>
          <p className="mt-2 text-body-sm leading-relaxed text-on-surface-variant">{finding.detail}</p>
          {finding.accepted && <p className="mt-2 text-xs text-tertiary-container">Accepted by user · original result retained</p>}
          {!!finding.evidence.length && <details className="mt-3 text-body-sm"><summary className="cursor-pointer text-on-surface-variant">Supporting evidence · {finding.evidence.length}</summary><div className="mt-3 space-y-2 border-l-2 border-outline-variant pl-3">{finding.evidence.map((item, i) => <div key={i} className="break-words"><span>{item.field}: {item.value}</span>{safeSource(item.source) ? <a className="ml-2 text-primary-container underline" href={safeSource(item.source)} target="_blank" rel="noreferrer">Source ↗</a> : item.source && <p className="text-xs text-outline">{item.source}</p>}</div>)}</div></details>}
        </article>)}
      </div>)}</div>
      {!selected.length && <p className={`${panel} p-6 text-on-surface-variant`}>No checks match this filter.</p>}
    </section>

    <section aria-labelledby="review-evidence"><p className="text-xs uppercase tracking-widest text-primary-container">04 / Traceable sources</p><h2 id="review-evidence" className="mb-4 mt-1 text-xl">Evidence and research</h2>
      <div className="grid gap-4 md:grid-cols-2">{evidence.map(part => <div id={`evidence-${part.slot}`} key={part.slot} className={`${panel} scroll-mt-6 p-5 min-w-0`}>
        <div className="flex items-start gap-3"><span className="text-primary-container"><Icon name="memory" /></span><div className="min-w-0"><h3 className="break-words">{part.mpn}</h3><p className="text-xs text-on-surface-variant">{part.manufacturer} · {part.slot}</p></div></div>
        <div className="my-4 flex flex-wrap gap-3 text-xs"><span>{Object.keys(part.facts).length} verified fields</span><span className="text-on-surface-variant">{part.documents.length} research documents</span></div>
        <details><summary className="cursor-pointer text-body-sm text-primary-container">Inspect facts & documents</summary>
          {Object.entries(part.facts).map(([field, fact]) => <div key={field} className="mt-3 border-t border-outline-variant pt-3 text-body-sm break-words"><p>{words(field)}: {String(fact.value)}</p><p className="text-xs text-on-surface-variant">{fact.original}</p>{safeSource(fact.source) && <a className="text-xs underline text-primary-container" href={safeSource(fact.source)} target="_blank" rel="noreferrer">Supplier source ↗</a>}</div>)}
          {!part.documents.length && <p className="mt-4 text-body-sm text-on-surface-variant">No research document was saved. Missing specifications remain open.</p>}
          {part.documents.map((doc, index) => <details key={`${doc.url}-${index}`} className="mt-4 border-t border-outline-variant pt-3"><summary className="cursor-pointer break-words text-body-sm">{doc.title}</summary><p className="my-2 text-xs text-tertiary-container">Research lead · requires verification</p><p className="mb-2 text-xs text-on-surface-variant">Retrieved {new Date(doc.retrieved_at).toLocaleString()} · {doc.question}</p><a href={safeSource(doc.url)} target="_blank" rel="noreferrer" className="text-body-sm text-primary-container underline">Open document ↗</a><pre className="mt-3 whitespace-pre-wrap break-words text-xs max-h-64 overflow-y-auto">{doc.text}</pre></details>)}
        </details>
      </div>)}</div>
    </section>
    <section className={`${panel} p-6`}><div className="flex items-center gap-2"><Icon name="frame_inspect" /><h2>Validation scope</h2></div><div className="mt-3 grid gap-3 md:grid-cols-2">{report.limitations.map(item => <p key={item} className="text-body-sm leading-relaxed text-on-surface-variant">{item}</p>)}</div><div className="mt-4 flex flex-wrap gap-2">{report.coverage.filter(item => item.outcome === 'NOT_APPLICABLE').map(item => <span key={item.rule} title={item.note} className="rounded border border-outline-variant px-3 py-2 text-xs text-on-surface-variant">{words(item.rule)} · no check emitted</span>)}</div></section>
  </>
}

function QuestionCard({ question, revision, active, onSaved }: { question: ReviewQuestion; revision: Revision; active: string; onSaved: (id: string) => Promise<void> }) {
  const range = question.field === 'temp_range'
  // Present defaults as proposals that need explicit confirmation, never as answers.
  const [value, setValue] = useState(question.value === null ? '' : Array.isArray(question.value) ? String(question.value[0]) : String(question.value))
  const [upper, setUpper] = useState(Array.isArray(question.value) ? String(question.value[1]) : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const result = await updateRequirements(revision.id, active, { [question.field]: range ? [Number(value), Number(upper)] : Number(value) })
      await onSaved(result.revision_id)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save answer') }
    finally { setBusy(false) }
  }
  return <form aria-label={question.title} onSubmit={submit} className={`${panel} flex flex-col p-5`}>
    <div className="mb-3 flex items-center gap-2 text-xs text-tertiary-container"><Icon name="chat_bubble" />{question.value === null ? 'Your input needed' : `Confirm ${question.basis} value`}</div>
    <h3 className="text-body-md">{question.title}</h3><p className="mt-2 mb-4 flex-1 text-body-sm leading-relaxed text-on-surface-variant">{question.why}</p>
    <div className="flex flex-wrap items-center gap-2"><input required type="number" step={range ? '1' : 'any'} min={question.minimum ?? undefined} max={question.maximum ?? undefined} aria-label={range ? 'Minimum operating temperature' : question.title} className="input-field w-28 min-w-0 flex-1" value={value} onChange={e => setValue(e.target.value)} />{range && <><span className="text-xs">to</span><input required type="number" step="1" min={question.minimum ?? undefined} max={question.maximum ?? undefined} aria-label="Maximum operating temperature" className="input-field w-28 min-w-0 flex-1" value={upper} onChange={e => setUpper(e.target.value)} /></>}<span className="text-xs text-on-surface-variant">{question.unit}</span><button className={`${button} text-primary-container`} disabled={busy || revision.id !== active || !value.trim() || (range && !upper.trim())}>{busy ? 'Validating…' : 'Confirm & revalidate'}</button></div>
    {revision.id !== active && <p className="mt-2 text-xs text-outline">Select the current revision to answer.</p>}{error && <p role="alert" className="mt-2 text-body-sm text-error">{error}</p>}
  </form>
}
