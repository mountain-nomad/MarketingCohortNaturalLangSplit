import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { apiError, clearCookies, mockApi, setCsrfCookie } from '../test/mockApi.ts'
import { ChangePasswordForm } from './ChangePasswordForm.tsx'

const CURRENT = 'temporary password 2026'
const NEW = 'a brand new passphrase'

async function fill(current: string, next: string, confirm: string) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Current password'), current)
  await user.type(screen.getByLabelText('New password'), next)
  await user.type(screen.getByLabelText('Confirm new password'), confirm)
  await user.click(screen.getByRole('button', { name: 'Change password' }))
}

afterEach(() => {
  clearCookies()
  vi.unstubAllGlobals()
})

describe('ChangePasswordForm', () => {
  it('refuses a confirmation that does not match, without calling the API', async () => {
    const calls = mockApi({})
    render(<ChangePasswordForm onChanged={() => {}} />)

    await fill(CURRENT, NEW, NEW + 'x')

    expect(screen.getByRole('alert')).toHaveTextContent(/do not match/i)
    expect(calls).toHaveLength(0)
  })

  it('refuses a password shorter than 12 characters', async () => {
    const calls = mockApi({})
    render(<ChangePasswordForm onChanged={() => {}} />)

    await fill(CURRENT, 'short pass', 'short pass')

    expect(screen.getByRole('alert')).toHaveTextContent(/at least 12 characters/i)
    expect(calls).toHaveLength(0)
  })

  it('refuses a password longer than 128 characters', async () => {
    const calls = mockApi({})
    render(<ChangePasswordForm onChanged={() => {}} />)
    const long = 'x'.repeat(129)

    await fill(CURRENT, long, long)

    expect(screen.getByRole('alert')).toHaveTextContent(/128/)
    expect(calls).toHaveLength(0)
  })

  it('refuses reusing the current password', async () => {
    const calls = mockApi({})
    render(<ChangePasswordForm onChanged={() => {}} />)

    await fill(CURRENT, CURRENT, CURRENT)

    expect(screen.getByRole('alert')).toHaveTextContent(/must differ/i)
    expect(calls).toHaveLength(0)
  })

  it('posts the change with the CSRF header and reports success', async () => {
    setCsrfCookie('csrf-123')
    const onChanged = vi.fn()
    const calls = mockApi({ 'POST /api/auth/password': { status: 204 } })
    render(<ChangePasswordForm onChanged={onChanged} />)

    await fill(CURRENT, NEW, NEW)

    await vi.waitFor(() => expect(onChanged).toHaveBeenCalledOnce())
    const [call] = calls
    expect(call?.method).toBe('POST')
    expect(call?.path).toBe('/api/auth/password')
    expect(call?.body).toEqual({ current_password: CURRENT, new_password: NEW })
    expect(call?.headers['x-csrf-token']).toBe('csrf-123')
  })

  it('shows the server error', async () => {
    mockApi({
      'POST /api/auth/password': apiError(400, 'invalid_current_password', 'Current password is incorrect.'),
    })
    render(<ChangePasswordForm onChanged={() => {}} />)

    await fill('wrong current pw', NEW, NEW)

    expect(await screen.findByRole('alert')).toHaveTextContent('Current password is incorrect.')
  })
})
