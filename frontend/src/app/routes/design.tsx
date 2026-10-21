import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'

import { BriefEntry } from '../design/BriefEntry'
import { Workspace, WorkspaceView } from '../design/Workspace'
import { useDesignSession } from '../hooks/useDesignSession'
import { ApiError, getThreadBoard, listProjectThreads, type ProjectThread } from '../lib/api'

type ProjectMode = 'loading' | 'brief' | 'workspace'

type StartedRequest = {
  brief: string
  bom?: string
}

function StartedWorkspace({ projectId, request }: { projectId: string; request: StartedRequest }) {
  const session = useDesignSession()

  useEffect(() => {
    if (request.bom !== undefined) {
      session.startBom(request.bom, request.brief || undefined, projectId)
      return
    }

    session.start(request.brief, projectId)
  }, [projectId, request, session.start, session.startBom])

  return <WorkspaceView projectId={projectId} session={session} />
}

type RestoreState = 'loading' | 'ready' | 'live' | 'empty' | 'error'

function RestoredWorkspace({ projectId, thread }: { projectId: string; thread: ProjectThread }) {
  const navigate = useNavigate()
  const session = useDesignSession()
  const [restoreState, setRestoreState] = useState<RestoreState>('loading')

  useEffect(() => {
    let active = true

    async function restore() {
      try {
        const board = await getThreadBoard(thread.id)
        if (!active) {
          return
        }

        if (board.status === 'running') {
          setRestoreState('live')
          return
        }

        if (board.slots.length === 0 && board.bom?.rows.length === 0 && board.trace.length === 0 && !board.question) {
          setRestoreState('empty')
          return
        }

        session.hydrate(board, thread.id)
        setRestoreState('ready')
      } catch (error) {
        if (!active) {
          return
        }

        if (error instanceof ApiError && error.status === 401) {
          navigate('/login', { replace: true })
          return
        }

        if (error instanceof ApiError && error.status === 404) {
          navigate('/projects', { replace: true })
          return
        }

        setRestoreState('error')
      }
    }

    restore()

    return () => {
      active = false
    }
  }, [navigate, session.hydrate, thread.id])

  if (restoreState === 'loading') {
    return null
  }

  if (restoreState === 'live') {
    return <Workspace projectId={projectId} />
  }

  if (restoreState === 'empty' || restoreState === 'error') {
    return (
      <div className="min-h-screen bg-background text-on-background flex items-center justify-center p-lg">
        <div className="border border-outline-variant bg-surface-container p-lg max-w-md">
          <p className="m-0 font-headline-sm text-headline-sm">
            {restoreState === 'empty' ? 'THIS RUN HAS NO BOARD TO RESTORE' : 'BOARD COULD NOT LOAD'}
          </p>
          <p className="mt-sm text-on-surface-variant">
            {restoreState === 'empty'
              ? 'Its saved run contains neither a graph checkpoint nor a bill of materials.'
              : 'The saved board could not be retrieved. Please try again.'}
          </p>
        </div>
      </div>
    )
  }

  return <WorkspaceView projectId={projectId} session={session} />
}

/**
 * One route, two states.
 *
 * A project with no runs yet shows the brief screen; once a run exists it shows the
 * workspace. They are the same URL because they are the same thing at two moments — a
 * project begins by being described.
 *
 * The started brief is held *here* rather than inside `BriefEntry` on purpose. Starting
 * the run in the child and then swapping components would unmount `Workspace` a moment
 * after it opened the SSE connection, and the run would vanish with no error — the
 * stream would simply stop. Holding it at this level means `Workspace` mounts exactly
 * once and keeps its connection.
 */
export default function DesignRoute() {
  const navigate = useNavigate()
  const { projectId } = useParams<{ projectId: string }>()

  const [mode, setMode] = useState<ProjectMode>(projectId ? 'loading' : 'workspace')
  const [startedRequest, setStartedRequest] = useState<StartedRequest | null>(null)
  const [latestThread, setLatestThread] = useState<ProjectThread | null>(null)

  useEffect(() => {
    if (!projectId) {
      // Single-user local mode: no accounts, no projects, nothing to look up.
      setMode('workspace')
      setStartedRequest(null)
      setLatestThread(null)
      return
    }

    // Captured so the async closure below has a `string` rather than `string | undefined`.
    const id = projectId
    let active = true

    setMode('loading')
    setStartedRequest(null)
    setLatestThread(null)

    async function decide() {
      try {
        // One request, not two: the threads endpoint already 404s for a project that
        // does not exist *or* belongs to somebody else, so a separate existence check
        // would only be a second round trip to learn the same thing.
        const threads = await listProjectThreads(id)
        if (active) {
          setLatestThread(threads[0] ?? null)
          setMode(threads.length === 0 ? 'brief' : 'workspace')
        }
      } catch {
        // 404 means it is not this user's project. Anything else means we cannot tell
        // which screen is correct, and guessing would either hide an existing run behind
        // the brief screen or open an empty workspace. The dashboard is the honest place
        // to land, and it has its own error state.
        if (active) {
          navigate('/projects', { replace: true })
        }
      }
    }

    decide()

    return () => {
      active = false
    }
  }, [navigate, projectId])

  if (!projectId) {
    return <Workspace />
  }

  // Render nothing until the answer arrives — flashing the brief screen at somebody who
  // already has a board is worse than a beat of blankness.
  if (mode === 'loading') {
    return null
  }

  if (mode === 'brief') {
    return (
      <BriefEntry
        onStarted={(brief, bom) => {
          setStartedRequest({ brief, bom })
          setMode('workspace')
        }}
        projectId={projectId}
      />
    )
  }

  return startedRequest ? (
    <StartedWorkspace projectId={projectId} request={startedRequest} />
  ) : latestThread && latestThread.status !== 'running' ? (
    <RestoredWorkspace projectId={projectId} thread={latestThread} />
  ) : (
    <Workspace projectId={projectId} />
  )
}
