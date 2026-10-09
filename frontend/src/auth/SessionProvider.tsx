import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ApiError, api, errorMessage, onSessionProblem } from '../api/client.ts'
import type { Me } from '../api/types.ts'
import { SessionContext, type LoginResult, type Session, type SessionState } from './session.ts'

interface LoginResponse {
  must_change_password: boolean
}

/** Holds who is signed in. Permissions always come from GET /api/me (server-evaluated). */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: 'loading' })

  const refresh = useCallback(async () => {
    try {
      const me = await api<Me>('/api/me')
      setState({ status: 'ready', me })
    } catch (error) {
      if (error instanceof ApiError && error.code === 'not_authenticated') {
        setState({ status: 'anonymous' })
      } else if (error instanceof ApiError && error.code === 'password_change_required') {
        setState({ status: 'must_change' })
      } else {
        setState({ status: 'error', message: errorMessage(error) })
      }
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(
    () =>
      onSessionProblem((code) => {
        setState(code === 'not_authenticated' ? { status: 'anonymous' } : { status: 'must_change' })
      }),
    [],
  )

  const login = useCallback(
    async (email: string, password: string): Promise<LoginResult> => {
      const result = await api<LoginResponse>('/api/auth/login', {
        method: 'POST',
        body: { email, password },
      })
      if (result.must_change_password) {
        setState({ status: 'must_change' })
      } else {
        await refresh()
      }
      return { mustChangePassword: result.must_change_password }
    },
    [refresh],
  )

  const logout = useCallback(async () => {
    try {
      await api<void>('/api/auth/logout', { method: 'POST' })
    } catch {
      // Already signed out or the server is unreachable: the local state still resets.
    }
    setState({ status: 'anonymous' })
  }, [])

  const value = useMemo<Session>(() => ({ state, refresh, login, logout }), [state, refresh, login, logout])
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}
