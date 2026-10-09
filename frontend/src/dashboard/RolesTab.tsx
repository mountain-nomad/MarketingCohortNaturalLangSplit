import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { ApiError, api, errorMessage } from '../api/client.ts'
import type { AdminUser, ListOf, PermissionDef, Role } from '../api/types.ts'

interface Draft {
  id: number | null
  name: string
  description: string
  permissions: string[]
  columns: string
  memberIds: number[]
}

const EMPTY_DRAFT: Draft = { id: null, name: '', description: '', permissions: [], columns: '', memberIds: [] }

function draftFrom(role: Role): Draft {
  return {
    id: role.id,
    name: role.name,
    description: role.description,
    permissions: [...role.permissions],
    columns: role.export_columns.join('\n'),
    memberIds: [...role.member_ids],
  }
}

function parseColumns(text: string): string[] {
  const unique = new Set(
    text
      .split(/[\n,]/)
      .map((line) => line.trim())
      .filter(Boolean),
  )
  return [...unique].sort()
}

function sameIds(a: number[], b: number[]): boolean {
  return [...a].sort((x, y) => x - y).join(',') === [...b].sort((x, y) => x - y).join(',')
}

function RoleEditor({
  draft,
  catalog,
  users,
  original,
  onSaved,
  onCancel,
  onError,
}: {
  draft: Draft
  catalog: PermissionDef[]
  users: AdminUser[] | null
  original: Role | null
  onSaved: () => void
  onCancel: () => void
  onError: (message: string | null) => void
}) {
  const [value, setValue] = useState<Draft>(draft)
  const [busy, setBusy] = useState(false)

  // Admin-only permissions (user.*, role.*) are never offered (AC-A14a).
  const grantable = useMemo(() => catalog.filter((p) => p.grantable), [catalog])
  const areas = useMemo(() => [...new Set(grantable.map((p) => p.area))], [grantable])

  function togglePermission(key: string) {
    setValue((v) => ({
      ...v,
      permissions: v.permissions.includes(key)
        ? v.permissions.filter((k) => k !== key)
        : [...v.permissions, key],
    }))
  }

  function toggleMember(id: number) {
    setValue((v) => ({
      ...v,
      memberIds: v.memberIds.includes(id) ? v.memberIds.filter((m) => m !== id) : [...v.memberIds, id],
    }))
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    onError(null)
    const body = {
      name: value.name,
      description: value.description,
      permissions: [...value.permissions].sort(),
      export_columns: parseColumns(value.columns),
    }
    try {
      const saved =
        value.id === null
          ? await api<Role>('/api/admin/roles', { method: 'POST', body })
          : await api<Role>(`/api/admin/roles/${value.id}`, { method: 'PATCH', body })
      const before = original?.member_ids ?? []
      if (users !== null && !sameIds(before, value.memberIds)) {
        await api<Role>(`/api/admin/roles/${saved.id}/members`, {
          method: 'PUT',
          body: { user_ids: [...value.memberIds].sort((a, b) => a - b) },
        })
      }
      onSaved()
    } catch (err) {
      onError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="stack card" aria-label="Role editor" onSubmit={(event) => void submit(event)}>
      <h3>{value.id === null ? 'New role' : `Edit ${draft.name}`}</h3>
      <label className="field">
        <span>Name</span>
        <input required value={value.name} onChange={(e) => setValue({ ...value, name: e.target.value })} />
      </label>
      <label className="field">
        <span>Description</span>
        <input value={value.description} onChange={(e) => setValue({ ...value, description: e.target.value })} />
      </label>

      {areas.map((area) => (
        <fieldset key={area} className="checks">
          <legend>{area}</legend>
          {grantable
            .filter((p) => p.area === area)
            .map((p) => (
              <label key={p.key} className="check">
                <input
                  type="checkbox"
                  value={p.key}
                  checked={value.permissions.includes(p.key)}
                  onChange={() => togglePermission(p.key)}
                />
                <code>{p.key}</code>
                <span className="muted">{p.description}</span>
              </label>
            ))}
        </fieldset>
      ))}
      <p className="muted">User and role management stay with the protected Admin role.</p>

      <label className="field">
        <span>Exportable columns</span>
        <textarea
          rows={3}
          placeholder="public.users.phone"
          value={value.columns}
          aria-describedby="columns-hint"
          onChange={(e) => setValue({ ...value, columns: e.target.value })}
        />
      </label>
      {original && (original.missing_export_columns?.length ?? 0) > 0 && (
        <p className="warning">
          Not found in the latest crawl: {original.missing_export_columns?.join(', ')}. These grants
          stay inert until the column exists again.
        </p>
      )}
      <small id="columns-hint" className="muted hint">
        One <code>schema.table.column</code> per line. The user ID is always exportable with
        cohort.export.
      </small>

      {users !== null && (
        <fieldset className="checks">
          <legend>Members</legend>
          {users.map((user) => (
            <label key={user.id} className="check">
              <input
                type="checkbox"
                value={String(user.id)}
                checked={value.memberIds.includes(user.id)}
                onChange={() => toggleMember(user.id)}
              />
              {user.email}
            </label>
          ))}
        </fieldset>
      )}

      <div className="row">
        <button type="submit" className="primary" disabled={busy}>
          Save role
        </button>
        <button type="button" className="quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

export function RolesTab() {
  const [roles, setRoles] = useState<Role[] | null>(null)
  const [catalog, setCatalog] = useState<PermissionDef[]>([])
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<Role | null>(null)
  const [editing, setEditing] = useState<{ draft: Draft; original: Role | null } | null>(null)

  const load = useCallback(async () => {
    try {
      const [roleList, permissionList] = await Promise.all([
        api<ListOf<Role>>('/api/admin/roles'),
        api<ListOf<PermissionDef>>('/api/admin/permissions'),
      ])
      setRoles(roleList.items)
      setCatalog(permissionList.items)
    } catch (err) {
      setError(errorMessage(err))
      return
    }
    try {
      setUsers((await api<ListOf<AdminUser>>('/api/admin/users')).items)
    } catch {
      setUsers(null) // Members are managed only by those who may list users.
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function remove(role: Role, confirm: boolean) {
    setError(null)
    try {
      await api<void>(`/api/admin/roles/${role.id}`, {
        method: 'DELETE',
        query: confirm ? { confirm: 'true' } : undefined,
      })
      setPendingDelete(null)
      await load()
    } catch (err) {
      if (err instanceof ApiError && err.code === 'confirmation_required') {
        setPendingDelete(role)
      }
      setError(errorMessage(err))
    }
  }

  return (
    <section className="panel" aria-labelledby="roles-title">
      <div className="panel-head">
        <h2 id="roles-title">Roles</h2>
        <button
          type="button"
          className="primary"
          onClick={() => {
            setError(null)
            setEditing({ draft: EMPTY_DRAFT, original: null })
          }}
        >
          New role
        </button>
      </div>
      {error && (
        <div className="error" role="alert">
          <p>{error}</p>
          {pendingDelete && (
            <div className="row">
              <button type="button" className="danger" onClick={() => void remove(pendingDelete, true)}>
                Delete anyway
              </button>
              <button
                type="button"
                className="quiet"
                onClick={() => {
                  setPendingDelete(null)
                  setError(null)
                }}
              >
                Keep role
              </button>
            </div>
          )}
        </div>
      )}
      {roles === null ? (
        <p className="muted">Loading roles…</p>
      ) : (
        <ul className="role-list">
          {roles.map((role) => (
            <li key={role.id} className={role.is_system ? 'system' : undefined}>
              <div className="role-main">
                <strong>{role.name}</strong>
                {role.is_system ? (
                  <span className="badge">Protected: holds every permission, cannot be edited</span>
                ) : (
                  role.description && <p className="muted">{role.description}</p>
                )}
              </div>
              <p className="role-meta">
                {role.is_system ? 'Every permission' : `${role.permissions.length} permission(s)`} ·{' '}
                {role.member_ids.length} member(s)
              </p>
              {role.export_columns.length > 0 && (
                <ul className="chips export-columns" aria-label={`Exportable columns of ${role.name}`}>
                  {role.export_columns.map((column) => (
                    <li
                      key={column}
                      className={role.missing_export_columns?.includes(column) ? 'missing' : undefined}
                    >
                      {role.missing_export_columns?.includes(column) ? `${column} (missing)` : column}
                    </li>
                  ))}
                </ul>
              )}
              {!role.is_system && (
                <div className="actions">
                  <button
                    type="button"
                    className="quiet"
                    onClick={() => {
                      setError(null)
                      setEditing({ draft: draftFrom(role), original: role })
                    }}
                  >
                    Edit
                  </button>
                  <button type="button" className="quiet danger" onClick={() => void remove(role, false)}>
                    Delete
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {editing && (
        <RoleEditor
          key={editing.draft.id ?? 'new'}
          draft={editing.draft}
          original={editing.original}
          catalog={catalog}
          users={users}
          onError={setError}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            void load()
          }}
        />
      )}
    </section>
  )
}
