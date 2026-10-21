import type { ReactNode } from 'react'
import { Navigate } from 'react-router'

import { useAuth } from '../hooks/useAuth'

type RequireAuthProps = {
  children: ReactNode
}

export function RequireAuth({ children }: RequireAuthProps) {
  const { user, loading } = useAuth()

  if (loading) {
    return null
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}
