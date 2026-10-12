import '@testing-library/jest-dom/vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { apiError, clearCookies, me, mockApi, setCsrfCookie, type MockResponse } from '../test/mockApi.ts'
import { HistoryTab } from './HistoryTab.tsx'

const A = 'a'.repeat(32)
const B = 'b'.repeat(32)

function run(id: string, overrides: object = {}) {
  return {
    id,
    experiment_key: `key_${id.slice(0, 1)}`,
    experiment_type: 'ab',
    allocation: { test: 50, control: 50 },
    assignment_version: 1,
    counts: { test: 45, control: 44, total: 89 },
    empty_groups: [],
    requires_empty_group_confirmation: false,
    export_columns: ['public.users.phone'],
    download_count: 1,
    nl_request: 'Users who paid for an order',
    spec_hash: 'c'.repeat(64),
    sql_hash: 'd'.repeat(64),
    semantic_version: 'e'.repeat(64),
    created_at: '2026-03-02T09:01:00+00:00',
    created_by: { id: 7, email: 'alice@example.com', display_name: 'Alice' },
    ...overrides,
  }
}

function list(items: object[], scope: 'own' | 'all' = 'own'): MockResponse {
  return { body: { items, total: items.length, limit: 50, offset: 0, scope } }
}

const zip: MockResponse = {
  body: 'PK',
  headers: { 'Content-Type': 'application/zip', 'Content-Disposition': 'attachment; filename="cohortsplit_key_a.zip"' },
}

let saved: string[] = []

beforeEach(() => {
  saved = []
  URL.createObjectURL = vi.fn(() => 'blob:zip')
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(this.download)
  })
  setCsrfCookie('csrf-1')
})

afterEach(() => {
  clearCookies()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function setup(handlers: Parameters<typeof mockApi>[0], permissions = ['cohort.create', 'cohort.export']) {
  const calls = mockApi(handlers)
  render(<HistoryTab me={me(permissions)} />)
  return { calls, user: userEvent.setup() }
}

async function row(key: string) {
  const table = await screen.findByRole('table', { name: /runs/i })
  const cell = within(table).getByText(key)
  const tr = cell.closest('tr')
  if (!tr) throw new Error('no row')
  return tr
}

test('lists runs with key, request, allocation and counts', async () => {
  const { calls } = setup({ 'GET /api/runs': list([run(A), run(B, { allocation: { test: 90, control: 10 } })]) })

  const first = await row('key_a')
  expect(within(first).getByText('Users who paid for an order')).toBeInTheDocument()
  expect(within(first).getByText('50 / 50')).toBeInTheDocument()
  expect(within(first).getByText(/45 test · 44 control/)).toBeInTheDocument()
  expect(within(await row('key_b')).getByText('90 / 10')).toBeInTheDocument()
  expect(calls[0]?.path).toBe('/api/runs')
  expect(screen.queryByRole('columnheader', { name: /created by/i })).not.toBeInTheDocument()
})

test('everyone’s runs show who created them', async () => {
  setup({ 'GET /api/runs': list([run(A)], 'all') }, ['cohort.read_all'])

  const first = await row('key_a')
  expect(screen.getByRole('columnheader', { name: /created by/i })).toBeInTheDocument()
  expect(within(first).getByText('alice@example.com')).toBeInTheDocument()
})

test('re-download is offered only with cohort.export', async () => {
  setup({ 'GET /api/runs': list([run(A)], 'all') }, ['cohort.read_all'])

  const first = await row('key_a')
  expect(within(first).queryByRole('button', { name: /download again/i })).not.toBeInTheDocument()
})

test('downloads a run again', async () => {
  const { calls, user } = setup({ 'GET /api/runs': list([run(A)]), [`POST /api/runs/${A}/download`]: zip })

  await user.click(within(await row('key_a')).getByRole('button', { name: /download again/i }))

  await screen.findByText(/downloaded cohortsplit_key_a\.zip/i)
  const download = calls.find((c) => c.path === `/api/runs/${A}/download`)
  expect(download?.method).toBe('POST')
  expect(download?.headers['x-csrf-token']).toBe('csrf-1')
  expect(download?.body).toEqual({ confirm_empty_group: false })
  expect(saved).toEqual(['cohortsplit_key_a.zip'])
})

test('a refused column offers a user-id-only download', async () => {
  let attempt = 0
  const { calls, user } = setup({
    'GET /api/runs': list([run(A)]),
    [`POST /api/runs/${A}/download`]: () =>
      ++attempt === 1
        ? apiError(403, 'export_column_denied', 'None of your roles is granted export of: public.users.phone.', {
            columns: ['public.users.phone'],
          })
        : zip,
  })

  await user.click(within(await row('key_a')).getByRole('button', { name: /download again/i }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/public\.users\.phone/)
  await user.click(screen.getByRole('button', { name: /download user ids only/i }))

  await screen.findByText(/downloaded/i)
  const bodies = calls.filter((c) => c.path === `/api/runs/${A}/download`).map((c) => c.body)
  expect(bodies).toEqual([{ confirm_empty_group: false }, { columns: [], confirm_empty_group: false }])
  expect(saved).toEqual(['cohortsplit_key_a.zip'])
})

test.each([
  [409, 'canonical_user_id_changed', 'The canonical user identifier changed since this run was made.'],
  [422, 'export_column_unsupported', 'Only columns of the user table public.users can be exported: public.users.phone.'],
])('a %s %s also offers a user-id-only download (ruling E11)', async (status, code, message) => {
  let attempt = 0
  const { calls, user } = setup({
    'GET /api/runs': list([run(A)]),
    [`POST /api/runs/${A}/download`]: () => (++attempt === 1 ? apiError(status, code, message) : zip),
  })

  await user.click(within(await row('key_a')).getByRole('button', { name: /download again/i }))
  expect(await screen.findByRole('alert')).toHaveTextContent(message)
  await user.click(screen.getByRole('button', { name: /download user ids only/i }))

  await screen.findByText(/downloaded/i)
  const bodies = calls.filter((c) => c.path === `/api/runs/${A}/download`).map((c) => c.body)
  expect(bodies).toEqual([{ confirm_empty_group: false }, { columns: [], confirm_empty_group: false }])
})

test('other download errors offer no user-id-only retry', async () => {
  setup({
    'GET /api/runs': list([run(A)]),
    [`POST /api/runs/${A}/download`]: apiError(503, 'warehouse_unavailable', 'Warehouse unavailable.'),
  })
  const user = userEvent.setup()

  await user.click(within(await row('key_a')).getByRole('button', { name: /download again/i }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/warehouse unavailable/i)
  expect(screen.queryByRole('button', { name: /download user ids only/i })).not.toBeInTheDocument()
})

test('a run with an empty group asks for confirmation first', async () => {
  const { calls, user } = setup({
    'GET /api/runs': list([run(A, { counts: { test: 1, control: 0, total: 1 }, empty_groups: ['control'], requires_empty_group_confirmation: true })]),
    [`POST /api/runs/${A}/download`]: zip,
  })

  await user.click(within(await row('key_a')).getByRole('button', { name: /download again/i }))
  expect(calls.some((c) => c.path.endsWith('/download'))).toBe(false)
  expect(screen.getByText(/control group is empty/i)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /download anyway/i }))

  await screen.findByText(/downloaded/i)
  expect(calls.find((c) => c.path.endsWith('/download'))?.body).toEqual({ confirm_empty_group: true })
})

test('no runs yet', async () => {
  setup({ 'GET /api/runs': list([]) })

  expect(await screen.findByText(/no runs yet/i)).toBeInTheDocument()
})

test('load errors are shown', async () => {
  setup({ 'GET /api/runs': apiError(503, 'service_unavailable', 'The application database is unavailable.') })

  expect(await screen.findByRole('alert')).toHaveTextContent(/database is unavailable/i)
})
