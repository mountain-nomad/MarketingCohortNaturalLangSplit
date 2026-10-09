import { useState, type FormEvent } from 'react'
import { api, errorMessage } from '../api/client.ts'
import type { ListOf } from '../api/types.ts'
import { failureFrom, type Failure } from './failure.ts'
import { ProblemAlert } from './ProblemAlert.tsx'
import type { BusinessContextEntry, EntryKind } from './types.ts'
import { useLoad } from './useLoad.ts'

const KINDS: { value: EntryKind; label: string }[] = [
  { value: 'metric', label: 'Metric (e.g. purchase)' },
  { value: 'term', label: 'Term / synonym' },
  { value: 'status_semantics', label: 'Status semantics' },
  { value: 'time_window', label: 'Default time window' },
  { value: 'exclusion', label: 'Exclusion' },
  { value: 'canonical_user_id', label: 'Canonical user identifier' },
]

/** Starting points per kind; every reference must exist in the latest crawl. */
const TEMPLATES: Record<EntryKind, object> = {
  metric: {
    kind: 'metric',
    table: 'public.orders',
    filters: [{ column: 'public.orders.status', operator: 'in', value: ['paid', 'shipped', 'delivered'] }],
  },
  term: { kind: 'term', table: 'public.users' },
  status_semantics: {
    kind: 'status_semantics',
    column: 'public.orders.status',
    meanings: { paid: 'Payment captured' },
  },
  time_window: { kind: 'time_window', last_days: 30 },
  exclusion: {
    kind: 'exclusion',
    table: 'public.users',
    filters: [{ column: 'public.users.deleted_at', operator: 'is_not_null' }],
  },
  canonical_user_id: { kind: 'canonical_user_id', column: 'public.users.user_id' },
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

interface Draft {
  editing: string | null
  key: string
  kind: EntryKind
  synonyms: string
  description: string
  definition: string
}

function newDraft(): Draft {
  return {
    editing: null,
    key: '',
    kind: 'metric',
    synonyms: '',
    description: '',
    definition: pretty(TEMPLATES.metric),
  }
}

function draftOf(entry: BusinessContextEntry): Draft {
  return {
    editing: entry.key,
    key: entry.key,
    kind: entry.kind,
    synonyms: entry.synonyms.join(', '),
    description: entry.description,
    definition: pretty(entry.definition),
  }
}

function EntryEditor({
  initial,
  onSaved,
  onCancel,
}: {
  initial: Draft
  onSaved: () => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState<Draft>(initial)
  const [failure, setFailure] = useState<Failure | null>(null)
  const [busy, setBusy] = useState(false)

  function changeKind(kind: EntryKind) {
    setDraft((d) => ({ ...d, kind, definition: pretty(TEMPLATES[kind]) }))
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    let definition: unknown
    try {
      definition = JSON.parse(draft.definition)
    } catch {
      setFailure({ message: 'Definition must be valid JSON.', problems: [] })
      return
    }
    const fields = {
      synonyms: draft.synonyms
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
      description: draft.description,
      definition,
    }
    setBusy(true)
    setFailure(null)
    try {
      if (draft.editing === null) {
        await api('/api/semantic/business-context', { method: 'POST', body: { key: draft.key, ...fields } })
      } else {
        await api(`/api/semantic/business-context/${encodeURIComponent(draft.editing)}`, {
          method: 'PUT',
          body: fields,
        })
      }
      onSaved()
    } catch (err) {
      setFailure(failureFrom(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="stack card" aria-label="Business-context entry" onSubmit={(e) => void submit(e)}>
      <h3>{draft.editing === null ? 'New entry' : `Edit ${draft.editing}`}</h3>
      {failure && <ProblemAlert failure={failure} />}
      <div className="row">
        <label className="field">
          <span>Key</span>
          <input
            required
            value={draft.key}
            disabled={draft.editing !== null}
            pattern="[a-z][a-z0-9_]{0,63}"
            aria-describedby="key-hint"
            onChange={(e) => setDraft({ ...draft, key: e.target.value })}
          />
        </label>
        <label className="field">
          <span>Kind</span>
          <select value={draft.kind} onChange={(e) => changeKind(e.target.value as EntryKind)}>
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p id="key-hint" className="hint">
        Lowercase letters, digits and underscores, e.g. <code>purchase</code>. The key cannot change later.
      </p>
      <label className="field">
        <span>Synonyms</span>
        <input
          placeholder="bought, purchased"
          value={draft.synonyms}
          onChange={(e) => setDraft({ ...draft, synonyms: e.target.value })}
        />
      </label>
      <label className="field">
        <span>Description</span>
        <textarea
          rows={2}
          value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
        />
      </label>
      <label className="field">
        <span>Definition (JSON)</span>
        <textarea
          className="code"
          rows={8}
          spellCheck={false}
          value={draft.definition}
          onChange={(e) => setDraft({ ...draft, definition: e.target.value })}
        />
      </label>
      <p className="hint">
        Reference tables as <code>schema.table</code> and columns as <code>schema.table.column</code>; values are
        literals, never SQL. Unknown tables or columns are refused.
      </p>
      <div className="actions">
        <button type="button" className="quiet" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" disabled={busy}>
          Save entry
        </button>
      </div>
    </form>
  )
}

function Entry({
  entry,
  canEdit,
  onEdit,
  onDelete,
}: {
  entry: BusinessContextEntry
  canEdit: boolean
  onEdit: () => void
  onDelete: () => void
}) {
  return (
    <article className={entry.missing_references.length ? 'card entry stale' : 'card entry'}>
      <div className="card-head">
        <h3>
          <code>{entry.key}</code>
        </h3>
        <span className="muted">{entry.kind}</span>
      </div>
      {entry.synonyms.length > 0 && (
        <ul className="chips" aria-label={`Synonyms of ${entry.key}`}>
          {entry.synonyms.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}
      {entry.description && <p>{entry.description}</p>}
      <pre className="code-block">{pretty(entry.definition)}</pre>
      {entry.missing_references.length > 0 && (
        <div className="warning">
          <p>Not in the latest crawl, so this entry is not used until fixed:</p>
          <ul>
            {entry.missing_references.map((p) => (
              <li key={`${p.reference}-${p.problem}`}>
                {p.reference}: {p.problem}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="muted small">
        Updated {new Date(entry.updated_at).toLocaleString()}
        {entry.updated_by ? ` by ${entry.updated_by.display_name}` : ''}
      </p>
      {canEdit && (
        <div className="actions">
          <button type="button" className="quiet" aria-label={`Edit ${entry.key}`} onClick={onEdit}>
            Edit
          </button>
          <button type="button" className="quiet danger" aria-label={`Delete ${entry.key}`} onClick={onDelete}>
            Delete
          </button>
        </div>
      )}
    </article>
  )
}

/** Human-authored business context: authoritative over generated docs (FR-3). */
export function BusinessContextView({ canEdit, onChanged }: { canEdit: boolean; onChanged: () => void }) {
  const { data, error, reload } = useLoad<ListOf<BusinessContextEntry>>('/api/semantic/business-context')
  const [editing, setEditing] = useState<Draft | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  function saved() {
    setEditing(null)
    reload()
    onChanged()
  }

  async function remove(entry: BusinessContextEntry) {
    if (!window.confirm(`Delete business-context entry "${entry.key}"?`)) return
    setActionError(null)
    try {
      await api(`/api/semantic/business-context/${encodeURIComponent(entry.key)}`, { method: 'DELETE' })
      reload()
      onChanged()
    } catch (err) {
      setActionError(errorMessage(err))
    }
  }

  return (
    <div className="stack">
      <p className="muted">
        Business definitions written by people. They are authoritative and are the only definitions the
        cohort assistant may use.
      </p>
      {canEdit && editing === null && (
        <div>
          <button type="button" onClick={() => setEditing(newDraft())}>
            New entry
          </button>
        </div>
      )}
      {editing && (
        <EntryEditor
          key={editing.editing ?? 'new'}
          initial={editing}
          onSaved={saved}
          onCancel={() => setEditing(null)}
        />
      )}
      {actionError && (
        <p className="error" role="alert">
          {actionError}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && data.items.length === 0 && <p className="muted">No business context yet.</p>}
      {data?.items.map((entry) => (
        <Entry
          key={entry.key}
          entry={entry}
          canEdit={canEdit}
          onEdit={() => setEditing(draftOf(entry))}
          onDelete={() => void remove(entry)}
        />
      ))}
    </div>
  )
}
