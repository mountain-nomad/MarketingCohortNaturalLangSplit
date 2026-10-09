import { render, screen } from '@testing-library/react'
import { clearCookies, me } from '../test/mockApi.ts'
import { AccountTab } from './AccountTab.tsx'

afterEach(() => {
  clearCookies()
  vi.unstubAllGlobals()
})

describe('AccountTab', () => {
  it('shows identity and roles read-only, and offers a password change', () => {
    render(<AccountTab me={me(['cohort.create'])} />)

    expect(screen.getByRole('heading', { name: 'Account' })).toBeInTheDocument()
    expect(screen.getByText('Alice')).toBeInTheDocument()
    expect(screen.getByText('alice@example.com')).toBeInTheDocument()
    expect(screen.getByText('Marketer')).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: /display name/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change password' })).toBeInTheDocument()
  })

  it('says when the user has no roles', () => {
    render(<AccountTab me={me([])} />)

    expect(screen.getByText(/no roles yet/i)).toBeInTheDocument()
  })
})
