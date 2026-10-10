import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { App } from './App.tsx'
import {
  ALL_PERMISSIONS,
  apiError,
  clearCookies,
  me,
  mockApi,
  setCsrfCookie,
} from './test/mockApi.ts'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

async function dashboardTabs() {
  const nav = await screen.findByRole('navigation', { name: 'Dashboard' })
  return within(nav)
    .getAllByRole('link')
    .map((link) => link.textContent)
}

const notSignedIn = apiError(401, 'not_authenticated', 'Sign in to continue.')

afterEach(() => {
  clearCookies()
  vi.unstubAllGlobals()
})

describe('App shell', () => {
  it('renders the CohortSplit heading', async () => {
    mockApi({ 'GET /api/me': notSignedIn })
    renderAt('/login')

    expect(screen.getByRole('heading', { level: 1, name: 'CohortSplit' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('sends an anonymous visitor to the sign-in page', async () => {
    mockApi({ 'GET /api/me': notSignedIn })
    renderAt('/admin/users')

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Dashboard' })).not.toBeInTheDocument()
  })
})

describe('Sign in', () => {
  it('signs in and shows the dashboard', async () => {
    const user = userEvent.setup()
    let signedIn = false
    const calls = mockApi({
      'GET /api/me': () => (signedIn ? { body: me(['cohort.create']) } : notSignedIn),
      'POST /api/auth/login': () => {
        signedIn = true
        return {
          body: {
            user: { id: 7, email: 'alice@example.com', display_name: 'Alice' },
            must_change_password: false,
            csrf_token: 'csrf-1',
          },
        }
      },
    })
    renderAt('/login')

    await user.type(await screen.findByLabelText('Email'), 'alice@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct horse battery staple')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await dashboardTabs()).toEqual(['New cohort', 'History', 'Account'])
    const login = calls.find((c) => c.path === '/api/auth/login')
    expect(login?.method).toBe('POST')
    expect(login?.body).toEqual({
      email: 'alice@example.com',
      password: 'correct horse battery staple',
    })
  })

  it('shows the server message on failure and stays on the form', async () => {
    const user = userEvent.setup()
    mockApi({
      'GET /api/me': notSignedIn,
      'POST /api/auth/login': apiError(401, 'invalid_credentials', 'Invalid email or password.'),
    })
    renderAt('/login')

    await user.type(await screen.findByLabelText('Email'), 'alice@example.com')
    await user.type(screen.getByLabelText('Password'), 'wrong password here')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.')
    expect(screen.getByLabelText('Password')).toHaveValue('')
    expect(screen.queryByRole('navigation', { name: 'Dashboard' })).not.toBeInTheDocument()
  })

  it('shows the lockout message', async () => {
    const user = userEvent.setup()
    mockApi({
      'GET /api/me': notSignedIn,
      'POST /api/auth/login': apiError(
        429,
        'login_locked',
        'Too many failed sign-in attempts. Try again in 15 minute(s).',
        { retry_after_seconds: 900 },
      ),
    })
    renderAt('/login')

    await user.type(await screen.findByLabelText('Email'), 'alice@example.com')
    await user.type(screen.getByLabelText('Password'), 'whatever password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Try again in 15 minute(s)')
  })

  it('sends a user with a temporary password to the change-password page', async () => {
    mockApi({
      'GET /api/me': apiError(403, 'password_change_required', 'Set a new password before continuing.'),
    })
    renderAt('/')

    expect(await screen.findByRole('heading', { name: 'Set a new password' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Dashboard' })).not.toBeInTheDocument()
  })
})

describe('Dashboard tabs follow effective permissions (AC-A23, AC-A24)', () => {
  it('user with zero roles sees only the Account tab', async () => {
    mockApi({ 'GET /api/me': { body: me([]) } })
    renderAt('/')

    expect(await dashboardTabs()).toEqual(['Account'])
    expect(await screen.findByRole('heading', { name: 'Account' })).toBeInTheDocument()
  })

  it('cohort.create user sees user-dashboard tabs only', async () => {
    mockApi({ 'GET /api/me': { body: me(['cohort.create', 'cohort.export']) } })
    renderAt('/')

    expect(await dashboardTabs()).toEqual(['New cohort', 'History', 'Account'])
  })

  it('admin sees every tab', async () => {
    mockApi({
      'GET /api/me': { body: me(ALL_PERMISSIONS, { is_admin: true }) },
      'GET /api/admin/users': { body: { items: [] } },
      'GET /api/admin/roles': { body: { items: [] } },
      'GET /api/admin/permissions': { body: { items: [] } },
      'GET /api/audit/events': { body: { items: [], total: 0, limit: 50, offset: 0 } },
    })
    renderAt('/')

    expect(await dashboardTabs()).toEqual([
      'New cohort',
      'History',
      'Account',
      'Users',
      'Roles',
      'Semantic context',
      'Audit',
    ])
  })

  it('shows placeholder content for tabs delivered by later features', async () => {
    mockApi({ 'GET /api/me': { body: me(['cohort.create']) } })
    renderAt('/cohorts/new')

    expect(await screen.findByRole('heading', { name: 'New cohort' })).toBeInTheDocument()
    expect(screen.getByText(/coming soon/i)).toBeInTheDocument()
  })

  it('serves the Semantic context tab (no longer a placeholder)', async () => {
    const calls = mockApi({
      'GET /api/me': { body: me(['semantic_context.read']) },
      'GET /api/semantic/version': { body: { version: 'f'.repeat(64), components: {} } },
      'GET /api/semantic/docs': { body: { tables: [], latest_run: null } },
    })
    renderAt('/semantic')

    expect(await screen.findByRole('heading', { name: 'Semantic context' })).toBeInTheDocument()
    expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument()
    await waitFor(() => expect(calls.some((c) => c.path === '/api/semantic/docs')).toBe(true))
  })

  it('direct navigation to the Semantic context tab without permission calls no semantic API', async () => {
    const calls = mockApi({ 'GET /api/me': { body: me(['cohort.create']) } })
    renderAt('/semantic')

    expect(await screen.findByText(/you don't have access to this page/i)).toBeInTheDocument()
    expect(calls.some((c) => c.path.startsWith('/api/semantic') || c.path.startsWith('/api/crawler'))).toBe(
      false,
    )
  })

  it('direct navigation to a hidden tab shows no content and calls no admin API', async () => {
    const calls = mockApi({ 'GET /api/me': { body: me(['cohort.create']) } })
    renderAt('/admin/users')

    expect(await screen.findByText(/you don't have access to this page/i)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Users' })).not.toBeInTheDocument()
    expect(calls.some((c) => c.path.startsWith('/api/admin'))).toBe(false)
  })

  it('signs out with the CSRF header and returns to sign-in', async () => {
    const user = userEvent.setup()
    setCsrfCookie('csrf-from-cookie')
    let signedIn = true
    const calls = mockApi({
      'GET /api/me': () => (signedIn ? { body: me([]) } : notSignedIn),
      'POST /api/auth/logout': () => {
        signedIn = false
        return { status: 204 }
      },
    })
    renderAt('/account')

    await user.click(await screen.findByRole('button', { name: 'Sign out' }))

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    const logout = calls.find((c) => c.path === '/api/auth/logout')
    expect(logout?.headers['x-csrf-token']).toBe('csrf-from-cookie')
  })

  it('returns to sign-in when the session expires mid-use', async () => {
    const user = userEvent.setup()
    let expired = false
    mockApi({
      'GET /api/me': () => (expired ? notSignedIn : { body: me(['audit.read']) }),
      'GET /api/audit/events': () => {
        expired = true
        return notSignedIn
      },
    })
    renderAt('/account')

    await user.click(await screen.findByRole('link', { name: 'Audit' }))

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument(),
    )
  })
})
