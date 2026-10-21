import { Outlet, useLocation } from 'react-router'

import { PcbBackground } from './PcbBackground'

export function AppFrame() {
  const location = useLocation()
  const isDesignWorkspace = location.pathname === '/design' || location.pathname.startsWith('/design/') || location.pathname === '/walkthrough'

  return (
    // The workspace is locked to the viewport, and `AppShell` locks it with `100dvh`.
    // This wrapper must use the *same* unit: `min-h-screen` is `100vh`, which is 23px
    // taller than `100dvh` here, and the taller outer box reintroduced exactly the page
    // scroll the lock exists to remove — the header slid under the browser chrome again.
    <div className={isDesignWorkspace ? 'h-[100dvh] overflow-hidden' : 'min-h-screen isolate'}>
      {isDesignWorkspace ? null : <PcbBackground pauseAnimation={location.pathname === '/memory'} />}
      <div className={isDesignWorkspace ? undefined : 'relative z-10'}>
        <Outlet />
      </div>
    </div>
  )
}
