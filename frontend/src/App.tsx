import type { ReactNode } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import type { Me } from './api/types.ts'
import { ChangePasswordPage } from './auth/ChangePasswordPage.tsx'
import { LoginPage } from './auth/LoginPage.tsx'
import { SessionProvider } from './auth/SessionProvider.tsx'
import { useSession } from './auth/session.ts'
import type { TabId } from './auth/tabs.ts'
import { AccountTab } from './dashboard/AccountTab.tsx'
import { AuditTab } from './dashboard/AuditTab.tsx'
import { ComingSoonTab } from './dashboard/ComingSoonTab.tsx'
import { DashboardLayout, FirstTab, TabGate } from './dashboard/DashboardLayout.tsx'
import { RolesTab } from './dashboard/RolesTab.tsx'
import { UsersTab } from './dashboard/UsersTab.tsx'

function Loading() {
  return <p className="muted center">Loading…</p>
}

function SessionError({ message }: { message: string }) {
  const { refresh } = useSession()
  return (
    <section className="auth-card" role="alert">
      <h2>CohortSplit is unavailable</h2>
      <p>{message}</p>
      <button type="button" onClick={() => void refresh()}>
        Try again
      </button>
    </section>
  )
}

function LoginRoute() {
  const { state } = useSession()
  switch (state.status) {
    case 'loading':
      return <Loading />
    case 'ready':
      return <Navigate to="/" replace />
    case 'must_change':
      return <Navigate to="/change-password" replace />
    default:
      return <LoginPage />
  }
}

function ChangePasswordRoute() {
  const { state } = useSession()
  switch (state.status) {
    case 'loading':
      return <Loading />
    case 'must_change':
      return <ChangePasswordPage />
    case 'ready':
      return <Navigate to="/account" replace />
    case 'error':
      return <SessionError message={state.message} />
    default:
      return <Navigate to="/login" replace />
  }
}

/** Every dashboard route needs a live session; `me` comes from the server on each load. */
function Dashboard({ render }: { render: (me: Me) => ReactNode }) {
  const { state } = useSession()
  switch (state.status) {
    case 'loading':
      return <Loading />
    case 'anonymous':
      return <Navigate to="/login" replace />
    case 'must_change':
      return <Navigate to="/change-password" replace />
    case 'error':
      return <SessionError message={state.message} />
    case 'ready':
      return <>{render(state.me)}</>
  }
}

function tab(id: TabId, element: (me: Me) => ReactNode) {
  return (
    <Dashboard
      render={(me) => (
        <TabGate me={me} id={id}>
          {element(me)}
        </TabGate>
      )}
    />
  )
}

function Header() {
  const { state, logout } = useSession()
  const signedIn = state.status === 'ready' || state.status === 'must_change'
  return (
    <header className="shell-header">
      <h1>
        <Link to="/" className="brand">
          CohortSplit
        </Link>
      </h1>
      {signedIn && (
        <div className="who">
          {state.status === 'ready' && <span className="muted">{state.me.display_name}</span>}
          <button type="button" className="quiet" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      )}
    </header>
  )
}

function DashboardShell() {
  return <Dashboard render={(me) => <DashboardLayout me={me} />} />
}

function RootIndex() {
  return <Dashboard render={(me) => <FirstTab me={me} />} />
}

export function App() {
  return (
    <SessionProvider>
      <div className="shell">
        <Header />
        <main className="shell-main">
          <Routes>
            <Route path="/login" element={<LoginRoute />} />
            <Route path="/change-password" element={<ChangePasswordRoute />} />
            <Route element={<DashboardShell />}>
              <Route index element={<RootIndex />} />
              <Route path="account" element={tab('account', (me) => <AccountTab me={me} />)} />
              <Route
                path="cohorts/new"
                element={tab('new-cohort', () => (
                  <ComingSoonTab
                    title="New cohort"
                    description="Describe an audience in plain English, review the interpretation, preview it, and split it into test and control groups."
                  />
                ))}
              />
              <Route
                path="history"
                element={tab('history', () => (
                  <ComingSoonTab
                    title="History"
                    description="Past cohort runs with their exact membership, ready to download again."
                  />
                ))}
              />
              <Route
                path="semantic"
                element={tab('semantic', () => (
                  <ComingSoonTab
                    title="Semantic context"
                    description="Business definitions, generated warehouse docs and use cases awaiting review."
                  />
                ))}
              />
              <Route path="admin/users" element={tab('users', () => <UsersTab />)} />
              <Route path="admin/roles" element={tab('roles', () => <RolesTab />)} />
              <Route path="admin/audit" element={tab('audit', () => <AuditTab />)} />
              <Route
                path="*"
                element={
                  <section className="panel">
                    <h2>Page not found</h2>
                    <p>
                      <Link to="/">Back to the dashboard</Link>
                    </p>
                  </section>
                }
              />
            </Route>
          </Routes>
        </main>
      </div>
    </SessionProvider>
  )
}
