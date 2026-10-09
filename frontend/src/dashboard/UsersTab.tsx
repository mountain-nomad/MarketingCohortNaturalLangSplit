import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, errorMessage } from '../api/client.ts'
import type { AdminUser, ListOf, Role } from '../api/types.ts'

function toggle(ids: number[], id: number): number[] {
  return ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id].sort((a, b) => a - b)
}

function RoleCheckboxes({
  roles,
  selected,
  onToggle,
}: {
  roles: Role[]
  selected: number[]
  onToggle: (id: number) => void
}) {
  return (
    <fieldset className="checks">
      <legend>Roles</legend>
      {roles.length === 0 && <p className="muted">No roles yet.</p>}
      {roles.map((role) => (
        <label key={role.id} className="check">
          <input
            type="checkbox"
            checked={selected.includes(role.id)}
            onChange={() => onToggle(role.id)}
          />
          {role.name}
        </label>
      ))}
    </fieldset>
  )
}

function status(user: AdminUser): string {
  if (!user.is_active) return 'Deactivated'
  if (user.must_change_password) return 'Must set password'
  return 'Active'
}

function CreateUserForm({ roles, onCreated, onError }: {
  roles: Role[]
  onCreated: () => void
  onError: (message: string | null) => void
}) {
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [roleIds, setRoleIds] = useState<number[]>([])
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    onError(null)
    try {
      await api<AdminUser>('/api/admin/users', {
        method: 'POST',
        body: { email, display_name: displayName, temporary_password: password, role_ids: roleIds },
      })
      setEmail('')
      setDisplayName('')
      setPassword('')
      setRoleIds([])
      onCreated()
    } catch (err) {
      onError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="stack card" aria-label="Create user" onSubmit={(event) => void submit(event)}>
      <h3>Add a user</h3>
      <p className="muted">
        Hand the temporary password to the person yourself; they choose their own at first sign-in.
      </p>
      <div className="row">
        <label className="field">
          <span>Email</span>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field">
          <span>Display name</span>
          <input required value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span>Temporary password</span>
        <input
          type="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      <RoleCheckboxes roles={roles} selected={roleIds} onToggle={(id) => setRoleIds(toggle(roleIds, id))} />
      <button type="submit" className="primary" disabled={busy}>
        Create user
      </button>
    </form>
  )
}

function EditUserForm({ user, roles, onSaved, onError, onClose }: {
  user: AdminUser
  roles: Role[]
  onSaved: () => void
  onError: (message: string | null) => void
  onClose: () => void
}) {
  const original = user.roles.map((r) => r.id).sort((a, b) => a - b)
  const [displayName, setDisplayName] = useState(user.display_name)
  const [roleIds, setRoleIds] = useState<number[]>(original)
  const [tempPassword, setTempPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  async function run(action: () => Promise<void>, done: string) {
    setBusy(true)
    onError(null)
    setNotice(null)
    try {
      await action()
      setNotice(done)
      onSaved()
    } catch (err) {
      onError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void run(async () => {
      if (displayName !== user.display_name) {
        await api<AdminUser>(`/api/admin/users/${user.id}`, {
          method: 'PATCH',
          body: { display_name: displayName },
        })
      }
      if (roleIds.join(',') !== original.join(',')) {
        await api<AdminUser>(`/api/admin/users/${user.id}/roles`, {
          method: 'PUT',
          body: { role_ids: roleIds },
        })
      }
    }, 'Changes saved.')
  }

  function resetPassword() {
    void run(async () => {
      await api<AdminUser>(`/api/admin/users/${user.id}/reset-password`, {
        method: 'POST',
        body: { temporary_password: tempPassword },
      })
      setTempPassword('')
    }, 'Temporary password set. Their sessions were signed out.')
  }

  return (
    <form className="stack card" aria-label={`Edit ${user.email}`} onSubmit={save}>
      <div className="card-head">
        <h3>{user.email}</h3>
        <button type="button" className="quiet" onClick={onClose}>
          Close
        </button>
      </div>
      <label className="field">
        <span>Display name</span>
        <input required value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </label>
      <RoleCheckboxes roles={roles} selected={roleIds} onToggle={(id) => setRoleIds(toggle(roleIds, id))} />
      <button type="submit" className="primary" disabled={busy}>
        Save changes
      </button>
      <div className="divider" />
      <label className="field">
        <span>New temporary password</span>
        <input
          type="password"
          autoComplete="new-password"
          value={tempPassword}
          onChange={(e) => setTempPassword(e.target.value)}
        />
      </label>
      <button type="button" disabled={busy || tempPassword === ''} onClick={resetPassword}>
        Reset password
      </button>
      {notice && (
        <p className="success" role="status">
          {notice}
        </p>
      )}
    </form>
  )
}

export function UsersTab() {
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [roles, setRoles] = useState<Role[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const [userList, roleList] = await Promise.all([
        api<ListOf<AdminUser>>('/api/admin/users'),
        api<ListOf<Role>>('/api/admin/roles'),
      ])
      setUsers(userList.items)
      setRoles(roleList.items)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function setActive(user: AdminUser, active: boolean) {
    setError(null)
    try {
      await api<AdminUser>(`/api/admin/users/${user.id}/${active ? 'reactivate' : 'deactivate'}`, {
        method: 'POST',
      })
      await load()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const editingUser = users?.find((u) => u.id === editing) ?? null

  return (
    <section className="panel" aria-labelledby="users-title">
      <h2 id="users-title">Users</h2>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {users === null ? (
        <p className="muted">Loading users…</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Email</th>
                <th scope="col">Name</th>
                <th scope="col">Roles</th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id} className={user.is_active ? undefined : 'inactive'}>
                  <td>{user.email}</td>
                  <td>{user.display_name}</td>
                  <td>
                    <ul className="chips">
                      {user.roles.map((role) => (
                        <li key={role.id}>{role.name}</li>
                      ))}
                    </ul>
                  </td>
                  <td>{status(user)}</td>
                  <td className="actions">
                    <button type="button" className="quiet" onClick={() => setEditing(user.id)}>
                      Edit
                    </button>
                    {user.is_active ? (
                      <button type="button" className="quiet danger" onClick={() => void setActive(user, false)}>
                        Deactivate
                      </button>
                    ) : (
                      <button type="button" className="quiet" onClick={() => void setActive(user, true)}>
                        Reactivate
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="split">
        {editingUser && (
          <EditUserForm
            key={editingUser.id}
            user={editingUser}
            roles={roles}
            onSaved={() => void load()}
            onError={setError}
            onClose={() => setEditing(null)}
          />
        )}
        <CreateUserForm roles={roles} onCreated={() => void load()} onError={setError} />
      </div>
    </section>
  )
}
