import { useState, type FormEvent } from 'react'
import { api, errorMessage } from '../api/client.ts'

export const MIN_PASSWORD_LENGTH = 12
export const MAX_PASSWORD_LENGTH = 128

/** Mirrors the server policy for fast feedback; the server remains the authority. */
function localProblem(current: string, next: string, confirm: string): string | null {
  if ([...next].length < MIN_PASSWORD_LENGTH) {
    return `Use at least ${MIN_PASSWORD_LENGTH} characters.`
  }
  if ([...next].length > MAX_PASSWORD_LENGTH) {
    return `Use at most ${MAX_PASSWORD_LENGTH} characters.`
  }
  if (next !== confirm) {
    return 'The new passwords do not match.'
  }
  if (next === current) {
    return 'The new password must differ from the current one.'
  }
  return null
}

export function ChangePasswordForm({ onChanged }: { onChanged: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const problem = localProblem(current, next, confirm)
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api<void>('/api/auth/password', {
        method: 'POST',
        body: { current_password: current, new_password: next },
      })
      setCurrent('')
      setNext('')
      setConfirm('')
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="stack" aria-label="Change password" onSubmit={(event) => void submit(event)}>
      <label className="field">
        <span>Current password</span>
        <input
          type="password"
          autoComplete="current-password"
          required
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
        />
      </label>
      <label className="field">
        <span>New password</span>
        <input
          type="password"
          autoComplete="new-password"
          required
          value={next}
          aria-describedby="new-password-hint"
          onChange={(event) => setNext(event.target.value)}
        />
      </label>
      <small id="new-password-hint" className="muted hint">
        {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters. A few unrelated words work well.
      </small>
      <label className="field">
        <span>Confirm new password</span>
        <input
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
        />
      </label>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="primary" disabled={busy}>
        Change password
      </button>
    </form>
  )
}
