import { Outlet, useLocation } from 'react-router'

import { SideRail } from './SideRail'

export function AppShell() {
  const location = useLocation()
  const isWorkspace = location.pathname === '/design' || location.pathname.startsWith('/design/') || location.pathname === '/walkthrough'

  return (
    <div className={isWorkspace ? 'h-[100dvh] overflow-hidden bg-background text-on-background' : 'min-h-screen bg-transparent text-on-background'}>
      <SideRail />
      <div className={isWorkspace ? 'ml-16 h-full w-[calc(100%-4rem)] overflow-hidden' : 'ml-16 min-h-screen w-[calc(100%-4rem)]'}>
        <div className={isWorkspace ? 'h-full overflow-hidden' : 'app-page-transition'} key={location.pathname}>
          <Outlet />
        </div>
      </div>
    </div>
  )
}
