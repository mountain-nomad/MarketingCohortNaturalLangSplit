/**
 * Minimal JSON client for the CohortSplit API.
 *
 * - Cookies carry the session (HttpOnly, never readable here).
 * - Unsafe methods send the CSRF token from the `cohortsplit_csrf` cookie as X-CSRF-Token.
 * - Errors arrive as {"error": {"code", "message", ...}} and are thrown as ApiError.
 * - A `not_authenticated` / `password_change_required` answer from any call is broadcast
 *   so the session state can react (redirect to sign-in or the forced password change).
 */

const CSRF_COOKIE = 'cohortsplit_csrf'
const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

type SessionListener = (code: 'not_authenticated' | 'password_change_required') => void
const listeners = new Set<SessionListener>()

export function onSessionProblem(listener: SessionListener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function readCsrfToken(): string | null {
  for (const part of document.cookie.split(';')) {
    const [name, ...rest] = part.trim().split('=')
    if (name === CSRF_COOKIE) {
      return decodeURIComponent(rest.join('='))
    }
  }
  return null
}

export type Query = Record<string, string | number | undefined | null>

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Query
}

function buildUrl(path: string, query?: Query): string {
  if (!query) {
    return path
  }
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value))
    }
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

async function errorFrom(response: Response): Promise<ApiError> {
  let code = 'http_error'
  let message = `Request failed (${response.status}).`
  let details: Record<string, unknown> = {}
  try {
    const payload: unknown = await response.json()
    if (payload && typeof payload === 'object' && 'error' in payload) {
      const error = (payload as { error: Record<string, unknown> }).error
      if (typeof error.code === 'string') code = error.code
      if (typeof error.message === 'string') message = error.message
      details = error
    }
  } catch {
    // Non-JSON error body: keep the generic message.
  }
  return new ApiError(response.status, code, message, details)
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }
  if (UNSAFE.has(method)) {
    const token = readCsrfToken()
    if (token) {
      headers['X-CSRF-Token'] = token
    }
  }
  const response = await fetch(buildUrl(path, options.query), {
    method,
    headers,
    credentials: 'same-origin',
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })
  if (!response.ok) {
    const error = await errorFrom(response)
    if (error.code === 'not_authenticated' || error.code === 'password_change_required') {
      for (const listener of listeners) listener(error.code)
    }
    throw error
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return 'Something went wrong. Check your connection and try again.'
}
