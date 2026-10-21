import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'

import { ApiError, type PublicUser } from '../lib/api'
import { useAuth } from '../hooks/useAuth'
import { Wordmark } from '../shell/Wordmark'

type AuthMode = 'signin' | 'signup'

type AuthCardProps = {
  mode: AuthMode
}

/** One rule for where a signed-in user goes, used by both the guard and the submit. */
function landingRouteFor(user: PublicUser) {
  return user.onboarded ? '/projects' : '/walkthrough'
}

function getErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return 'Email or password is incorrect'
    }

    if (error.status === 409) {
      return 'An account with that email already exists'
    }

    if (error.status === 422) {
      return 'Enter a valid email and a password of at least 8 characters'
    }
  }

  return 'Could not reach the server'
}

function AuthCard({ mode }: AuthCardProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, loading, signIn, signUp } = useAuth()

  const [email, setEmail] = useState(() => {
    const state = location.state as { email?: unknown } | null
    return typeof state?.email === 'string' ? state.email : ''
  })
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  if (loading) {
    return null
  }

  // Where a signed-in visitor to /login or /signup belongs. This has to agree with the
  // redirect after a successful submit, because it *races* it: signing in populates the
  // auth context, this component re-renders, and this guard navigates before the explicit
  // one below gets to. Hard-coding /projects here silently swallowed every new account's
  // walkthrough.
  if (user) {
    return <Navigate replace to={landingRouteFor(user)} />
  }

  const isSignIn = mode === 'signin'
  const submitLabel = isSignIn ? 'SIGN_IN' : 'CREATE_ACCOUNT'
  const submittingLabel = isSignIn ? 'SIGNING_IN…' : 'CREATING…'
  const sectionLabel = isSignIn ? 'SIGN IN' : 'CREATE ACCOUNT'

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) {
      return
    }

    setSubmitting(true)
    setErrorMessage(null)

    try {
      const nextUser = isSignIn
        ? await signIn(email, password)
        : await signUp(email, password)

      navigate(landingRouteFor(nextUser), { replace: true })
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    // Transparent, and no `grid-bg`. `AppFrame` already paints the shared PCB background
    // behind every non-workspace route — this page was covering it with an opaque
    // `bg-background` and then drawing a second, different grid on top, so the two screens
    // that precede every session were the only ones without the product's own backdrop.
    <div className="min-h-screen bg-transparent flex items-center justify-center p-container-margin text-on-background font-body-md antialiased">
      <div className="w-full max-w-[400px] bg-surface-container border border-outline-variant rounded-lg p-xl shadow-2xl flex flex-col gap-lg">
        <div className="flex flex-col items-center gap-md">
          <Wordmark size="lg" />
          <h1 className="font-label-caps text-label-caps uppercase tracking-widest text-primary-container">
            {sectionLabel}
          </h1>
        </div>

        <form className="flex flex-col gap-lg" onSubmit={handleSubmit}>
          <div className="flex items-center bg-surface-container-lowest border border-outline-variant rounded-DEFAULT glow-focus transition-all">
            <span className="material-symbols-outlined text-outline pl-sm text-[18px]">mail</span>
            <input
              autoComplete="email"
              className="w-full bg-transparent border-none focus:ring-0 text-on-surface font-data-tabular text-data-tabular py-sm px-sm placeholder:font-data-tabular placeholder:text-outline"
              onChange={(event) => setEmail(event.target.value)}
              placeholder="ENTER_EMAIL"
              required
              type="email"
              value={email}
            />
          </div>

          <div className="flex flex-col gap-xs">
            <div className="relative flex items-center bg-surface-container-lowest border border-outline-variant rounded-DEFAULT glow-focus transition-all">
              <span className="material-symbols-outlined text-outline pl-sm text-[18px]">lock</span>
              <input
                autoComplete={isSignIn ? 'current-password' : 'new-password'}
                className="w-full bg-transparent border-none focus:ring-0 text-on-surface font-data-tabular text-data-tabular py-sm pl-sm pr-[44px] placeholder:font-data-tabular placeholder:text-outline"
                onChange={(event) => setPassword(event.target.value)}
                placeholder="ENTER_PASSWORD"
                required
                type={showPassword ? 'text' : 'password'}
                value={password}
              />
              <button
                className="absolute right-sm text-outline hover:text-primary-container transition-colors"
                onClick={() => setShowPassword((current) => !current)}
                type="button"
              >
                <span className="material-symbols-outlined text-[18px]">
                  {showPassword ? 'visibility_off' : 'visibility'}
                </span>
              </button>
            </div>
            {!isSignIn ? (
              <p className="font-body-sm text-body-sm text-on-surface-variant pl-xs">
                8 characters minimum
              </p>
            ) : null}
          </div>

          {errorMessage ? (
            <div className="bg-error-container/20 border border-error rounded-DEFAULT py-xs px-sm flex items-center gap-sm">
              <span className="material-symbols-outlined text-error text-[16px]">warning</span>
              <span className="font-body-sm text-body-sm text-error">{errorMessage}</span>
            </div>
          ) : null}

          <button
            className="w-full bg-primary-container text-on-primary-fixed font-headline-sm text-headline-sm py-md px-lg rounded-DEFAULT hover:bg-primary-fixed transition-colors disabled:opacity-70 disabled:cursor-not-allowed"
            disabled={submitting}
            type="submit"
          >
            {submitting ? submittingLabel : submitLabel}
          </button>
        </form>

        <div className="border-t border-outline-variant" />

        <div className="text-center">
          {isSignIn ? (
            <p className="font-body-sm text-body-sm text-on-surface-variant">
              No account?{' '}
              <Link
                className="text-primary-container hover:text-primary-fixed underline decoration-primary-container/30 underline-offset-2"
                to="/signup"
              >
                Create one
              </Link>
            </p>
          ) : (
            <p className="font-body-sm text-body-sm text-on-surface-variant">
              Already have an account?{' '}
              <Link
                className="text-primary-container hover:text-primary-fixed underline decoration-primary-container/30 underline-offset-2"
                to="/login"
              >
                Sign in
              </Link>
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

export function SignInRoute() {
  return <AuthCard mode="signin" />
}

export function SignUpRoute() {
  return <AuthCard mode="signup" />
}
