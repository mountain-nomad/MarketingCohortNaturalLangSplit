import type { ReactNode } from 'react'
import { NavLink, Navigate, Outlet } from 'react-router-dom'
import type { Me } from '../api/types.ts'
import { canSee, tabById, visibleTabs, type TabId } from '../auth/tabs.ts'

export function DashboardLayout({ me }: { me: Me }) {
  const tabs = visibleTabs(me.permissions)
  const workspace = tabs.filter((tab) => tab.group === 'workspace')
  const admin = tabs.filter((tab) => tab.group === 'admin')

  return (
    <div className="dashboard">
      <nav aria-label="Dashboard" className="sidebar">
        <p className="nav-group">Workspace</p>
        <ul>
          {workspace.map((tab) => (
            <li key={tab.id}>
              <NavLink to={tab.path}>{tab.label}</NavLink>
            </li>
          ))}
        </ul>
        {admin.length > 0 && (
          <>
            <p className="nav-group">Administration</p>
            <ul>
              {admin.map((tab) => (
                <li key={tab.id}>
                  <NavLink to={tab.path}>{tab.label}</NavLink>
                </li>
              ))}
            </ul>
          </>
        )}
      </nav>
      <div className="dashboard-content">
        <Outlet />
      </div>
    </div>
  )
}

/** Renders a tab only for users whose permissions reveal it (UX only; the API enforces). */
export function TabGate({ me, id, children }: { me: Me; id: TabId; children: ReactNode }) {
  if (!canSee(tabById(id), me.permissions)) {
    return (
      <section className="panel">
        <h2>Not available</h2>
        <p>You don't have access to this page. Ask an administrator if you need it.</p>
      </section>
    )
  }
  return <>{children}</>
}

export function FirstTab({ me }: { me: Me }) {
  const [first] = visibleTabs(me.permissions)
  return <Navigate to={first?.path ?? '/account'} replace />
}
