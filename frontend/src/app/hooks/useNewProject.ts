import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router'

import { createProject } from '../lib/api'

export function useNewProject() {
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)

  const createNewProject = useCallback(async () => {
    if (creating) {
      return
    }

    setCreating(true)
    try {
      const created = await createProject('Untitled board')
      navigate(`/design/${created.id}`)
    } finally {
      setCreating(false)
    }
  }, [creating, navigate])

  return { createNewProject, creating }
}
