import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router'

import { createProject } from '../lib/api'
import { useAuth } from './useAuth'

export function useNewProject() {
  const navigate = useNavigate()
  const { runtime } = useAuth()
  const [creating, setCreating] = useState(false)

  const createNewProject = useCallback(async () => {
    if (creating) {
      return
    }
    if (runtime?.accounts === false) {
      navigate('/design')
      return
    }

    setCreating(true)
    try {
      const created = await createProject('Untitled board')
      navigate(`/design/${created.id}`)
    } finally {
      setCreating(false)
    }
  }, [creating, navigate, runtime])

  return { createNewProject, creating }
}
