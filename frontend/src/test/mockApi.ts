import { vi } from 'vitest'

export interface RecordedCall {
  method: string
  url: string
  path: string
  query: URLSearchParams
  headers: Record<string, string>
  body: unknown
}

export interface MockResponse {
  status?: number
  body?: unknown
  headers?: Record<string, string>
}

type Handler = MockResponse | ((call: RecordedCall) => MockResponse)

/**
 * Replace global fetch with a router keyed by "METHOD /path" (query string ignored).
 * Unknown routes answer 404 so tests notice unexpected calls.
 */
export function mockApi(handlers: Record<string, Handler>): RecordedCall[] {
  const calls: RecordedCall[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const parsed = new URL(url, 'http://localhost')
    const method = (init?.method ?? 'GET').toUpperCase()
    const headers: Record<string, string> = {}
    new Headers(init?.headers).forEach((value, key) => {
      headers[key.toLowerCase()] = value
    })
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
    const call: RecordedCall = {
      method,
      url,
      path: parsed.pathname,
      query: parsed.searchParams,
      headers,
      body,
    }
    calls.push(call)
    const handler = handlers[`${method} ${parsed.pathname}`]
    const response: MockResponse =
      handler === undefined
        ? { status: 404, body: { error: { code: 'not_found', message: 'Not found.' } } }
        : typeof handler === 'function'
          ? handler(call)
          : handler
    const status = response.status ?? 200
    const payload = status === 204 || response.body === undefined ? null : JSON.stringify(response.body)
    return new Response(payload, {
      status,
      headers: { 'Content-Type': 'application/json', ...(response.headers ?? {}) },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

export function apiError(status: number, code: string, message: string, extra: object = {}): MockResponse {
  return { status, body: { error: { code, message, ...extra } } }
}

export function setCsrfCookie(value: string): void {
  document.cookie = `cohortsplit_csrf=${value}; path=/`
}

export function clearCookies(): void {
  for (const part of document.cookie.split(';')) {
    const name = part.split('=')[0]?.trim()
    if (name) {
      document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`
    }
  }
}

export const ALL_PERMISSIONS = [
  'audit.read',
  'cohort.create',
  'cohort.export',
  'cohort.read_all',
  'crawler.run',
  'role.assign',
  'role.create',
  'role.delete',
  'role.read',
  'role.update',
  'semantic_context.edit',
  'semantic_context.read',
  'use_case.review',
  'user.create',
  'user.deactivate',
  'user.read',
  'user.reset_password',
  'user.update',
]

export function me(permissions: string[], overrides: object = {}) {
  return {
    id: 7,
    email: 'alice@example.com',
    display_name: 'Alice',
    is_admin: false,
    roles: permissions.length ? [{ id: 3, name: 'Marketer' }] : [],
    permissions,
    ...overrides,
  }
}
