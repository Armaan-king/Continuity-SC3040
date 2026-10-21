import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import {
  ApiError,
  login as loginRequest,
  logout as logoutRequest,
  me,
  register,
  type PublicUser,
} from '../lib/api'

type AuthContextValue = {
  user: PublicUser | null
  loading: boolean
  signIn: (email: string, password: string) => Promise<PublicUser>
  signUp: (email: string, password: string) => Promise<PublicUser>
  signOut: () => Promise<void>
  refresh: () => Promise<PublicUser | null>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<PublicUser | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const nextUser = await me()
      setUser(nextUser)
      return nextUser
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setUser(null)
        return null
      }

      throw error
    }
  }, [])

  useEffect(() => {
    refresh()
      .catch(() => {
        setUser(null)
      })
      .finally(() => {
        setLoading(false)
      })
  }, [refresh])

  const signIn = useCallback(async (email: string, password: string) => {
    const nextUser = await loginRequest(email, password)
    setUser(nextUser)
    return nextUser
  }, [])

  const signUp = useCallback(async (email: string, password: string) => {
    const nextUser = await register(email, password)
    setUser(nextUser)
    return nextUser
  }, [])

  const signOut = useCallback(async () => {
    await logoutRequest()
    setUser(null)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      signIn,
      signUp,
      signOut,
      refresh,
    }),
    [loading, refresh, signIn, signOut, signUp, user],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)

  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }

  return context
}
