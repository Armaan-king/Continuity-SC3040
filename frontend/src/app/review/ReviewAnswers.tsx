import { useRef, useState } from 'react'
import { updateRequirements, type Revision, type ReviewQuestion } from '../lib/revisions'
import AnswerPrompt from './AnswerPrompt'

type Answer = { value: string; upper: string }
type Props = {
  questions: ReviewQuestion[]
  revision: Revision
  active: string
  onSaved: (id: string) => Promise<void>
}

const button = 'rounded-md border border-outline-variant px-4 py-2 text-body-sm transition-colors hover:border-primary-container hover:text-primary-container disabled:opacity-40'
const input = 'input-field review-field w-28 min-w-0 flex-1'

function initialAnswer(question: ReviewQuestion): Answer {
  const value = question.value
  return {
    value: value === null ? '' : String(Array.isArray(value) ? value[0] : value),
    upper: Array.isArray(value) ? String(value[1]) : '',
  }
}

/** One shared draft and save boundary for individual and combined confirmation. */
export default function ReviewAnswers({ questions, revision, active, onSaved }: Props) {
  const [answers, setAnswers] = useState<Record<string, Answer>>(() =>
    Object.fromEntries(questions.map(question => [question.field, initialAnswer(question)])),
  )
  const [saving, setSaving] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [assistanceBusy, setAssistanceBusy] = useState(false)
  const [modes, setModes] = useState<Record<string, string>>({})
  const forms = useRef(new Map<string, HTMLFormElement>())
  const pending = useRef(false)
  const editable = revision.id === active
  const disabled = saving !== null || assistanceBusy || !editable
  const complete = (question: ReviewQuestion) => Boolean(answers[question.field].value.trim()
    && (question.field !== 'temp_range' || answers[question.field].upper.trim()))
  const completeCount = questions.filter(complete).length

  function change(field: string, update: Partial<Answer>) {
    setAnswers(previous => ({ ...previous, [field]: { ...previous[field], ...update } }))
    setError('')
  }

  function choose(question: ReviewQuestion, value: number | number[] | null, mode: string) {
    change(question.field, initialAnswer({ ...question, value }))
    setModes(previous => ({ ...previous, [question.field]: mode }))
  }

  async function save(selected: ReviewQuestion[], action: string) {
    if (pending.current || disabled) return
    setError('')
    const changes: Record<string, number | number[]> = {}
    for (const question of selected) {
      const form = forms.current.get(question.field)
      if (!form?.reportValidity()) return
      const answer = answers[question.field]
      const value = Number(answer.value)
      if (question.field === 'temp_range') {
        const upper = Number(answer.upper)
        if (value >= upper) {
          setError('The maximum operating temperature must be greater than the minimum. No answers were saved.')
          form.querySelector<HTMLInputElement>('[aria-label="Maximum operating temperature"]')?.focus()
          return
        }
        changes[question.field] = [value, upper]
      } else {
        changes[question.field] = value
      }
    }
    if (!Object.keys(changes).length) return
    pending.current = true
    setSaving(action)
    try {
      const result = await updateRequirements(revision.id, active, changes)
      await onSaved(result.revision_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save answers. Your entries are still here; please try again.')
    } finally {
      pending.current = false
      setSaving(null)
    }
  }

  function saveAll() {
    // Skip wholly blank questions, but never silently omit a partly entered range
    // or a malformed number whose native input value is an empty string.
    const selected = questions.filter(question => {
      const answer = answers[question.field]
      const badNumber = Array.from(forms.current.get(question.field)?.querySelectorAll('input') ?? [])
        .some(element => element.validity.badInput)
      return answer.value.trim() || answer.upper.trim() || badNumber
    })
    void save(selected, 'all')
  }

  return <section aria-labelledby="review-questions">
    <div className="mb-4 flex items-center justify-between gap-3">
      <div>
        <p className="text-xs uppercase tracking-widest text-primary-container">01 / Your design intent</p>
        <h2 id="review-questions" className="mt-1 text-xl">A few things only you can tell us</h2>
      </div>
      <span className="shrink-0 whitespace-nowrap rounded-full border border-outline-variant px-3 py-1 text-xs">{questions.length} questions</span>
    </div>
    <p className="mb-4 text-body-sm text-on-surface-variant">
      Choose an example target, write your own value, or leave a question open. Saving creates one revalidated revision and returns you to the planning workspace.
    </p>
    <div className="grid gap-4 md:grid-cols-2">
      {questions.map(question => {
        const answer = answers[question.field]
        const range = question.field === 'temp_range'
        return <form key={question.field} aria-label={question.title}
          ref={form => { if (form) forms.current.set(question.field, form); else forms.current.delete(question.field) }}
          onSubmit={event => { event.preventDefault(); void save([question], question.field) }}
          className="rounded-xl border border-outline-variant bg-surface-container-low flex flex-col p-5">
          <div className="mb-3 flex items-center gap-2 text-xs text-tertiary-container">
            <span aria-hidden="true" className="material-symbols-outlined text-[20px]">chat_bubble</span>
            {modes[question.field] === 'skip' ? 'Left open' : modes[question.field] ? 'Your answer · not saved'
              : question.value === null ? 'Your input needed' : `Confirm ${question.basis} value`}
          </div>
          <h3 className="text-body-md">{question.title}</h3>
          <p className="mt-2 mb-4 flex-1 text-body-sm leading-relaxed text-on-surface-variant">{question.why}</p>
          <p className="mb-2 text-xs text-on-surface-variant">{question.options?.length
            ? 'Example targets · choose only what matches your needs'
            : 'Use the actual battery rating · no capacity is assumed'}</p>
          <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label={`Answer choices: ${question.title}`}>
            {question.options?.map(option => <button key={option.label} type="button" disabled={disabled}
              aria-pressed={modes[question.field] === option.label}
              className={`${button} ${modes[question.field] === option.label ? 'border-primary-container bg-primary-container/10 text-primary-container' : ''}`}
              onClick={() => choose(question, option.value, option.label)}>{option.label}</button>)}
            <button type="button" disabled={disabled} aria-pressed={modes[question.field] === 'custom'} className={button}
              onClick={() => {
                setModes(previous => ({ ...previous, [question.field]: 'custom' }))
                forms.current.get(question.field)?.querySelector('input')?.focus()
              }}>Write your own</button>
            <button type="button" disabled={disabled} aria-pressed={modes[question.field] === 'skip'} className={button}
              onClick={() => choose(question, null, 'skip')}>Not sure yet</button>
          </div>
          {modes[question.field] === 'skip' && <p className="mb-3 text-xs text-tertiary-container">Left open. This question will stay unanswered when you confirm the others.</p>}
          <div className="flex flex-wrap items-center gap-2">
            <input required type="number" step={range ? '1' : 'any'}
              min={question.minimum ?? undefined} max={question.maximum ?? undefined}
              aria-label={range ? 'Minimum operating temperature' : question.title}
              className={input} value={answer.value} disabled={disabled}
              onChange={event => { change(question.field, { value: event.target.value }); setModes(previous => ({ ...previous, [question.field]: 'custom' })) }} />
            {range && <>
              <span className="text-xs">to</span>
              <input required type="number" step="1"
                min={question.minimum ?? undefined} max={question.maximum ?? undefined}
                aria-label="Maximum operating temperature" className={input}
                value={answer.upper} disabled={disabled}
                onChange={event => { change(question.field, { upper: event.target.value }); setModes(previous => ({ ...previous, [question.field]: 'custom' })) }} />
            </>}
            <span className="text-xs text-on-surface-variant">{question.unit}</span>
            <button className={`${button} w-full text-primary-container sm:w-auto`}
              disabled={disabled || !complete(question)}>
              {saving === question.field ? 'Validating…' : 'Confirm & revalidate'}
            </button>
          </div>
        </form>
      })}
    </div>
    <AnswerPrompt revisionId={revision.id} questions={questions} disabled={saving !== null || !editable}
      onBusy={setAssistanceBusy} onUse={changes => {
        for (const question of questions) {
          if (question.field in changes) choose(question, changes[question.field], 'custom')
        }
      }} />
    <div className="mt-4 flex flex-col items-stretch justify-between gap-4 rounded-xl border border-primary-container/30 bg-primary-container/5 p-5 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p className="text-body-sm">Save your answers together</p>
        <p className="mt-1 text-xs leading-relaxed text-on-surface-variant">
          Confirms all filled values, including displayed proposals. Blank fields stay unanswered.
        </p>
        <p className="mt-2 text-xs text-on-surface-variant">{completeCount} of {questions.length} questions have values.</p>
      </div>
      <button type="button" onClick={saveAll} disabled={disabled || completeCount === 0}
        className={`${button} shrink-0 border-primary-container text-primary-container`}>
        {saving === 'all' ? 'Validating answers…' : 'Confirm all & revalidate'}
      </button>
    </div>
    {assistanceBusy && <p className="mt-3 text-body-sm text-on-surface-variant">Review or discard the written proposal before confirming answers.</p>}
    {!editable && <p className="mt-3 text-body-sm text-outline">Select the current revision to confirm answers.</p>}
    {error && <p role="alert" className="mt-3 text-body-sm text-error">{error}</p>}
  </section>
}
