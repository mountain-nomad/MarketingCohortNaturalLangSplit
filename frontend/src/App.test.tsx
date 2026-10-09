import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { App } from './App.tsx'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

describe('App shell', () => {
  it('renders the CohortSplit heading', () => {
    renderAt('/')

    expect(screen.getByRole('heading', { level: 1, name: 'CohortSplit' })).toBeInTheDocument()
  })

  it('renders the login placeholder at /login', () => {
    renderAt('/login')

    expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument()
    expect(screen.getByText(/login is not available yet/i)).toBeInTheDocument()
  })

  it('navigates from the shell to the login placeholder', async () => {
    const user = userEvent.setup()
    renderAt('/')
    expect(screen.queryByRole('heading', { name: /sign in/i })).not.toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: /sign in/i }))

    expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument()
  })
})
