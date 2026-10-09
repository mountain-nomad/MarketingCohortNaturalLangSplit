import { createContext, useContext } from 'react'
import type { Me } from '../api/types.ts'

export type SessionState =
  | { status: 'loading' }
  | { status: 'anonymous' }
  | { status: 'must_change' }
  | { status: 'ready'; me: Me }
  | { status: 'error'; message: string }

export interface LoginResult {
  mustChangePassword: boolean
}

export interface Session {
  state: SessionState
  refresh: () => Promise<void>
  login: (email: string, password: string) => Promise<LoginResult>
  logout: () => Promise<void>
}

export const SessionContext = createContext<Session | null>(null)

export function useSession(): Session {
  const session = useContext(SessionContext)
  if (!session) {
    throw new Error('useSession must be used inside <SessionProvider>')
  }
  return session
}
