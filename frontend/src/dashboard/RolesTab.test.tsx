import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { apiError, clearCookies, mockApi, setCsrfCookie } from '../test/mockApi.ts'
import { RolesTab } from './RolesTab.tsx'

const catalog = {
  items: [
    { key: 'user.read', area: 'Users', description: 'List and view users', grantable: false },
    { key: 'role.update', area: 'Roles', description: 'Edit roles', grantable: false },
    { key: 'cohort.create', area: 'Cohorts', description: 'Interpret, preview and split', grantable: true },
    { key: 'cohort.export', area: 'Cohorts', description: 'Download exports', grantable: true },
    { key: 'audit.read', area: 'Audit', description: 'Read the audit log', grantable: true },
  ],
}

const roles = {
  items: [
    {
      id: 1,
      name: 'Admin',
      description: 'Protected system role',
      is_system: true,
      permissions: ['audit.read', 'cohort.create', 'cohort.export', 'role.update', 'user.read'],
      export_columns: [],
      member_ids: [1],
    },
    {
      id: 2,
      name: 'Marketer',
      description: 'Push campaigns',
      is_system: false,
      permissions: ['cohort.create'],
      export_columns: ['public.users.phone'],
      member_ids: [2],
    },
  ],
}

const users = {
  items: [
    { id: 1, email: 'root@example.com', display_name: 'Root', is_active: true, must_change_password: false, roles: [], created_at: '2026-03-02T09:00:00Z', last_login_at: null },
    { id: 2, email: 'alice@example.com', display_name: 'Alice', is_active: true, must_change_password: false, roles: [], created_at: '2026-03-02T09:00:00Z', last_login_at: null },
  ],
}

function baseApi(extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    'GET /api/admin/permissions': { body: catalog },
    'GET /api/admin/roles': { body: roles },
    'GET /api/admin/users': { body: users },
    ...extra,
  })
}

beforeEach(() => setCsrfCookie('csrf-r'))
afterEach(() => {
  clearCookies()
  vi.unstubAllGlobals()
})

describe('RolesTab', () => {
  it('shows the Admin role as protected, without edit or delete', async () => {
    baseApi()
    render(<RolesTab />)

    const adminRow = (await screen.findByText('Admin')).closest('li') as HTMLElement
    expect(within(adminRow).getByText(/protected/i)).toBeInTheDocument()
    expect(within(adminRow).queryByRole('button', { name: /edit/i })).not.toBeInTheDocument()
    expect(within(adminRow).queryByRole('button', { name: /delete/i })).not.toBeInTheDocument()
  })

  it('does not offer admin-only permissions (AC-A14a)', async () => {
    const u = userEvent.setup()
    baseApi()
    render(<RolesTab />)

    await u.click(await screen.findByRole('button', { name: 'New role' }))
    const form = await screen.findByRole('form', { name: 'Role editor' })
    const names = within(form)
      .getAllByRole('checkbox')
      .map((box) => box.getAttribute('value'))

    expect(names).toEqual(expect.arrayContaining(['cohort.create', 'cohort.export', 'audit.read']))
    expect(names).not.toContain('user.read')
    expect(names).not.toContain('role.update')
  })

  it('creates a role with permissions, export columns and members', async () => {
    const u = userEvent.setup()
    const calls = baseApi({
      'POST /api/admin/roles': { status: 201, body: { ...roles.items[1], id: 3, name: 'Analyst' } },
      'PUT /api/admin/roles/3/members': { body: { ...roles.items[1], id: 3, name: 'Analyst' } },
    })
    render(<RolesTab />)

    await u.click(await screen.findByRole('button', { name: 'New role' }))
    const form = await screen.findByRole('form', { name: 'Role editor' })
    await u.type(within(form).getByLabelText('Name'), 'Analyst')
    await u.type(within(form).getByLabelText('Description'), 'Knows the data')
    await u.click(within(form).getByRole('checkbox', { name: /cohort\.create/ }))
    await u.click(within(form).getByRole('checkbox', { name: /audit\.read/ }))
    await u.type(
      within(form).getByLabelText('Exportable columns'),
      'public.users.phone{enter}public.users.email',
    )
    await u.click(within(form).getByRole('checkbox', { name: 'alice@example.com' }))
    await u.click(within(form).getByRole('button', { name: 'Save role' }))

    await vi.waitFor(() =>
      expect(calls.some((c) => c.path === '/api/admin/roles/3/members')).toBe(true),
    )
    const create = calls.find((c) => c.method === 'POST' && c.path === '/api/admin/roles')
    expect(create?.body).toEqual({
      name: 'Analyst',
      description: 'Knows the data',
      permissions: ['audit.read', 'cohort.create'],
      export_columns: ['public.users.email', 'public.users.phone'],
    })
    expect(create?.headers['x-csrf-token']).toBe('csrf-r')
    expect(calls.find((c) => c.path === '/api/admin/roles/3/members')?.body).toEqual({
      user_ids: [2],
    })
  })

  it('edits an existing role', async () => {
    const u = userEvent.setup()
    const calls = baseApi({
      'PATCH /api/admin/roles/2': { body: roles.items[1] },
      'PUT /api/admin/roles/2/members': { body: roles.items[1] },
    })
    render(<RolesTab />)

    const row = (await screen.findByText('Marketer')).closest('li') as HTMLElement
    await u.click(within(row).getByRole('button', { name: 'Edit' }))
    const form = await screen.findByRole('form', { name: 'Role editor' })
    expect(within(form).getByLabelText('Name')).toHaveValue('Marketer')
    expect(within(form).getByLabelText('Exportable columns')).toHaveValue('public.users.phone')
    await u.click(within(form).getByRole('checkbox', { name: /cohort\.export/ }))
    await u.click(within(form).getByRole('button', { name: 'Save role' }))

    await vi.waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true))
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({
      name: 'Marketer',
      description: 'Push campaigns',
      permissions: ['cohort.create', 'cohort.export'],
      export_columns: ['public.users.phone'],
    })
  })

  it('asks for confirmation before deleting an assigned role', async () => {
    const u = userEvent.setup()
    const calls = baseApi({
      'DELETE /api/admin/roles/2': (call) =>
        call.query.get('confirm') === 'true'
          ? { status: 204 }
          : apiError(409, 'confirmation_required', 'This role is assigned to 1 user(s).', {
              member_count: 1,
            }),
    })
    render(<RolesTab />)

    const row = (await screen.findByText('Marketer')).closest('li') as HTMLElement
    await u.click(within(row).getByRole('button', { name: 'Delete' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('assigned to 1 user(s)')
    await u.click(screen.getByRole('button', { name: 'Delete anyway' }))

    await vi.waitFor(() =>
      expect(calls.filter((c) => c.method === 'DELETE').map((c) => c.query.get('confirm'))).toEqual([
        null,
        'true',
      ]),
    )
  })

  it('shows validation errors from the server', async () => {
    const u = userEvent.setup()
    baseApi({
      'POST /api/admin/roles': apiError(422, 'invalid_column', 'Export columns must look like schema.table.column.'),
    })
    render(<RolesTab />)

    await u.click(await screen.findByRole('button', { name: 'New role' }))
    const form = await screen.findByRole('form', { name: 'Role editor' })
    await u.type(within(form).getByLabelText('Name'), 'Broken')
    await u.type(within(form).getByLabelText('Exportable columns'), 'phone')
    await u.click(within(form).getByRole('button', { name: 'Save role' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('schema.table.column')
  })
})
