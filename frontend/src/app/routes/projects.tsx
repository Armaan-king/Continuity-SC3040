import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

import { Modal } from '../design/Modal'
import { useNewProject } from '../hooks/useNewProject'
import {
  deleteProject,
  listProjectThreads,
  listProjects,
  updateProject,
  type Project,
  type ProjectThread,
} from '../lib/api'
import { Wordmark } from '../shell/Wordmark'

type StatusBadge = {
  label: string
  dotClassName: string
  textClassName: string
  hollowDot?: boolean
}

function formatRelativeTime(timestamp: string) {
  const createdAt = new Date(timestamp).getTime()
  if (Number.isNaN(createdAt)) {
    return 'just now'
  }

  const seconds = Math.max(0, Math.floor((Date.now() - createdAt) / 1000))
  if (seconds < 60) {
    return 'just now'
  }

  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) {
    return `${minutes}m ago`
  }

  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    return `${hours}h ago`
  }

  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

function conflictResolvedLabel(count: number) {
  return `${count} conflict${count === 1 ? '' : 's'} resolved`
}

function shortPartsLabel(missingCount: number) {
  return `short ${missingCount} part${missingCount === 1 ? '' : 's'}`
}

function statusBadgeFromLatestThread(latestThread: ProjectThread | null): StatusBadge {
  if (!latestThread) {
    return {
      label: 'Never run',
      dotClassName: 'border border-outline',
      textClassName: 'text-on-surface-variant',
      hollowDot: true,
    }
  }

  const status = latestThread.status.toLowerCase()

  if (status === 'running') {
    return {
      label: 'RUNNING',
      dotClassName: 'bg-tertiary-container',
      textClassName: 'text-tertiary-fixed-dim',
    }
  }

  if (status === 'awaiting' || status === 'awaiting_input') {
    return {
      label: 'NEEDS INPUT',
      dotClassName: 'bg-tertiary-container',
      textClassName: 'text-tertiary-fixed-dim',
    }
  }

  if (status === 'error') {
    return {
      label: 'Failed',
      dotClassName: 'bg-error',
      textClassName: 'text-error',
    }
  }

  // A run whose client went away. Deliberately not 'Failed' — nothing broke, the tab
  // was closed, and a red badge would report a fault the user did not cause.
  if (status === 'abandoned') {
    return {
      label: 'Stopped',
      dotClassName: 'bg-outline',
      textClassName: 'text-on-surface-variant',
    }
  }

  if (status === 'done' || status === 'completed') {
    if (latestThread.summary && latestThread.summary.conflicts_resolved > 0) {
      return {
        label: conflictResolvedLabel(latestThread.summary.conflicts_resolved),
        dotClassName: 'bg-primary-container',
        textClassName: 'text-on-surface-variant',
      }
    }

    return {
      label: 'Completed',
      dotClassName: 'bg-outline',
      textClassName: 'text-on-surface-variant',
    }
  }

  return {
    label: 'Completed',
    dotClassName: 'bg-outline',
    textClassName: 'text-on-surface-variant',
  }
}

function shortageLabelFromLatestThread(latestThread: ProjectThread | null): string | null {
  if (!latestThread?.summary) {
    return null
  }

  const missingCount = latestThread.summary.slots - latestThread.summary.placed
  if (missingCount <= 0) {
    return null
  }

  return shortPartsLabel(missingCount)
}

export default function ProjectsRoute() {
  const navigate = useNavigate()
  const { createNewProject, creating } = useNewProject()

  const [projects, setProjects] = useState<Project[]>([])
  const [threadsByProject, setThreadsByProject] = useState<Record<string, ProjectThread[]>>({})
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [openMenuProjectId, setOpenMenuProjectId] = useState<string | null>(null)
  const [reloadCount, setReloadCount] = useState(0)

  const [renameProject, setRenameProject] = useState<Project | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const renameInputRef = useRef<HTMLInputElement | null>(null)

  const [deleteProjectTarget, setDeleteProjectTarget] = useState<Project | null>(null)

  useEffect(() => {
    if (!renameProject) {
      return
    }

    const frame = requestAnimationFrame(() => {
      renameInputRef.current?.select()
    })

    return () => {
      cancelAnimationFrame(frame)
    }
  }, [renameProject])

  useEffect(() => {
    let active = true

    async function load() {
      setLoading(true)

      try {
        const nextProjects = await listProjects()
        if (!active) {
          return
        }

        setLoadError(false)
        setProjects(nextProjects)

        const threadEntries = await Promise.all(
          nextProjects.map(async (project) => {
            try {
              const threads = await listProjectThreads(project.id)
              return [project.id, threads] as const
            } catch {
              return [project.id, []] as const
            }
          }),
        )

        if (!active) {
          return
        }

        setThreadsByProject(Object.fromEntries(threadEntries))
      } catch {
        if (active) {
          setLoadError(true)
        }
      } finally {
        if (active) {
          setLoading(false)
        }
      }
    }

    load().catch(() => {
      if (active) {
        setLoadError(true)
        setLoading(false)
      }
    })

    return () => {
      active = false
    }
  }, [reloadCount])

  const openRenameModal = useCallback((project: Project) => {
    setRenameProject(project)
    setRenameValue(project.name)
    setOpenMenuProjectId(null)
  }, [])

  const closeRenameModal = useCallback(() => {
    setRenameProject(null)
    setOpenMenuProjectId(null)
  }, [])

  const confirmRename = useCallback(async () => {
    if (!renameProject) {
      return
    }

    const nextName = renameValue.trim()
    if (!nextName || nextName === renameProject.name) {
      closeRenameModal()
      return
    }

    const updated = await updateProject(renameProject.id, nextName)
    setProjects((previous) =>
      previous.map((item) => (item.id === updated.id ? { ...item, ...updated } : item)),
    )
    closeRenameModal()
  }, [closeRenameModal, renameProject, renameValue])

  const openDeleteModal = useCallback((project: Project) => {
    setDeleteProjectTarget(project)
    setOpenMenuProjectId(null)
  }, [])

  const closeDeleteModal = useCallback(() => {
    setDeleteProjectTarget(null)
    setOpenMenuProjectId(null)
  }, [])

  const confirmDelete = useCallback(async () => {
    if (!deleteProjectTarget) {
      return
    }

    await deleteProject(deleteProjectTarget.id)

    setProjects((previous) => previous.filter((item) => item.id !== deleteProjectTarget.id))
    setThreadsByProject((previous) => {
      const next = { ...previous }
      delete next[deleteProjectTarget.id]
      return next
    })

    closeDeleteModal()
  }, [closeDeleteModal, deleteProjectTarget])

  const showLoadError = !loading && loadError
  const showEmptyState = !loading && !loadError && projects.length === 0
  const showRows = !loading && !loadError

  const rows = useMemo(
    () =>
      projects.map((project) => {
        const threads = threadsByProject[project.id] ?? []
        const latestThread = threads[0] ?? null

        return {
          project,
          subtitle: latestThread?.prompt ?? 'No runs yet',
          status: statusBadgeFromLatestThread(latestThread),
          shortageLabel: shortageLabelFromLatestThread(latestThread),
        }
      }),
    [projects, threadsByProject],
  )

  return (
    <>
      <div className="bg-transparent min-h-screen text-on-background font-body-md antialiased">
        <header className="flex items-center w-full px-lg h-12 bg-surface-container-low border-b border-outline-variant shadow-[0_1px_0_0_rgba(255,255,255,0.05)]">
          <Wordmark />
        </header>

        <main className="min-h-[calc(100vh-48px)] max-w-[1200px] mx-auto px-lg py-xl flex flex-col gap-lg">
          <div className="flex items-center justify-between border-b border-outline-variant pb-sm">
            <h1 className="font-label-caps text-label-caps tracking-[0.1em] uppercase text-on-surface">
              PROJECTS
            </h1>
            <button
              className="bg-primary-container text-on-primary-fixed px-md py-xs rounded-DEFAULT font-label-caps text-label-caps flex items-center gap-xs hover:bg-primary-fixed transition-colors disabled:opacity-70"
              disabled={creating}
              onClick={() => {
                createNewProject().catch(() => undefined)
              }}
              type="button"
            >
              <span className="material-symbols-outlined text-[16px]">add</span>
              NEW_PROJECT
            </button>
          </div>

          <div className="flex flex-col gap-sm">
            {showRows
              ? rows.map(({ project, subtitle, status, shortageLabel }) => (
                  <div
                    // Separated by surface and space rather than a hairline. A 1px border
                    // around every card is the other reliable generated-UI tell, and the
                    // list reads calmer without twelve of them stacked down the page.
                    className="bg-surface-container h-[72px] rounded-DEFAULT flex items-center justify-between px-md hover:bg-surface-container-high transition-colors cursor-pointer"
                    key={project.id}
                    onClick={() => navigate(`/design/${project.id}`)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        navigate(`/design/${project.id}`)
                      }
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <div className="flex flex-col justify-center min-w-0 flex-1">
                      <span className="font-headline-sm text-headline-sm text-on-surface truncate">
                        {project.name}
                      </span>
                      <span className="font-data-tabular text-body-sm text-on-surface-variant truncate">
                        {subtitle}
                      </span>
                    </div>

                    <div className="ml-lg flex items-center gap-lg shrink-0">
                      <div className="flex items-center gap-sm min-w-[260px]">
                        <div className="flex items-center gap-xs min-w-[120px]">
                          <span
                            className={`w-1.5 h-1.5 rounded-full ${status.dotClassName} ${
                              status.hollowDot ? 'bg-transparent' : ''
                            }`}
                          />
                          <span className={`font-label-caps text-label-caps ${status.textClassName}`}>
                            {status.label}
                          </span>
                        </div>

                        {shortageLabel ? (
                          <span className="font-label-caps text-label-caps text-error">
                            {shortageLabel}
                          </span>
                        ) : null}
                      </div>

                      <span className="font-data-tabular text-data-tabular text-outline min-w-[64px] text-right">
                        {formatRelativeTime(project.updated_at)}
                      </span>

                      <div className="relative">
                        <button
                          aria-label="Project actions"
                          className="p-1 text-on-surface-variant hover:text-on-surface rounded-DEFAULT hover:bg-surface-container-high transition-colors"
                          onClick={(event) => {
                            event.stopPropagation()
                            setOpenMenuProjectId((current) =>
                              current === project.id ? null : project.id,
                            )
                          }}
                          type="button"
                        >
                          <span className="material-symbols-outlined text-[20px]">more_vert</span>
                        </button>

                        {openMenuProjectId === project.id ? (
                          <div
                            className="absolute right-0 top-[calc(100%+4px)] z-10 w-[140px] bg-surface-container-high border border-outline-variant rounded-DEFAULT overflow-hidden shadow-lg"
                            onClick={(event) => event.stopPropagation()}
                          >
                            <button
                              className="w-full text-left px-sm py-xs font-body-sm text-body-sm text-on-surface hover:bg-surface-container-highest"
                              onClick={() => {
                                openRenameModal(project)
                              }}
                              type="button"
                            >
                              Rename
                            </button>
                            <button
                              className="w-full text-left px-sm py-xs font-body-sm text-body-sm text-error hover:bg-surface-container-highest"
                              onClick={() => {
                                openDeleteModal(project)
                              }}
                              type="button"
                            >
                              Delete
                            </button>
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </div>
                ))
              : null}

            {showLoadError ? (
              <div className="border border-error rounded-DEFAULT bg-error-container/20 p-md flex flex-col gap-sm">
                <div className="flex items-center gap-sm">
                  <span className="material-symbols-outlined text-error text-[16px]">warning</span>
                  <span className="font-body-md text-body-md text-error">Could not load your projects</span>
                </div>
                <p className="font-body-sm text-body-sm text-on-surface-variant">
                  The server did not respond.
                </p>
                <div>
                  <button
                    className="font-label-caps text-label-caps text-on-surface-variant hover:text-on-surface transition-colors"
                    onClick={() => {
                      setReloadCount((current) => current + 1)
                    }}
                    type="button"
                  >
                    RETRY
                  </button>
                </div>
              </div>
            ) : null}

            {showEmptyState ? (
              <div className="w-full border-2 border-dashed border-outline-variant rounded-xl p-xl flex flex-col items-center justify-center text-center bg-surface-container-low/30 mt-md">
                <div className="w-20 h-20 rounded-full bg-surface-container-high border border-outline-variant flex items-center justify-center mb-lg">
                  <span className="material-symbols-outlined text-[40px] text-on-surface-variant">memory</span>
                </div>
                <h2 className="font-headline-sm text-headline-sm text-on-surface mb-sm">No projects yet</h2>
                <p className="font-body-md text-body-md text-on-surface-variant max-w-md mb-xl">
                  Describe a board and Continuity will source and validate it
                </p>
                <button
                  className="bg-primary-container text-on-primary-fixed px-xl py-sm rounded-DEFAULT font-label-caps text-label-caps hover:bg-primary-fixed transition-colors flex items-center gap-sm"
                  disabled={creating}
                  onClick={() => {
                      createNewProject().catch(() => undefined)
                  }}
                  type="button"
                >
                  <span className="material-symbols-outlined text-[16px]">add</span>
                  NEW_PROJECT
                </button>
              </div>
            ) : null}
          </div>
        </main>
      </div>

      <Modal
        confirmLabel="SAVE"
        onClose={closeRenameModal}
        onConfirm={() => {
          confirmRename().catch(() => undefined)
        }}
        open={Boolean(renameProject)}
        title="RENAME PROJECT"
      >
        <input
          className="w-full bg-surface-container-lowest border border-outline-variant rounded-DEFAULT px-sm py-sm font-data-tabular text-data-tabular text-on-surface focus:outline-none focus:ring-0 glow-focus"
          onChange={(event) => setRenameValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              confirmRename().catch(() => undefined)
            }
          }}
          ref={renameInputRef}
          value={renameValue}
        />
      </Modal>

      <Modal
        confirmLabel="DELETE"
        destructive
        onClose={closeDeleteModal}
        onConfirm={() => {
          confirmDelete().catch(() => undefined)
        }}
        open={Boolean(deleteProjectTarget)}
        title="DELETE PROJECT"
      >
        <p className="font-body-md text-body-md text-on-surface-variant">
          Delete "{deleteProjectTarget?.name}"? This removes every run in this project and cannot be
          undone.
        </p>
      </Modal>
    </>
  )
}
