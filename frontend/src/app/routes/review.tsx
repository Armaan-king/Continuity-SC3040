import Report from '../review/Report'
import RequirementsForm from '../review/Requirements'
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { getProject } from '../lib/api'
import { compareRevisions, getRevision, listRevisions, restoreRevision, revisionExport, updateConnections, type NetInput, type Comparison, type Revision, type RevisionSummary } from '../lib/revisions'

const button = 'rounded border border-outline-variant bg-surface-container-high px-md py-sm text-body-sm hover:text-primary-container disabled:opacity-40'
const panel = 'border border-outline-variant bg-surface-container-low rounded-xl p-6'
const label = (value: string) => value.replaceAll('_', ' ')
const display = (value: unknown) => value === null || value === undefined ? 'Not specified' : Array.isArray(value) ? value.join(' to ') : String(value)
const tabs = { report: 'Review', requirements: 'Requirements', history: 'Revision history' }

export default function ReviewRoute() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
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

  const returnToPlan = useCallback(async (revisionId: string) => {
    navigate(`/design/${projectId}`, { state: { revisedRevision: revisionId } })
  }, [navigate, projectId])

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
    try { const result = await restoreRevision(selected, active); await returnToPlan(result.revision_id) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not restore revision') }
    finally { setBusy(false) }
  }

  return <div className="h-full overflow-y-auto bg-background text-on-background font-body-md">
    <header className="flex flex-wrap items-center justify-between gap-md px-lg py-md border-b border-outline-variant bg-surface-container-low">
      <div><Link className="text-primary-container text-body-sm" to={`/design/${projectId}`}>← Back to workspace</Link><h1 className="font-headline-sm text-headline-sm mt-sm">{name}</h1></div>
      {revision && <div className="flex gap-sm"><a className={button} href={revisionExport(selected, 'report.json')}>Export report</a><a className={button} href={revisionExport(selected, 'bom.csv')}>Export BOM</a></div>}
    </header>
    <main className="max-w-[1200px] mx-auto px-4 py-8 md:px-8 flex flex-col gap-8 animate-page-enter">
      <nav aria-label="Design review" className="flex gap-sm flex-wrap border-b border-outline-variant pb-4">{(['report', 'requirements', 'history'] as const).map(value => <button key={value} type="button" aria-pressed={tab === value} onClick={() => setTab(value)} className={`${button} ${tab === value ? 'text-primary-container border-primary-container' : ''}`}>{tabs[value]}</button>)}</nav>
      {error && <div role="alert" className="border border-error text-error p-md">{error} <button className="underline" onClick={() => { setError(''); load().catch(e => setError(e.message)) }}>Reload</button></div>}
      {!loaded && <p role="status">Loading saved designs…</p>}
      {loaded && history.length === 0 && <div className={panel}>This project has no versioned result yet. Its existing workspace and exports remain available. New completed runs include a validation report.</div>}
      {history.length > 0 && <label className="text-body-sm text-on-surface-variant">Viewing revision <select aria-label="Viewing revision" className={`${button} ml-sm max-w-full`} value={selected} onChange={e => setSelected(e.target.value)}>{history.map((item, i) => <option key={item.id} value={item.id}>{history.length - i} · {item.reason}{item.active ? ' · Current' : ''}</option>)}</select></label>}
      {revision && tab === 'report' && <Report key={revision.id} revision={revision} active={active} onSaved={returnToPlan} onRequirements={() => setTab('requirements')} />}
      {revision && tab === 'requirements' && <><RequirementsForm key={revision.id} revision={revision} active={active} onSaved={returnToPlan} /><ConnectionsForm key={`connections-${revision.id}`} revision={revision} active={active} onSaved={returnToPlan} /></>}
      {revision && tab === 'history' && <section className={`${panel} flex flex-col gap-md`}>
        <p className="text-body-sm text-on-surface-variant">Compare the selected revision with an earlier design. Restoring creates a revalidated revision and keeps the existing history.</p>
        <label>Compare against <select aria-label="Compare against" className={`${button} ml-sm`} value={before} onChange={e => setBefore(e.target.value)}><option value="">Choose revision</option>{history.filter(item => item.id !== selected).map(item => <option key={item.id} value={item.id}>{new Date(item.created_at).toLocaleString()} · {item.reason}</option>)}</select></label>
        {comparison && <div><p>Cost change: {comparison.cost_delta >= 0 ? '+' : ''}${comparison.cost_delta.toFixed(2)}</p><p>Failed checks: {comparison.before.fail} → {comparison.after.fail} · Unknown checks: {comparison.before.unknown} → {comparison.after.unknown}</p>{comparison.components.map(item => <p key={item.slot}>{item.slot}: {item.before?.join(' / ') ?? 'Empty'} → {item.after?.join(' / ') ?? 'Empty'}</p>)}{comparison.requirements.map(item => <p key={item.field}>{label(item.field)}: {display(item.before)} → {display(item.after)}</p>)}{comparison.connections_changed && <p>Connections changed.</p>}{!comparison.connections_changed && !comparison.components.length && !comparison.requirements.length && <p>No component or requirement differences.</p>}</div>}
        <button className={`${button} self-start`} disabled={busy || selected === active} onClick={restore}>{busy ? 'Restoring…' : 'Restore this revision'}</button>
      </section>}
    </main>
  </div>
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
      <fieldset className="flex gap-sm flex-wrap border-b border-outline-variant pb-4"><legend className="text-body-sm">Peripherals</legend>{slots.filter(([id]) => id !== net.controller).map(([id, slot]) => <label className="text-body-sm" key={id}><input type="checkbox" checked={net.peripherals.includes(id)} onChange={e => update(index, { peripherals: e.target.checked ? [...net.peripherals, id] : net.peripherals.filter(value => value !== id) })} /> {slot.label}</label>)}</fieldset>
      <button className={button} type="button" onClick={() => setNets(previous => previous.filter((_, i) => i !== index))}>Remove</button>
    </div>)}
    <div className="flex gap-sm"><button className={button} disabled={nets.length >= 32} onClick={() => setNets(previous => [...previous, { id: `bus-${Date.now()}`, protocol: 'I2C', controller: '', peripherals: [] }])}>Add signal connection</button><button className={button} disabled={busy || revision.id !== active} onClick={save}>{busy ? 'Validating…' : 'Save connections and revalidate'}</button></div>
    {error && <p role="alert" className="text-error">{error}</p>}
  </section>
}
