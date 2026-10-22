import { useRef, useState } from 'react'
import { proposeAnswers, type AnswerProposal, type ReviewQuestion } from '../lib/revisions'

type Props = {
  revisionId: string
  questions: ReviewQuestion[]
  disabled: boolean
  onBusy: (busy: boolean) => void
  onUse: (changes: AnswerProposal['changes']) => void
}
const button = 'rounded-md border border-outline-variant px-4 py-2 text-body-sm hover:border-primary-container hover:text-primary-container disabled:opacity-40'

export default function AnswerPrompt({ revisionId, questions, disabled, onBusy, onUse }: Props) {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [proposal, setProposal] = useState<AnswerProposal | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [unhandled, setUnhandled] = useState<string[]>([])
  const pending = useRef(false)

  async function preview() {
    if (pending.current || disabled || !text.trim()) return
    pending.current = true
    setLoading(true); setError(''); setNotice(''); setUnhandled([]); onBusy(true)
    try {
      setProposal(await proposeAnswers(revisionId, text.trim()))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not interpret this request. Your text is still here.')
      onBusy(false)
    } finally {
      pending.current = false
      setLoading(false)
    }
  }

  function clearPreview() {
    setProposal(null); onBusy(false)
  }

  return <details className="mt-4 rounded-xl border border-outline-variant bg-surface-container-low p-5">
    <summary className="cursor-pointer text-body-sm text-primary-container">None of these? Describe a change</summary>
    <div className="mt-4 space-y-3">
      <p className="text-body-sm leading-relaxed text-on-surface-variant">
        Write answers to the open questions in your own words. Review the proposed values, then confirm them below.
        Component replacements and circuit connections need the workspace or Requirements editor.
      </p>
      <label htmlFor="review-answer-request" className="block text-body-sm">Your requirement changes</label>
      <textarea id="review-answer-request" rows={3} maxLength={2000}
        className="input-field w-full resize-y px-3 py-2 font-body-sm text-body-sm leading-relaxed"
        placeholder="For example: It should run for 3 days, tolerate −10 to 60 °C, and use a 30% current margin."
        disabled={loading || disabled} value={text}
        onChange={event => { setText(event.target.value); clearPreview(); setError(''); setNotice(''); setUnhandled([]) }} />
      <p className="text-xs text-on-surface-variant">Only this preview uses AI. Options, direct answers and revalidation need no AI calls.</p>
      {!proposal && <button type="button" className={button} disabled={disabled || loading || !text.trim()} onClick={() => void preview()}>
        {loading ? 'Interpreting…' : 'Preview proposed answers'}
      </button>}
      {proposal && <div className="space-y-3 rounded-lg border border-primary-container/30 p-4" aria-label="Proposed answers" role="region">
        <p className="text-body-sm">Review before adding these answers</p>
        {Object.entries(proposal.changes).map(([field, value]) => {
          const question = questions.find(item => item.field === field)
          return <div key={field} className="border-t border-outline-variant pt-3 text-body-sm">
            <p>{question?.title ?? field}</p>
            <p className="mt-1 text-primary-container">{Array.isArray(value) ? value.join(' to ') : value} {question?.unit}</p>
            <p className="mt-1 break-words text-xs text-on-surface-variant">From your request: “{proposal.quotes[field]}”</p>
          </div>
        })}
        {!!proposal.unhandled.length && <div className="text-body-sm text-tertiary-container">
          <p>Still needs your attention</p>
          <ul className="mt-2 list-disc space-y-1 pl-4">{proposal.unhandled.map((line, i) => <li key={i}>{line}</li>)}</ul>
        </div>}
        {!Object.keys(proposal.changes).length && <p className="text-body-sm">No requirement values were proposed. Add exact values and units, or answer the cards directly.</p>}
        <p className="text-xs text-on-surface-variant">Using these answers replaces those fields in your draft. Nothing is saved until you confirm and revalidate.</p>
        <div className="flex flex-wrap gap-2">
          {!!Object.keys(proposal.changes).length && <button type="button" className={`${button} text-primary-container`} disabled={disabled}
            onClick={() => { onUse(proposal.changes); setUnhandled(proposal.unhandled); clearPreview(); setNotice('Answers added to the cards. Review them, then choose Confirm all & revalidate.') }}>
            Use these answers
          </button>}
          <button type="button" className={button} disabled={disabled} onClick={clearPreview}>Discard proposal</button>
        </div>
      </div>}
      {error && <p role="alert" className="text-body-sm text-error">{error}</p>}
      {notice && <p role="status" className="text-body-sm text-primary-container">{notice}</p>}
      {!!unhandled.length && <div className="text-body-sm text-tertiary-container"><p>Not included in these answers</p>
        <ul className="mt-2 list-disc space-y-1 pl-4">{unhandled.map((line, i) => <li key={i}>{line}</li>)}</ul>
      </div>}
    </div>
  </details>
}
