import { useState } from 'react'
import type { Me } from '../api/types.ts'
import { ChangePasswordForm } from '../auth/ChangePasswordForm.tsx'

export function AccountTab({ me }: { me: Me }) {
  const [changed, setChanged] = useState(false)

  return (
    <section className="panel" aria-labelledby="account-title">
      <h2 id="account-title">Account</h2>
      <dl className="facts">
        <dt>Name</dt>
        <dd>{me.display_name}</dd>
        <dt>Email</dt>
        <dd>{me.email}</dd>
        <dt>Roles</dt>
        <dd>
          {me.roles.length === 0 ? (
            <span className="muted">No roles yet. Ask an administrator for access.</span>
          ) : (
            <ul className="chips">
              {me.roles.map((role) => (
                <li key={role.id}>{role.name}</li>
              ))}
            </ul>
          )}
        </dd>
      </dl>
      <p className="muted">Only an administrator can change your name or roles.</p>

      <h3>Change password</h3>
      <p className="muted">Changing your password signs you out everywhere else.</p>
      {changed && (
        <p className="success" role="status">
          Password changed.
        </p>
      )}
      <ChangePasswordForm onChanged={() => setChanged(true)} />
    </section>
  )
}
