import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from 'react'
import { Link } from 'react-router'

import { updateProject } from '../lib/api'
import { PcbBackground } from '../shell/PcbBackground'
import { Wordmark } from '../shell/Wordmark'

type BriefEntryProps = {
  projectId?: string
  onStarted: (brief: string, bom?: string) => void
  walkthroughBrief?: string
}

type BomAttachment = {
  name: string
  text: string
}

const SAMPLE_BRIEFS = [
  'LoRa GPS tracker, solar powered, runs outdoors year round',
  'PoE security camera with an image sensor',
  'BLE beacon on a coin cell, must last a year',
]

function deriveProjectName(brief: string) {
  return brief
    .trim()
    .split(/\s+/)
    .slice(0, 6)
    .join(' ')
    .trim()
    .slice(0, 64)
}

export function BriefEntry({ projectId, onStarted, walkthroughBrief }: BriefEntryProps) {
  const [brief, setBrief] = useState('')
  const [attachment, setAttachment] = useState<BomAttachment | null>(null)
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const attachmentReadRef = useRef(0)
  const submitRef = useRef<() => Promise<void>>(() => Promise.resolve())

  const canSubmit = useMemo(
    () => (brief.trim().length > 0 || attachment !== null) && !submitting,
    [attachment, brief, submitting],
  )

  const handleAttachmentChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''

    if (!file) {
      return
    }

    const readId = attachmentReadRef.current + 1
    attachmentReadRef.current = readId

    const extension = file.name.split('.').pop()?.toLowerCase()
    if (extension !== 'csv' && extension !== 'txt') {
      setAttachmentError('Choose a .csv or .txt BOM file.')
      return
    }

    setAttachment(null)
    setAttachmentError(null)

    const reader = new FileReader()
    reader.onload = () => {
      if (readId !== attachmentReadRef.current) {
        return
      }

      if (typeof reader.result !== 'string') {
        setAttachmentError(`Could not read ${file.name}. Choose another .csv or .txt file.`)
        return
      }

      setAttachment({ name: file.name, text: reader.result })
    }
    reader.onerror = () => {
      if (readId !== attachmentReadRef.current) {
        return
      }

      setAttachmentError(`Could not read ${file.name}. Choose another .csv or .txt file.`)
    }
    reader.onabort = reader.onerror
    reader.readAsText(file)
  }

  const submitBrief = async () => {
    const trimmedBrief = brief.trim()
    if ((!trimmedBrief && !attachment) || submitting) {
      return
    }

    setSubmitting(true)

    const nextName = deriveProjectName(trimmedBrief)
    if (projectId && nextName) {
      try {
        await updateProject(projectId, nextName)
      } catch {
        // naming failure must not block the run start
      }
    }

    onStarted(trimmedBrief, attachment?.text)
  }

  submitRef.current = submitBrief

  useEffect(() => {
    if (!walkthroughBrief) {
      return
    }

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let cursor = 0
    let timer: ReturnType<typeof window.setTimeout> | undefined
    let active = true

    const finish = () => {
      if (active) {
        window.setTimeout(() => {
          if (active) {
            submitRef.current().catch(() => undefined)
          }
        }, reducedMotion ? 0 : 450)
      }
    }
    const typeNext = () => {
      cursor += 1
      setBrief(walkthroughBrief.slice(0, cursor))
      if (cursor === walkthroughBrief.length) {
        finish()
        return
      }
      timer = window.setTimeout(typeNext, 40)
    }

    if (reducedMotion) {
      setBrief(walkthroughBrief)
      finish()
    } else {
      timer = window.setTimeout(typeNext, 40)
    }

    return () => {
      active = false
      if (timer) {
        window.clearTimeout(timer)
      }
    }
  }, [walkthroughBrief])

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    submitBrief().catch(() => undefined)
  }

  const handleTextareaKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submitBrief().catch(() => undefined)
    }
  }

  return (
    <>
      <PcbBackground />
      <div className="relative z-10 bg-transparent text-on-surface h-screen w-full overflow-hidden grid-bg flex flex-col font-body-md antialiased">
      <header className="w-full h-12 flex items-center justify-between px-md sticky top-0 z-50 bg-surface border-b border-outline-variant">
        <Link
          className="flex items-center gap-sm p-unit rounded hover:bg-surface-variant transition-colors active:opacity-80"
          to="/projects"
        >
          <span className="material-symbols-outlined text-primary">arrow_back</span>
          <span className="font-label-caps text-label-caps uppercase text-on-surface-variant">
            NEW PROJECT
          </span>
        </Link>

        <Wordmark />
      </header>

      <main className="flex-grow flex items-center justify-center p-container-margin">
        <div className="w-full max-w-[720px] flex flex-col gap-xl">
          <div className="flex flex-col gap-sm">
            <h1 className="font-headline-sm text-[28px] leading-[36px] text-on-surface">
              What are you building?
            </h1>
            <p className="font-body-md text-on-surface-variant">
              Describe the board in plain language. Continuity will plan it, source real parts,
              and tell you what does not fit.
            </p>
          </div>

          <form onSubmit={handleSubmit}>
            <div className="relative rounded-DEFAULT border border-outline-variant bg-surface-container-lowest transition-all duration-200 focus-within:border-primary-container focus-within:shadow-[0_0_0_1px_#f2a25c,0_0_8px_rgba(242,162,92,0.2)]" data-tour="brief-entry">
              <textarea
                className="w-full h-[120px] bg-transparent border-none resize-none p-md pb-12 pr-[52px] font-data-tabular text-data-tabular text-on-surface placeholder:text-on-surface-variant/50 focus:ring-0"
                onChange={(event) => setBrief(event.target.value)}
                onKeyDown={handleTextareaKeyDown}
                placeholder="temperature sensor and OLED on USB-C, first production run of 5000 units"
                value={brief}
              />

              <div className="absolute bottom-md left-md right-[52px] flex items-center gap-sm">
                {attachment ? (
                  <div className="flex min-w-0 items-center gap-xs rounded-full bg-surface-variant px-sm py-1 text-on-surface">
                    <span className="material-symbols-outlined text-base">attach_file</span>
                    <span className="truncate font-body-sm text-body-sm">{attachment.name}</span>
                    <button
                      aria-label={`Remove ${attachment.name}`}
                      className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full hover:bg-surface-container-high"
                      onClick={() => {
                        attachmentReadRef.current += 1
                        setAttachment(null)
                        setAttachmentError(null)
                      }}
                      type="button"
                    >
                      <span className="material-symbols-outlined text-base">close</span>
                    </button>
                  </div>
                ) : (
                  <label
                    aria-label="Attach a bill of materials"
                    className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-full text-on-surface-variant transition-colors hover:bg-surface-variant hover:text-primary"
                    title="Attach a .csv or .txt bill of materials"
                  >
                    <span className="material-symbols-outlined text-xl">add</span>
                    <span className="sr-only">Attach a bill of materials</span>
                    <input
                      accept=".csv,.txt,text/csv,text/plain"
                      aria-label="Choose a .csv or .txt bill of materials"
                      className="sr-only"
                      onChange={handleAttachmentChange}
                      type="file"
                    />
                  </label>
                )}
              </div>

              <button
                aria-label="Start validation"
                className="absolute bottom-md right-md w-8 h-8 rounded-full bg-primary-container text-on-primary-fixed flex items-center justify-center hover:bg-primary transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                disabled={!canSubmit}
                type="submit"
              >
                <span
                  className="material-symbols-outlined text-lg"
                  style={{ fontVariationSettings: "'FILL' 1" }}
                >
                  arrow_upward
                </span>
              </button>
            </div>

            {attachmentError ? (
              <p className="mt-sm font-body-sm text-body-sm text-error" role="alert">
                {attachmentError}
              </p>
            ) : null}
          </form>

          <div className="flex flex-col gap-md mt-sm">
            <span className="font-label-caps text-label-caps text-on-surface-variant uppercase">
              TRY ONE
            </span>
            <div className="flex flex-wrap gap-sm">
              {SAMPLE_BRIEFS.map((sample) => (
                <button
                  className="px-md py-sm rounded-DEFAULT border border-outline-variant bg-surface-container-low hover:bg-surface-variant transition-colors text-left font-data-tabular text-data-tabular text-on-surface text-xs leading-relaxed max-w-full"
                  key={sample}
                  onClick={() => setBrief(sample)}
                  type="button"
                >
                  {sample}
                </button>
              ))}
            </div>
          </div>
        </div>
      </main>
      </div>
    </>
  )
}
