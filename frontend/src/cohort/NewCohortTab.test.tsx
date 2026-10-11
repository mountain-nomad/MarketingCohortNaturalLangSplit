import '@testing-library/jest-dom/vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { apiError, clearCookies, me, mockApi, setCsrfCookie, type MockResponse } from '../test/mockApi.ts'
import { DashboardLayout, TabGate } from '../dashboard/DashboardLayout.tsx'
import { NewCohortTab } from './NewCohortTab.tsx'

const CREATE = ['cohort.create']
const REQUEST = 'Users from Germany who abandoned a cart and never paid for an order'

const interpretation = {
  kind: 'interpretation',
  interpretation_id: 'a'.repeat(32),
  readable: 'has addresses with country_code = DE AND has carts with status = abandoned AND NOT has paid_order',
  spec: { spec_version: '1', entity: 'user', where: { type: 'metric', term: 'paid_order' } },
  spec_hash: 'b'.repeat(64),
  semantic_version: 'c'.repeat(64),
  expires_at: '2026-03-02T10:00:00+00:00',
}

const preview = {
  prepared_cohort_id: 'd'.repeat(32),
  expires_at: '2026-03-03T09:00:00+00:00',
  total_count: 3,
  empty: false,
  split_export_enabled: true,
  disabled_reason: null,
  sample: { columns: ['canonical_user_id', 'public.users.phone'], rows: [[1, '+491'], [2, null], [3, '+3']], limit: 20 },
  null_counts: { 'public.users.phone': 1 },
  readable: interpretation.readable,
  sql: 'SELECT DISTINCT u."user_id" AS "canonical_user_id" FROM "public"."users" AS u ORDER BY 1',
  sql_params: { p1: 'DE' },
  as_of: '2026-03-02T09:00:00+00:00',
  sql_hash: 'e'.repeat(64),
  spec_hash: interpretation.spec_hash,
  semantic_version: interpretation.semantic_version,
  row_cap: 1000000,
}

function setup(handlers: Record<string, MockResponse>, permissions: string[] = CREATE) {
  setCsrfCookie('csrf-1')
  const calls = mockApi(handlers)
  render(<NewCohortTab me={me(permissions)} />)
  return calls
}

async function ask(text = REQUEST) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText(/describe the audience/i), text)
  await user.click(screen.getByRole('button', { name: /interpret/i }))
  return user
}

afterEach(() => {
  clearCookies()
})

test('shows the interpretation and previews only after explicit confirmation', async () => {
  const calls = setup({
    'POST /api/cohorts/interpret': { body: interpretation },
    'POST /api/cohorts/preview': { body: preview },
  })

  const user = await ask()

  expect(await screen.findByText(interpretation.readable)).toBeInTheDocument()
  expect(calls.filter((c) => c.path === '/api/cohorts/preview')).toHaveLength(0)
  expect(calls[0]?.body).toEqual({ nl_request: REQUEST })
  expect(calls[0]?.headers['x-csrf-token']).toBe('csrf-1')

  await user.click(screen.getByRole('button', { name: /confirm and preview/i }))

  const result = await screen.findByRole('region', { name: /preview/i })
  expect(within(result).getByText('Users in cohort').nextElementSibling).toHaveTextContent(/^3$/)
  expect(within(result).getByText(preview.sql)).toBeInTheDocument()
  expect(within(result).getByRole('columnheader', { name: 'public.users.phone' })).toBeInTheDocument()
  expect(within(result).getByText('+491')).toBeInTheDocument()
  const nulls = within(result).getByRole('table', { name: /null counts/i })
  expect(within(nulls).getByText('1')).toBeInTheDocument()
  const previewCall = calls.find((c) => c.path === '/api/cohorts/preview')
  expect(previewCall?.body).toEqual({
    interpretation_id: interpretation.interpretation_id,
    spec_hash: interpretation.spec_hash,
    semantic_version: interpretation.semantic_version,
    export_columns: [],
  })
})

test('shows a clarification question with unknown terms and values', async () => {
  setup({
    'POST /api/cohorts/interpret': {
      body: {
        kind: 'clarification',
        question: "What do you mean by 'best customer'? There is no definition in Business context.",
        unknown_terms: ['best customer'],
        unknown_values: [{ column: 'public.carts.status', value: 'abandonned', suggestions: ['abandoned'] }],
        semantic_version: 'c'.repeat(64),
      },
    },
  })

  await ask('Our best customers')

  expect(await screen.findByText(/what do you mean by 'best customer'/i)).toBeInTheDocument()
  expect(screen.getByText(/abandonned/)).toBeInTheDocument()
  expect(screen.getByText(/abandoned/, { selector: 'li *, li' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /confirm and preview/i })).not.toBeInTheDocument()
})

test('shows a rejection with its reason', async () => {
  setup({
    'POST /api/cohorts/interpret': {
      body: {
        kind: 'rejection',
        reason: 'mutation',
        message: 'CohortSplit only builds read-only audiences; nothing was changed.',
        semantic_version: 'c'.repeat(64),
      },
    },
  })

  await ask('Delete inactive users')

  expect(await screen.findByText(/request changes data/i)).toBeInTheDocument()
  expect(screen.getByText(/nothing was changed/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /confirm and preview/i })).not.toBeInTheDocument()
})

test('an empty cohort disables split and export with the reason', async () => {
  setup({
    'POST /api/cohorts/interpret': { body: interpretation },
    'POST /api/cohorts/preview': {
      body: {
        ...preview,
        prepared_cohort_id: null,
        total_count: 0,
        empty: true,
        split_export_enabled: false,
        disabled_reason: 'The cohort is empty: there is nothing to split or export.',
        sample: { columns: ['canonical_user_id'], rows: [], limit: 20 },
        null_counts: {},
      },
    },
  })

  const user = await ask()
  await user.click(await screen.findByRole('button', { name: /confirm and preview/i }))

  expect(await screen.findByText(/nothing to split or export/i)).toBeInTheDocument()
})

test('a changed semantic version asks to interpret again', async () => {
  setup({
    'POST /api/cohorts/interpret': { body: interpretation },
    'POST /api/cohorts/preview': apiError(
      409,
      'semantic_version_changed',
      'The business context changed since this request was interpreted. Interpret the request again.',
    ),
  })

  const user = await ask()
  await user.click(await screen.findByRole('button', { name: /confirm and preview/i }))

  expect(await screen.findByRole('alert')).toHaveTextContent(/interpret the request again/i)
  expect(screen.queryByRole('button', { name: /confirm and preview/i })).not.toBeInTheDocument()
})

test('interpret errors are shown', async () => {
  setup({
    'POST /api/cohorts/interpret': apiError(503, 'llm_unavailable', 'LLM provider unavailable at http://ollama:11434/v1'),
  })

  await ask()

  expect(await screen.findByRole('alert')).toHaveTextContent('LLM provider unavailable at http://ollama:11434/v1')
})

test('export columns are offered only with cohort.export and sent with the confirmation', async () => {
  const calls = setup(
    {
      'POST /api/cohorts/interpret': { body: interpretation },
      'POST /api/cohorts/preview': { body: preview },
    },
    ['cohort.create', 'cohort.export'],
  )

  const user = await ask()
  await user.type(await screen.findByLabelText(/extra export columns/i), 'public.users.phone')
  await user.click(screen.getByRole('button', { name: /confirm and preview/i }))
  await screen.findByRole('region', { name: /preview/i })

  expect(calls.find((c) => c.path === '/api/cohorts/preview')?.body).toMatchObject({
    export_columns: ['public.users.phone'],
  })
})

test('no export column field without cohort.export', async () => {
  setup({ 'POST /api/cohorts/interpret': { body: interpretation } })

  await ask()
  await screen.findByText(interpretation.readable)

  expect(screen.queryByLabelText(/extra export columns/i)).not.toBeInTheDocument()
})

test('the tab is hidden and gated without cohort.create', () => {
  render(
    <MemoryRouter>
      <DashboardLayout me={me(['semantic_context.read'])} />
      <TabGate me={me(['semantic_context.read'])} id="new-cohort">
        <NewCohortTab me={me(['semantic_context.read'])} />
      </TabGate>
    </MemoryRouter>,
  )

  expect(screen.queryByRole('link', { name: /new cohort/i })).not.toBeInTheDocument()
  expect(screen.getByText(/not available/i)).toBeInTheDocument()
  expect(screen.queryByLabelText(/describe the audience/i)).not.toBeInTheDocument()
})
