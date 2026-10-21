import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { useAuth } from '../hooks/useAuth'
import { useNewProject } from '../hooks/useNewProject'

type RailButtonProps = {
  active?: boolean
  disabled?: boolean
  icon: string
  label: string
  onClick: () => void
}

function RailButton({ active = false, disabled = false, icon, label, onClick }: RailButtonProps) {
  return (
    <button
      aria-current={active ? 'page' : undefined}
      aria-label={label}
      className={`w-10 h-10 rounded flex items-center justify-center group relative focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-container focus-visible:outline-offset-2 disabled:opacity-40 disabled:cursor-not-allowed ${
        active
          ? 'text-primary-container bg-surface-container-high border-l-2 border-primary-container'
          : 'text-on-surface-variant opacity-70 hover:text-primary hover:bg-surface-container transition-colors'
      }`}
      onClick={onClick}
      disabled={disabled}
      type="button"
    >
      <span
        className="material-symbols-outlined text-[20px]"
        style={active ? { fontVariationSettings: "'FILL' 1" } : undefined}
      >
        {icon}
      </span>
      <span className="pointer-events-none absolute left-[calc(100%+10px)] top-1/2 -translate-y-1/2 whitespace-nowrap bg-surface-container-high border border-outline-variant px-sm py-xs font-label-caps text-label-caps text-on-surface opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100 transition-opacity z-50">
        {label}
      </span>
    </button>
  )
}

export function SideRail() {
  const location = useLocation()
  const navigate = useNavigate()
  const { user, signOut } = useAuth()
  const { createNewProject, creating } = useNewProject()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const settingsRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const closeOnOutsidePointer = (event: MouseEvent) => {
      if (!settingsRef.current?.contains(event.target as Node)) {
        setSettingsOpen(false)
      }
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSettingsOpen(false)
      }
    }
    document.addEventListener('mousedown', closeOnOutsidePointer)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsidePointer)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [])

  const handleSignOut = useCallback(async () => {
    await signOut()
    navigate('/login', { replace: true })
  }, [navigate, signOut])

  return (
    <nav aria-label="Primary navigation" className="fixed left-0 top-0 bottom-0 w-16 flex flex-col items-center py-md z-40 bg-surface-container-lowest dark:bg-surface-container-lowest border-r border-outline-variant transition-all duration-150 ease-in-out">
      <div className="flex flex-col gap-sm w-full items-center">
        <RailButton disabled={creating} icon="add" label="New project" onClick={() => { createNewProject().catch(() => undefined) }} />
        <RailButton active={location.pathname === '/projects'} icon="folder_open" label="All projects" onClick={() => navigate('/projects')} />
        {/* `hub` — nodes and the links between them, which is literally what /memory shows.
            It was `memory`, a chip glyph, sitting one rail away from the wordmark's
            `developer_board` chip: two chips for two unrelated things. */}
        <RailButton active={location.pathname === '/memory'} icon="hub" label="Memory" onClick={() => navigate('/memory')} />
        <RailButton active={location.pathname === '/analytics'} icon="monitoring" label="Analytics" onClick={() => navigate('/analytics')} />
      </div>

      <div className="mt-auto flex flex-col gap-sm w-full items-center border-t border-outline-variant pt-sm">
        <div className="relative" ref={settingsRef}>
          <RailButton icon="settings" label="Settings" onClick={() => setSettingsOpen((open) => !open)} />
          {settingsOpen ? (
            <div className="absolute bottom-0 left-[calc(100%+10px)] z-50 w-64 border border-outline-variant bg-surface-container-high p-sm shadow-lg">
              <p className="m-0 px-sm py-xs font-data-tabular text-body-sm text-on-surface-variant truncate" title={user?.email}>
                {user?.email}
              </p>
              <button
                className="w-full text-left px-sm py-xs font-label-caps text-label-caps text-on-surface hover:bg-surface-container-highest focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-container"
                onClick={() => { handleSignOut().catch(() => undefined) }}
                type="button"
              >
                SIGN_OUT
              </button>
            </div>
          ) : null}
        </div>
        <RailButton active={location.pathname === '/walkthrough'} icon="help" label="Help and walkthrough" onClick={() => navigate('/walkthrough')} />
      </div>
    </nav>
  )
}
