import type { ReactNode } from 'react'
import { Navigate } from 'react-router'

import { useAuth } from '../hooks/useAuth'

type RequireAuthProps = {
  children: ReactNode
}

export function RequireAuth({ children }: RequireAuthProps) {
  const { user, loading, runtime } = useAuth()

  if (loading) {
    return <p className="p-xl text-on-surface-variant" role="status">Connecting to the workspace…</p>
  }

  if (runtime?.accounts === false) return <Navigate to="/design" replace />

  if (!user) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}
