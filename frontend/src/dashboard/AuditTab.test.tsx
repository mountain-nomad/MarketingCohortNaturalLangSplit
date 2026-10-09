import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { clearCookies, mockApi } from '../test/mockApi.ts'
import { AuditTab } from './AuditTab.tsx'

function event(id: number, overrides: object = {}) {
  return {
    id,
    occurred_at: '2026-03-02T09:00:00Z',
    actor_type: 'user',
    actor_user_id: 1,
    actor_email: 'root@example.com',
    action: 'role.create',
    target_type: 'role',
    target_id: '3',
    outcome: 'success',
    request_id: 'req-1',
    metadata: { name: 'Analyst' },
    ...overrides,
  }
}

afterEach(() => {
  clearCookies()
  vi.unstubAllGlobals()
})

describe('AuditTab', () => {
  it('lists events with actor, action, target and outcome', async () => {
    mockApi({
      'GET /api/audit/events': {
        body: {
          items: [
            event(2, { action: 'auth.login_failed', actor_type: 'anonymous', actor_user_id: null, actor_email: null, outcome: 'denied', target_type: 'user', target_id: '7' }),
            event(1),
          ],
          total: 2,
          limit: 50,
          offset: 0,
        },
      },
    })
    render(<AuditTab />)

    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(within(rows[0] as HTMLElement).getByText('auth.login_failed')).toBeInTheDocument()
    expect(within(rows[0] as HTMLElement).getByText('anonymous')).toBeInTheDocument()
    expect(within(rows[0] as HTMLElement).getByText('denied')).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText('root@example.com')).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText('role 3')).toBeInTheDocument()
  })

  it('applies filters as query parameters', async () => {
    const u = userEvent.setup()
    const calls = mockApi({
      'GET /api/audit/events': { body: { items: [], total: 0, limit: 50, offset: 0 } },
    })
    render(<AuditTab />)
    await screen.findByText(/no events/i)

    await u.type(screen.getByLabelText('Actor email'), 'alice@example.com')
    await u.type(screen.getByLabelText('Action'), 'auth.login')
    await u.selectOptions(screen.getByLabelText('Outcome'), 'denied')
    await u.type(screen.getByLabelText('From'), '2026-03-01T00:00')
    await u.type(screen.getByLabelText('To'), '2026-03-02T23:59')
    await u.click(screen.getByRole('button', { name: 'Apply filters' }))

    await vi.waitFor(() => expect(calls).toHaveLength(2))
    const query = calls[1]?.query
    expect(query?.get('actor_email')).toBe('alice@example.com')
    expect(query?.get('action')).toBe('auth.login')
    expect(query?.get('outcome')).toBe('denied')
    expect(query?.get('since')).toBe(new Date('2026-03-01T00:00').toISOString())
    expect(query?.get('until')).toBe(new Date('2026-03-02T23:59').toISOString())
    expect(query?.get('offset')).toBe('0')
  })

  it('paginates with offset', async () => {
    const u = userEvent.setup()
    const calls = mockApi({
      'GET /api/audit/events': (call) => ({
        body: {
          items: [event(Number(call.query.get('offset') ?? 0) + 1)],
          total: 120,
          limit: 50,
          offset: Number(call.query.get('offset') ?? 0),
        },
      }),
    })
    render(<AuditTab />)

    expect(await screen.findByText('Showing 1–50 of 120')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    await u.click(screen.getByRole('button', { name: 'Next' }))

    expect(await screen.findByText('Showing 51–100 of 120')).toBeInTheDocument()
    expect(calls[1]?.query.get('offset')).toBe('50')
    expect(calls[1]?.query.get('limit')).toBe('50')
    await u.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Showing 101–120 of 120')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  })

  it('offers no way to edit or delete events', async () => {
    mockApi({
      'GET /api/audit/events': { body: { items: [event(1)], total: 1, limit: 50, offset: 0 } },
    })
    render(<AuditTab />)

    await screen.findByRole('table')
    expect(screen.queryByRole('button', { name: /delete|edit|remove/i })).not.toBeInTheDocument()
  })
})
