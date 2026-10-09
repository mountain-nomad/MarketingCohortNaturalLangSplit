import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { apiError, clearCookies, me, mockApi, setCsrfCookie, type MockResponse, type RecordedCall } from '../test/mockApi.ts'
import { SemanticTab } from './SemanticTab.tsx'

const READ = ['semantic_context.read']
const EDIT = ['semantic_context.read', 'semantic_context.edit']
const REVIEW = ['semantic_context.read', 'use_case.review']
const CRAWL = ['semantic_context.read', 'crawler.run']

const docs = {
  tables: [
    {
      qualified_name: 'public.orders',
      kind: 'table',
      comment: null,
      primary_key: ['order_id'],
      foreign_keys: [{ columns: ['user_id'], referred_table: 'public.users', referred_columns: ['user_id'] }],
      estimated_row_count: 830,
      columns: [
        { name: 'order_id', data_type: 'bigint', nullable: false, comment: null, allowed_values: null, sample_values: [] },
        {
          name: 'status',
          data_type: 'text',
          nullable: false,
          comment: null,
          allowed_values: ['pending', 'paid', 'delivered'],
          sample_values: ['delivered', 'paid'],
        },
      ],
    },
  ],
  latest_run: {
    id: 4,
    status: 'succeeded',
    started_at: '2026-03-02T09:00:00Z',
    finished_at: '2026-03-02T09:00:05Z',
    triggered_by: 'user:7',
    content_hash: 'a'.repeat(64),
    summary: { tables: 16 },
    error: null,
  },
}

const purchase = {
  key: 'purchase',
  kind: 'metric',
  synonyms: ['bought'],
  description: 'Paid orders',
  definition: {
    kind: 'metric',
    table: 'public.orders',
    filters: [{ column: 'public.orders.status', operator: 'in', value: ['paid', 'delivered'] }],
  },
  missing_references: [],
  created_at: '2026-03-02T09:00:00Z',
  updated_at: '2026-03-02T09:00:00Z',
  created_by: { id: 7, display_name: 'Alice' },
  updated_by: { id: 7, display_name: 'Alice' },
}

const stale = {
  ...purchase,
  key: 'liked',
  kind: 'term',
  synonyms: [],
  description: '',
  definition: { kind: 'term', table: 'public.product_likes' },
  missing_references: [{ reference: 'public.product_likes', problem: 'unknown table' }],
}

function makeUseCase(id: number, status: string, overrides: object = {}) {
  return {
    id,
    key: `template_${id}`,
    origin: 'generated',
    status,
    nl_request: `Use case ${id}`,
    spec: { spec_version: 'draft-0', entity: { table: 'public.users', key: 'user_id' } },
    spec_version: 'draft-0',
    template_key: `template_${id}`,
    rewritten_from: null,
    generation_note: null,
    review_note: null,
    referenced_columns: ['public.users.user_id'],
    reviewed_by: null,
    reviewed_at: null,
    updated_at: '2026-03-02T09:00:00Z',
    ...overrides,
  }
}

const queue = {
  items: [
    makeUseCase(1, 'pending_review'),
    makeUseCase(2, 'needs_rereview', { review_note: 'Flagged by crawl run 4: no longer in the warehouse: column public.orders.status.' }),
    makeUseCase(3, 'confirmed', { origin: 'human' }),
  ],
}

const runs = { items: [docs.latest_run] }
const version = { version: 'b'.repeat(64), components: {} }

function baseHandlers(extra: Record<string, MockResponse | ((call: RecordedCall) => MockResponse)> = {}) {
  return {
    'GET /api/semantic/version': { body: version },
    'GET /api/semantic/docs': { body: docs },
    'GET /api/semantic/business-context': { body: { items: [purchase, stale] } },
    'GET /api/semantic/use-cases': { body: queue },
    'GET /api/crawler/runs': { body: runs },
    ...extra,
  }
}

function renderTab(permissions: string[]) {
  return render(<SemanticTab me={me(permissions)} />)
}

async function openView(name: string) {
  const u = userEvent.setup()
  await u.click(await screen.findByRole('tab', { name }))
  return u
}

beforeEach(() => setCsrfCookie('csrf-s'))
afterEach(() => {
  clearCookies()
  vi.unstubAllGlobals()
})

describe('SemanticTab', () => {
  it('shows generated docs read-only with row counts, keys, relationships and samples', async () => {
    mockApi(baseHandlers())
    renderTab(READ)

    expect(await screen.findByRole('heading', { name: 'Semantic context' })).toBeInTheDocument()
    const table = await screen.findByRole('region', { name: 'public.orders' })
    expect(within(table).getByText(/830 rows/)).toBeInTheDocument()
    expect(within(table).getByText(/user_id → public\.users\.user_id/)).toBeInTheDocument()
    const status = within(table).getByText('status').closest('tr') as HTMLElement
    expect(within(status).getByText('delivered, paid')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /edit|delete|save/i })).not.toBeInTheDocument()
    expect(screen.getByText(/version bbbbbbbbbbbb/i)).toBeInTheDocument()
  })

  it('hides every control a read-only user lacks permission for', async () => {
    mockApi(baseHandlers())
    renderTab(READ)

    let u = await openView('Business context')
    expect(await screen.findByText('purchase')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New entry' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^edit/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^delete/i })).not.toBeInTheDocument()

    u = await openView('Use cases')
    expect(await screen.findByText('Use case 1')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /confirm|reject|edit/i })).not.toBeInTheDocument()

    await u.click(screen.getByRole('tab', { name: 'Crawler' }))
    expect(await screen.findByText(/succeeded/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Run crawler' })).not.toBeInTheDocument()
  })

  it('marks business-context entries whose references disappeared', async () => {
    mockApi(baseHandlers())
    renderTab(READ)
    await openView('Business context')

    const entry = (await screen.findByText('liked')).closest('article') as HTMLElement
    expect(within(entry).getByText(/public\.product_likes: unknown table/)).toBeInTheDocument()
  })

  it('lets an editor create a typed entry', async () => {
    const calls = mockApi(
      baseHandlers({ 'POST /api/semantic/business-context': { status: 201, body: purchase } }),
    )
    renderTab(EDIT)
    const u = await openView('Business context')

    await u.click(await screen.findByRole('button', { name: 'New entry' }))
    const form = screen.getByRole('form', { name: 'Business-context entry' })
    await u.type(within(form).getByLabelText('Key'), 'recently')
    await u.selectOptions(within(form).getByLabelText('Kind'), 'time_window')
    await u.type(within(form).getByLabelText('Synonyms'), 'Recently, lately')
    await u.type(within(form).getByLabelText('Description'), 'Last 30 days')
    const definition = within(form).getByLabelText('Definition (JSON)')
    await u.clear(definition)
    await u.click(definition)
    await u.paste('{"kind": "time_window", "last_days": 30}')
    await u.click(within(form).getByRole('button', { name: 'Save entry' }))

    await vi.waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    const post = calls.find((c) => c.method === 'POST')
    expect(post?.path).toBe('/api/semantic/business-context')
    expect(post?.body).toEqual({
      key: 'recently',
      synonyms: ['Recently', 'lately'],
      description: 'Last 30 days',
      definition: { kind: 'time_window', last_days: 30 },
    })
    expect(post?.headers['x-csrf-token']).toBe('csrf-s')
  })

  it('shows unknown references refused by the server', async () => {
    mockApi(
      baseHandlers({
        'POST /api/semantic/business-context': apiError(
          422,
          'invalid_reference',
          'The definition references tables or columns that are not in the latest crawl.',
          { problems: [{ reference: 'public.likes', problem: 'unknown table' }] },
        ),
      }),
    )
    renderTab(EDIT)
    const u = await openView('Business context')

    await u.click(await screen.findByRole('button', { name: 'New entry' }))
    const form = screen.getByRole('form', { name: 'Business-context entry' })
    await u.type(within(form).getByLabelText('Key'), 'liked_product')
    await u.click(within(form).getByRole('button', { name: 'Save entry' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('not in the latest crawl')
    expect(alert).toHaveTextContent('public.likes: unknown table')
  })

  it('refuses to send a definition that is not valid JSON', async () => {
    const calls = mockApi(baseHandlers())
    renderTab(EDIT)
    const u = await openView('Business context')

    await u.click(await screen.findByRole('button', { name: 'New entry' }))
    const form = screen.getByRole('form', { name: 'Business-context entry' })
    await u.type(within(form).getByLabelText('Key'), 'broken')
    const definition = within(form).getByLabelText('Definition (JSON)')
    await u.clear(definition)
    await u.click(definition)
    await u.paste('{not json')
    await u.click(within(form).getByRole('button', { name: 'Save entry' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/valid JSON/i)
    expect(calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('lets an editor delete an entry', async () => {
    const calls = mockApi(
      baseHandlers({ 'DELETE /api/semantic/business-context/purchase': { status: 204 } }),
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderTab(EDIT)
    const u = await openView('Business context')

    await u.click(await screen.findByRole('button', { name: 'Delete purchase' }))

    await vi.waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
  })

  it('highlights use cases that need re-review with the reason', async () => {
    mockApi(baseHandlers())
    renderTab(REVIEW)
    await openView('Use cases')

    const flagged = (await screen.findByText('Use case 2')).closest('article') as HTMLElement
    expect(flagged).toHaveClass('needs-rereview')
    expect(within(flagged).getByText('Needs re-review')).toBeInTheDocument()
    expect(within(flagged).getByText(/no longer in the warehouse/)).toBeInTheDocument()
    const pending = screen.getByText('Use case 1').closest('article') as HTMLElement
    expect(pending).not.toHaveClass('needs-rereview')
  })

  it('lets a reviewer confirm and reject', async () => {
    const calls = mockApi(
      baseHandlers({
        'POST /api/semantic/use-cases/1/confirm': { body: makeUseCase(1, 'confirmed') },
        'POST /api/semantic/use-cases/2/reject': { body: makeUseCase(2, 'rejected') },
      }),
    )
    renderTab(REVIEW)
    const u = await openView('Use cases')

    const pending = (await screen.findByText('Use case 1')).closest('article') as HTMLElement
    await u.click(within(pending).getByRole('button', { name: 'Confirm' }))
    const flagged = screen.getByText('Use case 2').closest('article') as HTMLElement
    await u.click(within(flagged).getByRole('button', { name: 'Reject' }))

    await vi.waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2))
    const posts = calls.filter((c) => c.method === 'POST').map((c) => c.path)
    expect(posts).toEqual(['/api/semantic/use-cases/1/confirm', '/api/semantic/use-cases/2/reject'])
    expect(calls.find((c) => c.method === 'POST')?.headers['x-csrf-token']).toBe('csrf-s')
    const confirmed = screen.getByText('Use case 3').closest('article') as HTMLElement
    expect(within(confirmed).queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
  })

  it('lets a reviewer edit a use case, which saves it as confirmed', async () => {
    const calls = mockApi(
      baseHandlers({ 'PUT /api/semantic/use-cases/2': { body: makeUseCase(2, 'confirmed', { origin: 'human' }) } }),
    )
    renderTab(REVIEW)
    const u = await openView('Use cases')

    const flagged = (await screen.findByText('Use case 2')).closest('article') as HTMLElement
    await u.click(within(flagged).getByRole('button', { name: 'Edit' }))
    const form = screen.getByRole('form', { name: 'Edit use case 2' })
    const request = within(form).getByLabelText('Request')
    await u.clear(request)
    await u.type(request, 'Users with a delivered order')
    await u.click(within(form).getByRole('button', { name: 'Save and confirm' }))

    await vi.waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({
      nl_request: 'Users with a delivered order',
      spec: makeUseCase(2, 'x').spec,
    })
  })

  it('runs the crawler and reports the result', async () => {
    const calls = mockApi(
      baseHandlers({
        'POST /api/crawler/runs': {
          status: 201,
          body: { ...docs.latest_run, id: 5, semantic_version: 'c'.repeat(64) },
        },
      }),
    )
    renderTab(CRAWL)
    const u = await openView('Crawler')

    await u.click(await screen.findByRole('button', { name: 'Run crawler' }))

    expect(await screen.findByText(/crawl run 5 succeeded/i)).toBeInTheDocument()
    expect(calls.find((c) => c.method === 'POST')?.path).toBe('/api/crawler/runs')
  })

  it('explains when another crawl is already running', async () => {
    mockApi(
      baseHandlers({
        'POST /api/crawler/runs': apiError(
          409,
          'crawl_in_progress',
          'Another crawl is running. Try again when it has finished.',
        ),
      }),
    )
    renderTab(CRAWL)
    const u = await openView('Crawler')

    await u.click(await screen.findByRole('button', { name: 'Run crawler' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Another crawl is running')
  })
})
