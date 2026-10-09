import { useState, type FormEvent } from 'react'
import { errorMessage } from '../api/client.ts'
import { useSession } from './session.ts'

export function LoginPage() {
  const { login } = useSession()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email, password)
    } catch (err) {
      setError(errorMessage(err))
      setPassword('')
      setBusy(false)
    }
  }

  return (
    <section className="auth-card" aria-labelledby="login-title">
      <h2 id="login-title">Sign in</h2>
      <p className="muted">Use the email and password your administrator gave you.</p>
      <form className="stack" onSubmit={(event) => void submit(event)}>
        <label className="field">
          <span>Email</span>
          <input
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="primary" disabled={busy}>
          Sign in
        </button>
      </form>
    </section>
  )
}
