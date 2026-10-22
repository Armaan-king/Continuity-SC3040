import { useState } from 'react'
import { updateRequirements, type Revision } from '../lib/revisions'

const fields: Record<string, { unit: string; help: string; min: number; max: number; optional?: boolean; integer?: boolean }> = {
  input_voltage: { unit: 'V', help: 'Nominal input to this power tree. Changing it rechecks the selected parts; it does not redesign the circuit.', min: 0.01, max: 1000 },
  ambient_c: { unit: '°C', help: 'Ambient temperature used for thermal calculations.', min: -100, max: 200, integer: true },
  current_margin: { unit: 'fraction', help: '0.15 means 15% allowance above known load. Unknown currents still need evidence.', min: 0, max: 2 },
  min_stock: { unit: 'units', help: 'Minimum supplier stock needed for your planned quantity.', min: 0, max: 10000000, optional: true, integer: true },
  max_lead_days: { unit: 'days', help: 'Longest acceptable component lead time.', min: 0, max: 3650, integer: true },
  lifetime_hours: { unit: 'hours', help: 'Required operation between charges. Sleep and alarm duty cycles are not modeled.', min: 0.01, max: 87600, optional: true },
  supply_capacity_mah: { unit: 'mAh', help: 'The selected battery’s rated capacity, from its label or datasheet.', min: 0.01, max: 10000000, optional: true },
  max_package_mm: { unit: 'mm', help: 'Maximum individual component dimension. This does not constrain PCB or enclosure size.', min: 0.01, max: 1000, optional: true },
}
const label = (value: string) => value.replaceAll('_', ' ')

export default function RequirementsForm({ revision, active, onSaved }: { revision: Revision; active: string; onSaved: (id: string) => Promise<void> }) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const set = (key: string, value: string) => setValues(previous => ({ ...previous, [key]: value }))
  const temperatures = revision.document.requirements.find(item => item.field === 'temp_range')?.value as number[] ?? [0, 70]
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const changes: Record<string, unknown> = Object.fromEntries(Object.entries(values).filter(([key]) => !key.startsWith('temp_')).map(([key, value]) => [key, value.trim() === '' ? null : ['priority', 'input_source'].includes(key) ? value : Number(value)]))
      if ('temp_min' in values || 'temp_max' in values) changes.temp_range = [Number(values.temp_min ?? temperatures[0]), Number(values.temp_max ?? temperatures[1])]
      const result = await updateRequirements(revision.id, active, changes)
      await onSaved(result.revision_id)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save requirements') }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="rounded-xl border border-outline-variant bg-surface-container-low overflow-hidden">
    <div className="p-6 border-b border-outline-variant"><p className="text-xs uppercase tracking-widest text-primary-container">Design intent</p><h2 className="mt-2 text-xl">Requirements & assumptions</h2><p className="mt-2 max-w-2xl text-body-sm text-on-surface-variant">Defaults are proposals. Confirm them against your brief, not the parts search returned. Saving creates a new revision and reruns validation without replacing components.</p></div>
    <div className="grid gap-5 p-6 md:grid-cols-2">{revision.document.requirements.map(item => <div key={item.field} className="rounded-lg border border-outline-variant p-4 min-w-0">
      <div className="flex justify-between gap-2 mb-3"><label htmlFor={`requirement-${item.field}`} className="text-body-sm capitalize">{label(item.field)}</label><span className={`rounded px-2 py-0.5 text-xs ${item.basis === 'confirmed' || item.basis === 'stated' ? 'bg-primary-container/10 text-primary-container' : 'bg-surface-container text-tertiary-container'}`}>{item.value === null ? 'unanswered' : item.basis}</span></div>
      {fields[item.field] ? <div className="flex items-center gap-3"><input id={`requirement-${item.field}`} required={!fields[item.field].optional} className="input-field review-field w-full min-w-0" type="number" step={fields[item.field].integer ? '1' : 'any'} min={fields[item.field].min} max={fields[item.field].max} aria-label={label(item.field)} value={values[item.field] ?? (item.value === null ? '' : String(item.value))} onChange={e => set(item.field, e.target.value)} /><span className="text-xs text-on-surface-variant">{fields[item.field].unit}</span></div>
      : item.field === 'temp_range' ? <div className="flex items-center gap-2"><input id="requirement-temp_range" required type="number" step="1" min="-273" max="300" aria-label="Minimum operating temperature" className="input-field review-field min-w-0 w-full" value={values.temp_min ?? temperatures[0]} onChange={e => set('temp_min', e.target.value)} /><span className="text-xs">to</span><input required type="number" step="1" min="-273" max="300" aria-label="Maximum operating temperature" className="input-field review-field min-w-0 w-full" value={values.temp_max ?? temperatures[1]} onChange={e => set('temp_max', e.target.value)} /><span className="text-xs">°C</span></div>
      : item.field === 'input_source' ? <select id="requirement-input_source" className="input-field review-field w-full" value={values.input_source ?? String(item.value)} onChange={e => set('input_source', e.target.value)}>{!(revision.guidance?.input_sources ?? []).some(s => s.key === item.value) && <option value={String(item.value)}>{String(item.value)}</option>}{revision.guidance?.input_sources.map(source => <option key={source.key} value={source.key}>{source.label}</option>)}</select>
      : item.field === 'priority' ? <select id="requirement-priority" className="input-field review-field w-full" value={values.priority ?? String(item.value)} onChange={e => set('priority', e.target.value)}>{['cost', 'size', 'availability'].map(option => <option key={option}>{option}</option>)}</select> : <span>{String(item.value ?? 'Not specified')}</span>}
      <p className="mt-3 text-xs leading-relaxed text-on-surface-variant">{fields[item.field]?.help ?? (item.field === 'input_source' ? 'Select what powers the load during operation. Charging and battery discharge modes need separate circuit review.' : item.field === 'temp_range' ? 'Required environment for the finished device. Confirm the proposed range.' : 'Used to rank candidates after electrical checks.')}</p>
    </div>)}</div>
    <div className="p-6 border-t border-outline-variant">{error && <p role="alert" className="mb-3 text-error">{error}</p>}<button className="rounded-md border border-primary-container bg-primary-container/10 px-5 py-3 text-body-sm text-primary-container disabled:opacity-40" disabled={busy || !Object.keys(values).length || revision.id !== active}>{busy ? 'Validating…' : 'Save and revalidate'}</button>{revision.id !== active && <p className="mt-2 text-body-sm text-outline">Select the current revision to edit requirements.</p>}</div>
  </form>
}
