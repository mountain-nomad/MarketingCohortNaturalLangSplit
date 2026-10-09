import { ChangePasswordForm } from './ChangePasswordForm.tsx'
import { useSession } from './session.ts'

/** Shown while a temporary password is set; every other API call is refused until done. */
export function ChangePasswordPage() {
  const { refresh } = useSession()

  return (
    <section className="auth-card" aria-labelledby="change-title">
      <h2 id="change-title">Set a new password</h2>
      <p className="muted">
        You signed in with a temporary password. Choose your own password to continue.
      </p>
      <ChangePasswordForm onChanged={() => void refresh()} />
    </section>
  )
}
