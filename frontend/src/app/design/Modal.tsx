import { useEffect, useRef, type ReactNode } from 'react'

type ModalProps = {
  open: boolean
  title: string
  onClose: () => void
  onConfirm: () => void
  confirmLabel: string
  destructive?: boolean
  children: ReactNode
}

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Modal({
  open,
  title,
  onClose,
  onConfirm,
  confirmLabel,
  destructive = false,
  children,
}: ModalProps) {
  const cardRef = useRef<HTMLDivElement | null>(null)
  const lastFocusedRef = useRef<HTMLElement | null>(null)
  const hadOpenedRef = useRef(false)

  useEffect(() => {
    if (open) {
      hadOpenedRef.current = true
      lastFocusedRef.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null

      const frame = requestAnimationFrame(() => {
        const firstFocusable = cardRef.current?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR)
        firstFocusable?.focus()
      })

      return () => {
        cancelAnimationFrame(frame)
      }
    }

    if (hadOpenedRef.current) {
      hadOpenedRef.current = false
      lastFocusedRef.current?.focus()
    }
  }, [open])

  useEffect(() => {
    if (!open) {
      return
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [open, onClose])

  if (!open) {
    return null
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-lg"
      onClick={(event) => {
        if (event.target !== event.currentTarget) {
          return
        }

        if (!destructive) {
          onClose()
        }
      }}
    >
      <div
        className="w-full max-w-[400px] bg-surface-container border border-outline-variant rounded-lg p-xl flex flex-col gap-lg"
        ref={cardRef}
      >
        <h2 className="font-label-caps text-label-caps uppercase tracking-widest text-on-surface">
          {title}
        </h2>

        <div>{children}</div>

        <div className="flex items-center justify-between gap-md pt-xs">
          <button
            className="font-label-caps text-label-caps text-on-surface-variant hover:text-on-surface transition-colors"
            onClick={onClose}
            type="button"
          >
            CANCEL
          </button>

          <button
            className={`px-md py-xs rounded-DEFAULT font-label-caps text-label-caps transition-colors ${
              destructive
                ? 'bg-error text-on-error hover:opacity-90'
                : 'bg-primary-container text-on-primary-fixed hover:bg-primary-fixed'
            }`}
            onClick={onConfirm}
            type="button"
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
