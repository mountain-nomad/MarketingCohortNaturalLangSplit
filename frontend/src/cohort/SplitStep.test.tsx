import '@testing-library/jest-dom/vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { apiError, clearCookies, me, mockApi, setCsrfCookie, type MockResponse } from '../test/mockApi.ts'
import { NewCohortTab } from './NewCohortTab.tsx'

const EXPORTER = ['cohort.create', 'cohort.export']
const PHONE = 'public.users.phone'
const EMAIL = 'public.users.email'

const interpretation = {
  kind: 'interpretation',
  interpretation_id: 'a'.repeat(32),
  readable: 'has paid_order',
  spec: { spec_version: '1', entity: 'user', where: { type: 'metric', term: 'paid_order' } },
  spec_hash: 'b'.repeat(64),
  semantic_version: 'c'.repeat(64),
  expires_at: '2026-03-02T10:00:00+00:00',
}

const preview = {
  prepared_cohort_id: 'd'.repeat(32),
  expires_at: '2026-03-03T09:00:00+00:00',
  total_count: 89,
  empty: false,
  split_export_enabled: true,
  disabled_reason: null,
  sample: { columns: ['canonical_user_id'], rows: [[1], [2]], limit: 20 },
  null_counts: {},
  readable: interpretation.readable,
  sql: 'SELECT DISTINCT u."user_id" AS "canonical_user_id" FROM "public"."users" AS u ORDER BY 1',
  sql_params: {},
  as_of: '2026-03-02T09:00:00+00:00',
  sql_hash: 'e'.repeat(64),
  spec_hash: interpretation.spec_hash,
  semantic_version: interpretation.semantic_version,
  row_cap: 1000000,
}

function run(overrides: object = {}) {
  return {
    id: 'f'.repeat(32),
    experiment_key: 'push_bf_2026',
    experiment_type: 'ab',
    allocation: { test: 50, control: 50 },
    assignment_version: 1,
    counts: { test: 45, control: 44, total: 89 },
    empty_groups: [],
    requires_empty_group_confirmation: false,
    export_columns: [],
    download_count: 0,
    nl_request: 'Users who paid for an order',
    spec_hash: interpretation.spec_hash,
    sql_hash: preview.sql_hash,
    semantic_version: interpretation.semantic_version,
    created_at: '2026-03-02T09:01:00+00:00',
    created_by: { id: 7, email: 'alice@example.com', display_name: 'Alice' },
    preview_count: 89,
    warnings: [],
    ...overrides,
  }
}

const zip: MockResponse = {
  body: 'PK-zip-bytes',
  headers: {
    'Content-Type': 'application/zip',
    'Content-Disposition': 'attachment; filename="cohortsplit_push_bf_2026.zip"',
  },
}

let saved: string[] = []

beforeEach(() => {
  saved = []
  // jsdom has no object URLs: stub them and capture the file name of the saved download.
  URL.createObjectURL = vi.fn(() => 'blob:zip')
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(this.download)
  })
})

afterEach(() => {
  clearCookies()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function toPreview(handlers: Parameters<typeof mockApi>[0], permissions = EXPORTER, exportColumns: string[] = []) {
  setCsrfCookie('csrf-1')
  const calls = mockApi({
    'POST /api/cohorts/interpret': { body: interpretation },
    'POST /api/cohorts/preview': { body: preview },
    ...handlers,
  })
  render(<NewCohortTab me={{ ...me(permissions), export_columns: exportColumns }} />)
  const user = userEvent.setup()
  await user.type(screen.getByLabelText(/describe the audience/i), 'Users who paid for an order')
  await user.click(screen.getByRole('button', { name: /interpret/i }))
  await user.click(await screen.findByRole('button', { name: /confirm and preview/i }))
  const split = await screen.findByRole('region', { name: /split into test and control/i })
  return { user, calls, split }
}

function splitCall(calls: { path: string; method: string; body: unknown }[]) {
  return calls.find((c) => c.path === '/api/runs' && c.method === 'POST')
}

test('splits with the default 50/50 allocation and shows the group counts', async () => {
  const { user, calls, split } = await toPreview({ 'POST /api/runs': { status: 201, body: run() } })

  expect(within(split).getByLabelText(/test group/i)).toHaveValue(50)
  expect(within(split).getByText(/control group: 50%/i)).toBeInTheDocument()
  await user.type(within(split).getByLabelText(/experiment key/i), 'push_bf_2026')
  await user.click(within(split).getByRole('button', { name: /^split$/i }))

  const result = await screen.findByRole('region', { name: /split result/i })
  expect(within(result).getByText('Test').nextElementSibling).toHaveTextContent(/^45$/)
  expect(within(result).getByText('Control').nextElementSibling).toHaveTextContent(/^44$/)
  expect(within(result).getByText('Total').nextElementSibling).toHaveTextContent(/^89$/)
  expect(splitCall(calls)?.body).toEqual({
    prepared_cohort_id: preview.prepared_cohort_id,
    experiment_key: 'push_bf_2026',
    allocation: { test: 50, control: 50 },
    experiment_type: 'ab',
    export_columns: [],
  })
})

test('the control share follows the test share', async () => {
  const { user, calls, split } = await toPreview({ 'POST /api/runs': { status: 201, body: run() } })

  const test = within(split).getByLabelText(/test group/i)
  await user.clear(test)
  await user.type(test, '90')
  expect(within(split).getByText(/control group: 10%/i)).toBeInTheDocument()
  await user.type(within(split).getByLabelText(/experiment key/i), 'k90')
  await user.click(within(split).getByRole('button', { name: /^split$/i }))
  await screen.findByRole('region', { name: /split result/i })

  expect(splitCall(calls)?.body).toMatchObject({ allocation: { test: 90, control: 10 } })
})

test('invalid key or allocation is refused before any request', async () => {
  const { user, calls, split } = await toPreview({})
  const button = within(split).getByRole('button', { name: /^split$/i })

  expect(button).toBeDisabled() // no key yet
  await user.type(within(split).getByLabelText(/experiment key/i), 'bad key/../x')
  expect(within(split).getByText(/letters, digits/i)).toBeInTheDocument()
  expect(button).toBeDisabled()

  await user.clear(within(split).getByLabelText(/experiment key/i))
  await user.type(within(split).getByLabelText(/experiment key/i), 'good_key')
  const test = within(split).getByLabelText(/test group/i)
  await user.clear(test)
  await user.type(test, '100')
  expect(within(split).getByText(/between 1 and 99/i)).toBeInTheDocument()
  expect(button).toBeDisabled()
  expect(splitCall(calls)).toBeUndefined()
})

test('A/A check is sent as experiment type aa', async () => {
  const { user, calls, split } = await toPreview({ 'POST /api/runs': { status: 201, body: run({ experiment_type: 'aa' }) } })

  await user.type(within(split).getByLabelText(/experiment key/i), 'aa_check')
  await user.click(within(split).getByLabelText(/a\/a check/i))
  await user.click(within(split).getByRole('button', { name: /^split$/i }))
  await screen.findByRole('region', { name: /split result/i })

  expect(splitCall(calls)?.body).toMatchObject({ experiment_type: 'aa' })
})

test('only granted export columns are offered and the chosen ones are sent', async () => {
  const { user, calls, split } = await toPreview(
    { 'POST /api/runs': { status: 201, body: run({ export_columns: [PHONE] }) } },
    EXPORTER,
    [EMAIL, PHONE],
  )

  const boxes = within(split).getAllByRole('checkbox', { name: /public\.users\./ })
  expect(boxes.map((b) => b.getAttribute('name'))).toEqual([EMAIL, PHONE])
  await user.click(within(split).getByRole('checkbox', { name: PHONE }))
  await user.type(within(split).getByLabelText(/experiment key/i), 'k')
  await user.click(within(split).getByRole('button', { name: /^split$/i }))
  await screen.findByRole('region', { name: /split result/i })

  expect(splitCall(calls)?.body).toMatchObject({ export_columns: [PHONE] })
})

test('without cohort.export there are no columns and no download', async () => {
  const { user, split } = await toPreview({ 'POST /api/runs': { status: 201, body: run() } }, ['cohort.create'], [PHONE])

  expect(within(split).queryByRole('checkbox', { name: PHONE })).not.toBeInTheDocument()
  await user.type(within(split).getByLabelText(/experiment key/i), 'k')
  await user.click(within(split).getByRole('button', { name: /^split$/i }))
  const result = await screen.findByRole('region', { name: /split result/i })

  expect(within(result).queryByRole('button', { name: /download/i })).not.toBeInTheDocument()
  expect(within(result).getByText(/needs the cohort\.export permission/i)).toBeInTheDocument()
})

test('downloads the ZIP for the run', async () => {
  const { user, calls, split } = await toPreview({
    'POST /api/runs': { status: 201, body: run() },
    [`POST /api/runs/${'f'.repeat(32)}/download`]: zip,
  })

  await user.type(within(split).getByLabelText(/experiment key/i), 'push_bf_2026')
  await user.click(within(split).getByRole('button', { name: /^split$/i }))
  const result = await screen.findByRole('region', { name: /split result/i })
  await user.click(within(result).getByRole('button', { name: /download zip/i }))

  await within(result).findByText(/downloaded/i)
  const download = calls.find((c) => c.path.endsWith('/download'))
  expect(download?.method).toBe('POST')
  expect(download?.headers['x-csrf-token']).toBe('csrf-1')
  expect(download?.body).toEqual({ confirm_empty_group: false })
  expect(saved).toEqual(['cohortsplit_push_bf_2026.zip'])
})

test('an empty group is warned about and must be confirmed before download', async () => {
  const warned = run({
    counts: { test: 1, control: 0, total: 1 },
    empty_groups: ['control'],
    requires_empty_group_confirmation: true,
    warnings: [{ code: 'empty_group', group: 'control', message: 'The control group has no users with this key and allocation.' }],
  })
  const { user, calls, split } = await toPreview({
    'POST /api/runs': { status: 201, body: warned },
    [`POST /api/runs/${'f'.repeat(32)}/download`]: zip,
  })

  await user.type(within(split).getByLabelText(/experiment key/i), 'k')
  await user.click(within(split).getByRole('button', { name: /^split$/i }))
  const result = await screen.findByRole('region', { name: /split result/i })

  expect(within(result).getByText(/control group has no users/i)).toBeInTheDocument()
  const download = within(result).getByRole('button', { name: /download zip/i })
  expect(download).toBeDisabled()
  await user.click(within(result).getByLabelText(/export with an empty group/i))
  await user.click(download)
  await within(result).findByText(/downloaded/i)

  expect(calls.find((c) => c.path.endsWith('/download'))?.body).toEqual({ confirm_empty_group: true })
})

test('split errors are shown', async () => {
  const { user, split } = await toPreview({
    'POST /api/runs': apiError(409, 'semantic_version_changed', 'The business context changed since this request was interpreted. Interpret the request again.'),
  })

  await user.type(within(split).getByLabelText(/experiment key/i), 'k')
  await user.click(within(split).getByRole('button', { name: /^split$/i }))

  expect(await screen.findByRole('alert')).toHaveTextContent(/interpret the request again/i)
})

test('download errors are shown', async () => {
  const { user, split } = await toPreview({
    'POST /api/runs': { status: 201, body: run() },
    [`POST /api/runs/${'f'.repeat(32)}/download`]: apiError(403, 'export_column_denied', 'None of your roles is granted export of: public.users.phone.'),
  })

  await user.type(within(split).getByLabelText(/experiment key/i), 'k')
  await user.click(within(split).getByRole('button', { name: /^split$/i }))
  const result = await screen.findByRole('region', { name: /split result/i })
  await user.click(within(result).getByRole('button', { name: /download zip/i }))

  expect(await screen.findByRole('alert')).toHaveTextContent(/public\.users\.phone/)
  expect(saved).toEqual([])
})

test('an empty cohort offers no split', async () => {
  setCsrfCookie('csrf-1')
  mockApi({
    'POST /api/cohorts/interpret': { body: interpretation },
    'POST /api/cohorts/preview': {
      body: {
        ...preview,
        prepared_cohort_id: null,
        total_count: 0,
        empty: true,
        split_export_enabled: false,
        disabled_reason: 'The cohort is empty: there is nothing to split or export.',
      },
    },
  })
  render(<NewCohortTab me={me(EXPORTER)} />)
  const user = userEvent.setup()
  await user.type(screen.getByLabelText(/describe the audience/i), 'x')
  await user.click(screen.getByRole('button', { name: /interpret/i }))
  await user.click(await screen.findByRole('button', { name: /confirm and preview/i }))

  await screen.findByText(/nothing to split or export/i)
  expect(screen.queryByRole('region', { name: /split into test and control/i })).not.toBeInTheDocument()
})

test('after a refused column the result offers a user-id-only download', async () => {
  let attempt = 0
  const { user, calls, split } = await toPreview({
    'POST /api/runs': { status: 201, body: run({ export_columns: [PHONE] }) },
    [`POST /api/runs/${'f'.repeat(32)}/download`]: () =>
      ++attempt === 1 ? apiError(403, 'export_column_denied', 'None of your roles is granted export of: public.users.phone.') : zip,
  })

  await user.type(within(split).getByLabelText(/experiment key/i), 'k')
  await user.click(within(split).getByRole('button', { name: /^split$/i }))
  const result = await screen.findByRole('region', { name: /split result/i })
  await user.click(within(result).getByRole('button', { name: /download zip/i }))
  await screen.findByRole('alert')
  await user.click(within(result).getByRole('button', { name: /download user ids only/i }))

  await within(result).findByText(/downloaded/i)
  const bodies = calls.filter((c) => c.path.endsWith('/download')).map((c) => c.body)
  expect(bodies).toEqual([{ confirm_empty_group: false }, { columns: [], confirm_empty_group: false }])
})
