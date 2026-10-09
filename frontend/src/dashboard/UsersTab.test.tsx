import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { apiError, clearCookies, mockApi, setCsrfCookie } from '../test/mockApi.ts'
import { UsersTab } from './UsersTab.tsx'

const roles = {
  items: [
    { id: 1, name: 'Admin', description: '', is_system: true, permissions: [], export_columns: [], member_ids: [1] },
    { id: 2, name: 'Marketer', description: '', is_system: false, permissions: ['cohort.create'], export_columns: [], member_ids: [2] },
  ],
}

function user(id: number, email: string, overrides: object = {}) {
  return {
    id,
    email,
    display_name: email.split('@')[0],
    is_active: true,
    must_change_password: false,
    roles: [],
    created_at: '2026-03-02T09:00:00Z',
    last_login_at: null,
    ...overrides,
  }
}

const users = {
  items: [
    user(1, 'root@example.com', { roles: [{ id: 1, name: 'Admin' }] }),
    user(2, 'alice@example.com', { roles: [{ id: 2, name: 'Marketer' }] }),
    user(3, 'gone@example.com', { is_active: false }),
  ],
}

beforeEach(() => setCsrfCookie('csrf-u'))
afterEach(() => {
  clearCookies()
  vi.unstubAllGlobals()
})

describe('UsersTab', () => {
  it('lists users with roles and status', async () => {
    mockApi({ 'GET /api/admin/users': { body: users }, 'GET /api/admin/roles': { body: roles } })
    render(<UsersTab />)

    const row = (await screen.findByText('alice@example.com')).closest('tr')
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText('Marketer')).toBeInTheDocument()
    const goneRow = screen.getByText('gone@example.com').closest('tr') as HTMLElement
    expect(within(goneRow).getByText('Deactivated')).toBeInTheDocument()
    expect(within(goneRow).getByRole('button', { name: 'Reactivate' })).toBeInTheDocument()
  })

  it('creates a user with a temporary password and roles', async () => {
    const u = userEvent.setup()
    const calls = mockApi({
      'GET /api/admin/users': { body: users },
      'GET /api/admin/roles': { body: roles },
      'POST /api/admin/users': { status: 201, body: user(4, 'new@example.com') },
    })
    render(<UsersTab />)

    const form = await screen.findByRole('form', { name: 'Create user' })
    await u.type(within(form).getByLabelText('Email'), 'new@example.com')
    await u.type(within(form).getByLabelText('Display name'), 'New Person')
    await u.type(within(form).getByLabelText('Temporary password'), 'temporary password 2026')
    await u.click(within(form).getByRole('checkbox', { name: 'Marketer' }))
    await u.click(within(form).getByRole('button', { name: 'Create user' }))

    await vi.waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    const post = calls.find((c) => c.method === 'POST')
    expect(post?.body).toEqual({
      email: 'new@example.com',
      display_name: 'New Person',
      temporary_password: 'temporary password 2026',
      role_ids: [2],
    })
    expect(post?.headers['x-csrf-token']).toBe('csrf-u')
  })

  it('shows the server error when deactivating the last admin', async () => {
    const u = userEvent.setup()
    mockApi({
      'GET /api/admin/users': { body: users },
      'GET /api/admin/roles': { body: roles },
      'POST /api/admin/users/1/deactivate': apiError(
        409,
        'last_admin',
        'At least one active admin must remain. Make another user an admin first.',
      ),
    })
    render(<UsersTab />)

    const row = (await screen.findByText('root@example.com')).closest('tr') as HTMLElement
    await u.click(within(row).getByRole('button', { name: 'Deactivate' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('At least one active admin must remain')
  })

  it('resets a password with a new temporary password', async () => {
    const u = userEvent.setup()
    const calls = mockApi({
      'GET /api/admin/users': { body: users },
      'GET /api/admin/roles': { body: roles },
      'POST /api/admin/users/2/reset-password': {
        body: user(2, 'alice@example.com', { must_change_password: true }),
      },
    })
    render(<UsersTab />)

    const row = (await screen.findByText('alice@example.com')).closest('tr') as HTMLElement
    await u.click(within(row).getByRole('button', { name: 'Edit' }))
    const editor = await screen.findByRole('form', { name: 'Edit alice@example.com' })
    await u.type(within(editor).getByLabelText('New temporary password'), 'another temporary pw')
    await u.click(within(editor).getByRole('button', { name: 'Reset password' }))

    await vi.waitFor(() =>
      expect(calls.some((c) => c.path === '/api/admin/users/2/reset-password')).toBe(true),
    )
    const reset = calls.find((c) => c.path === '/api/admin/users/2/reset-password')
    expect(reset?.body).toEqual({ temporary_password: 'another temporary pw' })
  })

  it('saves edited roles for a user', async () => {
    const u = userEvent.setup()
    const calls = mockApi({
      'GET /api/admin/users': { body: users },
      'GET /api/admin/roles': { body: roles },
      'PUT /api/admin/users/2/roles': { body: user(2, 'alice@example.com') },
      'PATCH /api/admin/users/2': { body: user(2, 'alice@example.com') },
    })
    render(<UsersTab />)

    const row = (await screen.findByText('alice@example.com')).closest('tr') as HTMLElement
    await u.click(within(row).getByRole('button', { name: 'Edit' }))
    const editor = await screen.findByRole('form', { name: 'Edit alice@example.com' })
    await u.click(within(editor).getByRole('checkbox', { name: 'Marketer' }))
    await u.click(within(editor).getByRole('checkbox', { name: 'Admin' }))
    await u.click(within(editor).getByRole('button', { name: 'Save changes' }))

    await vi.waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ role_ids: [1] })
  })
})
