import type { ResearchDocument } from '../lib/revisions'

const safeSource = (url: string) => /^https?:\/\//.test(url) ? url : undefined

export default function ResearchSource({ document: doc }: { document: ResearchDocument }) {
  const claims = doc.claims ?? []
  const accepted = claims.filter(claim => claim.status === 'source_checked').length
  const conflicts = claims.filter(claim => claim.status === 'conflict').length
  const pending = claims.length - accepted - conflicts
  const extraction = doc.extraction_status === 'ai_unavailable' ? 'AI extraction unavailable; evidence remains open.'
    : doc.extraction_status === 'budget_exhausted' ? 'Extraction allowance reached; evidence remains open.'
    : doc.extraction_status === 'invalid_response' ? 'Extraction could not be validated; evidence remains open.'
    : doc.extraction_status === 'source_not_eligible' ? 'No eligible manufacturer text was available for automatic extraction. Review this source manually.' : null
  return <details className="mt-4 border-t border-outline-variant pt-3">
    <summary className="cursor-pointer break-words text-body-sm">{doc.title}</summary>
    <div className="my-3 flex flex-wrap gap-2 text-xs">
      {accepted > 0 && <span className="rounded border border-primary-container/40 px-2 py-1 text-primary-container">{accepted} source-checked {accepted === 1 ? 'claim' : 'claims'}</span>}
      {conflicts > 0 && <span className="rounded border border-error/40 px-2 py-1 text-error">{conflicts} conflicting {conflicts === 1 ? 'claim' : 'claims'}</span>}
      {pending > 0 && <span className="rounded border border-tertiary-container/40 px-2 py-1 text-tertiary-container">{pending} {pending === 1 ? 'claim needs' : 'claims need'} review</span>}
      {!claims.length && <span className="text-tertiary-container">Research lead · requires verification</span>}
    </div>
    <p className="mb-2 text-xs leading-relaxed text-on-surface-variant">{extraction ?? (accepted ? 'Only the cited facts passed source checks. This does not verify the entire document or physical circuit.' : 'No facts from this document have been admitted to the checks.')}</p>
    <p className="mb-2 text-xs text-on-surface-variant">Retrieved {new Date(doc.retrieved_at).toLocaleString()} · {doc.question}</p>
    <a href={safeSource(doc.url)} target="_blank" rel="noreferrer" className="text-body-sm text-primary-container underline">Open document ↗</a>
    {!!claims.length && <div className="mt-3 space-y-3">{claims.map((claim, index) => <div key={index} className="rounded border border-outline-variant bg-surface p-3 text-xs">
      <p className="break-words">{claim.parameter}: {claim.value}</p>
      <p className={`mt-1 ${claim.status === 'source_checked' ? 'text-primary-container' : claim.status === 'conflict' ? 'text-error' : 'text-tertiary-container'}`}>{claim.status === 'source_checked' ? 'Used by checks' : claim.status === 'conflict' ? 'Conflict · excluded from checks' : 'Needs review · not used by checks'}</p>
      {claim.reason && <p className="mt-2 leading-relaxed text-on-surface-variant">{claim.reason}</p>}
      {claim.previous_values && <p className="mt-2 leading-relaxed text-on-surface-variant">Previous specifications: {Object.entries(claim.previous_values).filter(([, value]) => value !== null).map(([name, value]) => `${name.replaceAll('_', ' ')} = ${String(value)}`).join(' · ') || 'not established'}</p>}
      {claim.identity_basis === 'supplier_linked_family' && <p className="mt-2 text-on-surface-variant">Family interface evidence · datasheet linked by the supplier</p>}
      {claim.conditions && <p className="mt-2 text-on-surface-variant">Conditions: {claim.conditions}</p>}
      <blockquote className="mt-2 whitespace-pre-wrap break-words border-l-2 border-outline-variant pl-3 leading-relaxed text-on-surface-variant">{claim.quote}</blockquote>
    </div>)}</div>}
    <details className="mt-3 text-xs"><summary className="cursor-pointer text-on-surface-variant">Retrieved document text</summary><pre className="mt-3 max-h-64 overflow-y-auto whitespace-pre-wrap break-words">{doc.text}</pre></details>
  </details>
}
