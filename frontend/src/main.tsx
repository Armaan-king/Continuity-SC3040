import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'

import { AuthProvider } from './app/hooks/useAuth'
import { RequireAuth } from './app/routes/RequireAuth'
import DesignRoute from './app/routes/design'
import { SignInRoute, SignUpRoute } from './app/routes/auth'
import LandingRoute from './app/routes/landing'
import ProjectsRoute from './app/routes/projects'
import MemoryRoute from './app/routes/memory'
import AnalyticsRoute from './app/routes/analytics'
import WalkthroughRoute from './app/routes/walkthrough'
import { AppFrame } from './app/shell/AppFrame'
import { AppShell } from './app/shell/AppShell'
import './index.css'

const router = createBrowserRouter([
  {
    element: <AppFrame />,
    children: [
      {
        path: '/',
        element: <LandingRoute />,
      },
      {
        path: '/login',
        element: <SignInRoute />,
      },
      {
        path: '/signup',
        element: <SignUpRoute />,
      },
      {
        element: <AppShell />,
        children: [
          {
            path: '/projects',
            element: (
              <RequireAuth>
                <ProjectsRoute />
              </RequireAuth>
            ),
          },
          {
            path: '/walkthrough',
            element: (
              <RequireAuth>
                <WalkthroughRoute />
              </RequireAuth>
            ),
          },
          {
            path: '/memory',
            element: (
              <RequireAuth>
                <MemoryRoute />
              </RequireAuth>
            ),
          },
          {
            path: '/analytics',
            element: (
              <RequireAuth>
                <AnalyticsRoute />
              </RequireAuth>
            ),
          },
          {
            path: '/design',
            element: <DesignRoute />,
          },
          {
            // Guarded, unlike the bare /design above: a project belongs to somebody. Without
            // this a signed-out visitor gets the whole workspace shell — the API refuses every
            // call behind it, so nothing leaks, but it renders a broken page where a redirect
            // belongs.
            path: '/design/:projectId',
            element: (
              <RequireAuth>
                <DesignRoute />
              </RequireAuth>
            ),
          },
        ],
      },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  </StrictMode>,
)
