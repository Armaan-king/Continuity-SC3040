import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

import { BriefEntry } from '../design/BriefEntry'
import { Walkthrough } from '../design/Walkthrough'
import { WorkspaceView } from '../design/Workspace'
import { useAuth } from '../hooks/useAuth'
import { useDesignSession } from '../hooks/useDesignSession'
import { releaseWalkthroughReplay } from '../lib/sseClient'
import { walkthroughBrief } from '../lib/walkthroughReplay'

export default function WalkthroughRoute() {
  const navigate = useNavigate()
  const { refresh } = useAuth()
  const session = useDesignSession()
  const [workspaceVisible, setWorkspaceVisible] = useState(false)
  const [briefSubmitted, setBriefSubmitted] = useState(false)
  const [tourStep, setTourStep] = useState(0)
  const startWalkthroughRef = useRef(session.startWalkthrough)
  const walkthroughStarted = useRef(false)

  startWalkthroughRef.current = session.startWalkthrough

  useEffect(() => {
    if (session.status === 'error') {
      navigate('/projects', { replace: true })
    }
  }, [navigate, session.status])

  const finish = async () => {
    session.cancel()
    await refresh()
    navigate('/projects', { replace: true })
  }

  const start = () => {
    if (walkthroughStarted.current) {
      return
    }
    walkthroughStarted.current = true
    startWalkthroughRef.current()
    setWorkspaceVisible(true)
    setBriefSubmitted(true)
  }

  const enterStep = (step: number) => {
    setTourStep(step)
    if (step === 1) {
      releaseWalkthroughReplay('plan')
    }
    if (step === 2) {
      releaseWalkthroughReplay('trace')
    }
    if (step === 3) {
      releaseWalkthroughReplay('conflict')
    }
    if (step === 5) {
      releaseWalkthroughReplay('bom')
    }
    if (step === 6) {
      releaseWalkthroughReplay('complete')
      window.setTimeout(() => {
        navigate('/memory', { state: { walkthrough: true }, replace: true })
      }, 150)
    }
  }

  return (
    <>
      {workspaceVisible ? (
        <WorkspaceView
          session={session}
          walkthroughStep={tourStep}
          walkthrough
        />
      ) : (
        <BriefEntry onStarted={start} walkthroughBrief={walkthroughBrief} />
      )}
      <Walkthrough briefSubmitted={briefSubmitted} onFinish={finish} onStepChange={enterStep} />
    </>
  )
}
