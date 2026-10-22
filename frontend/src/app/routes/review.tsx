import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import { getProject } from '../lib/api'
import { compareRevisions, getRevision, listRevisions, restoreRevision, revisionExport, updateRequirements, updateConnections, type NetInput, type Comparison, type Revision, type RevisionSummary } from '../lib/revisions'

const button = 'rounded border border-outline-variant bg-surface-container-high px-md py-sm text-body-sm hover:text-primary-container disabled:opacity-40'
const panel = 'border border-outline-variant bg-surface-container-low rounded p-lg'
const editable = new Set(['input_voltage', 'ambient_c', 'current_margin', 'min_stock', 'max_lead_days', 'lifetime_hours', 'supply_capacity_mah', 'max_package_mm'])
const label = (value: string) => value.replaceAll('_', ' ')
const display = (value: unknown) => value === null || value === undefined ? 'Not specified' : Array.isArray(value) ? value.join(' to ') : String(value)
const safeSource = (value: string | null) => value?.startsWith('https://') ? value : undefined

export default function ReviewRoute() {
  const { projectId = '' } = useParams()
  const [name, setName] = useState('Design review')
  const [history, setHistory] = useState<RevisionSummary[]>([])
  const [selected, setSelected] = useState('')
  const [revision, setRevision] = useState<Revision | null>(null)
  const [tab, setTab] = useState<'report' | 'requirements' | 'history'>('report')
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [before, setBefore] = useState('')
  const active = history.find(item => item.active)?.id ?? ''

  const load = useCallback(async (choose?: string) => {
    const result = await listRevisions(projectId)
    setHistory(result.revisions)
    setSelected(choose ?? result.revisions.find(item => item.active)?.id ?? result.revisions[0]?.id ?? '')
    setLoaded(true)
  }, [projectId])

  useEffect(() => {
    let alive = true
    Promise.all([getProject(projectId), listRevisions(projectId)]).then(([project, result]) => {
      if (!alive) return
      setName(project.name); setHistory(result.revisions)
      setSelected(result.revisions.find(item => item.active)?.id ?? result.revisions[0]?.id ?? '')
      setLoaded(true)
    }).catch(e => { if (alive) { setError(String(e.message)); setLoaded(true) } })
    return () => { alive = false }
  }, [projectId])

  useEffect(() => {
    let alive = true
    setRevision(null); setComparison(null)
    if (selected) getRevision(selected).then(value => { if (alive) setRevision(value) }).catch(e => { if (alive) setError(e.message) })
    return () => { alive = false }
  }, [selected])

  useEffect(() => {
    let alive = true
    setComparison(null)
    if (before && selected && before !== selected) compareRevisions(before, selected).then(value => { if (alive) setComparison(value) }).catch(e => { if (alive) setError(e.message) })
    return () => { alive = false }
  }, [before, selected])

  async function restore() {
    setBusy(true); setError('')
    try { const result = await restoreRevision(selected, active); await load(result.revision_id) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not restore revision') }
    finally { setBusy(false) }
  }

  return <div className="h-full overflow-y-auto bg-background text-on-background font-body-md">
    <header className="flex flex-wrap items-center justify-between gap-md px-lg py-md border-b border-outline-variant bg-surface-container-low">
      <div><Link className="text-primary-container text-body-sm" to={`/design/${projectId}`}>← Back to workspace</Link><h1 className="font-headline-sm text-headline-sm mt-sm">{name}</h1></div>
      {revision && <div className="flex gap-sm"><a className={button} href={revisionExport(selected, 'report.json')}>Export report</a><a className={button} href={revisionExport(selected, 'bom.csv')}>Export BOM</a></div>}
    </header>
    <main className="max-w-[1200px] mx-auto p-lg flex flex-col gap-lg">
      <nav aria-label="Design review" className="flex gap-sm flex-wrap">{(['report', 'requirements', 'history'] as const).map(value => <button key={value} type="button" onClick={() => setTab(value)} className={`${button} ${tab === value ? 'text-primary-container border-primary-container' : ''}`}>{value === 'history' ? 'Revision history' : label(value)}</button>)}</nav>
      {error && <div role="alert" className="border border-error text-error p-md">{error} <button className="underline" onClick={() => { setError(''); load().catch(e => setError(e.message)) }}>Reload</button></div>}
      {!loaded && <p role="status">Loading saved designs…</p>}
      {loaded && history.length === 0 && <div className={panel}>This project has no versioned result yet. Its existing workspace and exports remain available. New completed runs include a validation report.</div>}
      {history.length > 0 && <label className="text-body-sm text-on-surface-variant">Viewing revision <select aria-label="Viewing revision" className={`${button} ml-sm max-w-full`} value={selected} onChange={e => setSelected(e.target.value)}>{history.map((item, i) => <option key={item.id} value={item.id}>{history.length - i} · {item.reason}{item.active ? ' · Current' : ''}</option>)}</select></label>}
      {revision && tab === 'report' && <>
        <section className={`${panel} flex flex-wrap gap-lg`} aria-label="Validation counts">{Object.entries(revision.document.report.counts).map(([key, value]) => <div key={key}><strong className="text-headline-sm">{value}</strong><span className="ml-sm text-on-surface-variant">{label(key)}</span></div>)}<p className="w-full m-0 text-body-sm text-outline">Results apply to this saved revision. Unknown checks and accepted failures remain visible.</p></section>
        {revision.document.report.findings.map(finding => <article key={finding.id} className={panel}>
          <div className="flex flex-wrap justify-between gap-sm"><strong className="uppercase text-label-caps">{label(finding.rule)} · {finding.subject}{finding.scope ? ` · ${finding.scope}` : ''}</strong><span className={finding.outcome === 'FAIL' ? 'text-error' : finding.outcome === 'UNKNOWN' ? 'text-tertiary-container' : 'text-primary-container'}>{finding.outcome}{finding.accepted ? ' · Accepted by user' : ''}</span></div>
          <p className="text-body-sm text-on-surface-variant">{finding.detail}</p>
          {finding.evidence.map((item, index) => <div key={index} className="border-t border-outline-variant py-sm text-body-sm"><span>{item.field}: {item.value}</span>{safeSource(item.source) ? <a className="ml-sm text-primary-container underline" href={item.source!} target="_blank" rel="noreferrer">Source</a> : item.source && <span className="ml-sm text-outline">{item.source}</span>}</div>)}
        </article>)}
        <section className={panel}><h2 className="text-label-caps uppercase">Evidence and research</h2>{revision.document.evidence.map(part => <details key={part.slot} className="py-md border-b border-outline-variant"><summary className="cursor-pointer">{part.mpn} · {Object.keys(part.facts).length} verified fields · {part.documents.length} research documents</summary>
          {Object.entries(part.facts).map(([field, fact]) => <p key={field} className="text-body-sm">{label(field)}: {display(fact.value)} <span className="text-outline">— {fact.original}</span></p>)}
          {part.documents.map((doc, index) => <div key={`${doc.url}-${index}`} className="mt-md p-md bg-surface-container"><a href={safeSource(doc.url)} target="_blank" rel="noreferrer" className="text-primary-container underline">{doc.title}</a><p className="text-body-sm text-outline">Retrieved {new Date(doc.retrieved_at).toLocaleString()} · Requires verification · Researching {doc.question}</p><pre className="whitespace-pre-wrap break-words text-body-sm max-h-64 overflow-y-auto">{doc.text}</pre></div>)}
        </details>)}</section>
        <section className={panel}><h2 className="text-label-caps uppercase">Validation scope</h2>{revision.document.report.limitations.map(item => <p key={item} className="text-body-sm text-on-surface-variant">{item}</p>)}{revision.document.report.coverage.filter(item => item.outcome === 'NOT_APPLICABLE').map(item => <p key={item.rule} className="text-body-sm text-outline">{label(item.rule)}: {item.note}</p>)}</section>
      </>}
      {revision && tab === 'requirements' && <><RequirementsForm key={revision.id} revision={revision} active={active} onSaved={load} /><ConnectionsForm key={`connections-${revision.id}`} revision={revision} active={active} onSaved={load} /></>}
      {revision && tab === 'history' && <section className={`${panel} flex flex-col gap-md`}>
        <p className="text-body-sm text-on-surface-variant">Compare the selected revision with an earlier design. Restoring creates a revalidated revision and keeps the existing history.</p>
        <label>Compare against <select aria-label="Compare against" className={`${button} ml-sm`} value={before} onChange={e => setBefore(e.target.value)}><option value="">Choose revision</option>{history.filter(item => item.id !== selected).map(item => <option key={item.id} value={item.id}>{new Date(item.created_at).toLocaleString()} · {item.reason}</option>)}</select></label>
        {comparison && <div><p>Cost change: {comparison.cost_delta >= 0 ? '+' : ''}${comparison.cost_delta.toFixed(2)}</p><p>Failed checks: {comparison.before.fail} → {comparison.after.fail} · Unknown checks: {comparison.before.unknown} → {comparison.after.unknown}</p>{comparison.components.map(item => <p key={item.slot}>{item.slot}: {item.before?.join(' / ') ?? 'Empty'} → {item.after?.join(' / ') ?? 'Empty'}</p>)}{comparison.requirements.map(item => <p key={item.field}>{label(item.field)}: {display(item.before)} → {display(item.after)}</p>)}{comparison.connections_changed && <p>Connections changed.</p>}{!comparison.connections_changed && !comparison.components.length && !comparison.requirements.length && <p>No component or requirement differences.</p>}</div>}
        <button className={`${button} self-start`} disabled={busy || selected === active} onClick={restore}>{busy ? 'Restoring…' : 'Restore this revision'}</button>
      </section>}
    </main>
  </div>
}

function RequirementsForm({ revision, active, onSaved }: { revision: Revision; active: string; onSaved: (id: string) => Promise<void> }) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const changes = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, value.trim() === '' ? null : key === 'priority' ? value : Number(value)]))
      const result = await updateRequirements(revision.id, active, changes)
      await onSaved(result.revision_id)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save requirements') }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit} className={`${panel} flex flex-col gap-md`}>
    <p className="text-body-sm text-on-surface-variant">Review stated requirements and assumptions. Saving creates a new revision and reruns validation; it does not automatically replace components.</p>
    {revision.document.requirements.map(item => <label key={item.field} className="grid grid-cols-1 md:grid-cols-3 items-center gap-sm border-b border-outline-variant pb-md"><span className="capitalize">{label(item.field)}</span>{editable.has(item.field) ? <input className={button} type="number" step="any" aria-label={label(item.field)} value={values[item.field] ?? (item.value === null ? '' : String(item.value))} onChange={e => setValues(previous => ({ ...previous, [item.field]: e.target.value }))} /> : item.field === 'priority' ? <select className={button} aria-label="priority" value={values.priority ?? String(item.value)} onChange={e => setValues(previous => ({ ...previous, priority: e.target.value }))}>{['cost', 'size', 'availability'].map(option => <option key={option}>{option}</option>)}</select> : <span>{display(item.value)}</span>}<span className="text-outline text-body-sm">{item.basis}</span></label>)}
    {error && <p role="alert" className="text-error">{error}</p>}
    <button className={`${button} self-start`} disabled={busy || !Object.keys(values).length || revision.id !== active}>{busy ? 'Validating…' : 'Save and revalidate'}</button>
    {revision.id !== active && <p className="text-body-sm text-outline">Select the current revision to edit requirements.</p>}
  </form>
}

function ConnectionsForm({ revision, active, onSaved }: { revision: Revision; active: string; onSaved: (id: string) => Promise<void> }) {
  const [nets, setNets] = useState<NetInput[]>((revision.document.board.signal_nets ?? []).map(net => ({ id: net.id, protocol: net.protocol, controller: net.ports.find(port => port.role === 'controller')?.component ?? '', peripherals: net.ports.filter(port => port.role === 'peripheral').map(port => port.component) })))
  const [power, setPower] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const slots = Object.entries(revision.document.board.slots)
  const update = (index: number, changes: Partial<NetInput>) => setNets(previous => previous.map((net, i) => i === index ? { ...net, ...changes } : net))
  async function save() {
    setBusy(true); setError('')
    try { const result = await updateConnections(revision.id, nets, power); await onSaved(result.revision_id) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not save connections') }
    finally { setBusy(false) }
  }
  return <section className={`${panel} flex flex-col gap-md`}>
    <h2 className="font-label-caps text-label-caps uppercase">Connections</h2>
    <p className="text-body-sm text-on-surface-variant">Confirm connections against your circuit. Protocol support is checked per connection; physical pin assignments and timing still need review.</p>
    {Object.entries(revision.document.board.rails).map(([id, rail]) => <p key={id} className="text-body-sm">{id} · {rail.voltage} V · {rail.source ?? 'External supply'} → {[...new Set(rail.members)].join(', ')}</p>)}
    {Object.keys(revision.document.board.rails).length > 0 && <label className="text-body-sm"><input type="checkbox" checked={power} onChange={event => setPower(event.target.checked)} /> The displayed power connections match my circuit.</label>}
    {nets.map((net, index) => <div key={index} className="flex flex-wrap gap-sm items-center">
      <input aria-label={`Connection ${index + 1} name`} className={button} value={net.id} onChange={e => update(index, { id: e.target.value })} />
      <select aria-label={`Connection ${index + 1} protocol`} className={button} value={net.protocol} onChange={e => update(index, { protocol: e.target.value })}>{['I2C', 'SPI', 'UART', 'CAN', 'USB', 'I2S', '1-WIRE'].map(value => <option key={value}>{value}</option>)}</select>
      <select aria-label={`Connection ${index + 1} controller`} className={button} value={net.controller} onChange={e => update(index, { controller: e.target.value })}><option value="">Controller</option>{slots.map(([id, slot]) => <option key={id} value={id}>{slot.label}</option>)}</select>
      <fieldset className="flex gap-sm flex-wrap"><legend className="text-body-sm">Peripherals</legend>{slots.filter(([id]) => id !== net.controller).map(([id, slot]) => <label className="text-body-sm" key={id}><input type="checkbox" checked={net.peripherals.includes(id)} onChange={e => update(index, { peripherals: e.target.checked ? [...net.peripherals, id] : net.peripherals.filter(value => value !== id) })} /> {slot.label}</label>)}</fieldset>
      <button className={button} type="button" onClick={() => setNets(previous => previous.filter((_, i) => i !== index))}>Remove</button>
    </div>)}
    <div className="flex gap-sm"><button className={button} disabled={nets.length >= 32} onClick={() => setNets(previous => [...previous, { id: `bus-${Date.now()}`, protocol: 'I2C', controller: '', peripherals: [] }])}>Add signal connection</button><button className={button} disabled={busy || revision.id !== active} onClick={save}>{busy ? 'Validating…' : 'Save connections and revalidate'}</button></div>
    {error && <p role="alert" className="text-error">{error}</p>}
  </section>
}
