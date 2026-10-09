import { useEffect, useState, type FormEvent } from 'react'
import { api, errorMessage } from '../api/client.ts'
import type { AuditEvent, AuditPage } from '../api/types.ts'

const PAGE_SIZE = 50

interface Filters {
  actorEmail: string
  action: string
  outcome: '' | 'success' | 'denied' | 'error'
  from: string
  to: string
}

const NO_FILTERS: Filters = { actorEmail: '', action: '', outcome: '', from: '', to: '' }

function toIso(local: string): string | undefined {
  return local ? new Date(local).toISOString() : undefined
}

function actorLabel(event: AuditEvent): string {
  return event.actor_email ?? event.actor_type
}

function targetLabel(event: AuditEvent): string {
  if (!event.target_type) return '—'
  return event.target_id ? `${event.target_type} ${event.target_id}` : event.target_type
}

export function AuditTab() {
  const [draft, setDraft] = useState<Filters>(NO_FILTERS)
  const [applied, setApplied] = useState<Filters>(NO_FILTERS)
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<AuditPage | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api<AuditPage>('/api/audit/events', {
      query: {
        actor_email: applied.actorEmail.trim(),
        action: applied.action.trim(),
        outcome: applied.outcome,
        since: toIso(applied.from),
        until: toIso(applied.to),
        limit: PAGE_SIZE,
        offset,
      },
    })
      .then((result) => {
        if (!cancelled) {
          setPage(result)
          setError(null)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorMessage(err))
      })
    return () => {
      cancelled = true
    }
  }, [applied, offset])

  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setApplied({ ...draft })
    setOffset(0)
  }

  const first = page && page.total > 0 ? page.offset + 1 : 0
  const lastOnPage = page ? Math.min(page.offset + PAGE_SIZE, page.total) : 0

  return (
    <section className="panel" aria-labelledby="audit-title">
      <h2 id="audit-title">Audit log</h2>
      <p className="muted">Append-only record of sign-ins, access changes and exports.</p>

      <form className="filters" aria-label="Audit filters" onSubmit={apply}>
        <label className="field">
          <span>Actor email</span>
          <input value={draft.actorEmail} onChange={(e) => setDraft({ ...draft, actorEmail: e.target.value })} />
        </label>
        <label className="field">
          <span>Action</span>
          <input
            placeholder="e.g. role.update"
            value={draft.action}
            onChange={(e) => setDraft({ ...draft, action: e.target.value })}
          />
        </label>
        <label className="field">
          <span>Outcome</span>
          <select
            value={draft.outcome}
            onChange={(e) => setDraft({ ...draft, outcome: e.target.value as Filters['outcome'] })}
          >
            <option value="">Any</option>
            <option value="success">success</option>
            <option value="denied">denied</option>
            <option value="error">error</option>
          </select>
        </label>
        <label className="field">
          <span>From</span>
          <input type="datetime-local" value={draft.from} onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
        </label>
        <label className="field">
          <span>To</span>
          <input type="datetime-local" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
        </label>
        <button type="submit" className="primary">
          Apply filters
        </button>
      </form>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {page === null ? (
        <p className="muted">Loading events…</p>
      ) : page.items.length === 0 ? (
        <p className="muted">No events match these filters.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Actor</th>
                <th scope="col">Action</th>
                <th scope="col">Target</th>
                <th scope="col">Outcome</th>
                <th scope="col">Details</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((event) => (
                <tr key={event.id}>
                  <td>
                    <time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleString()}</time>
                  </td>
                  <td>{actorLabel(event)}</td>
                  <td>
                    <code>{event.action}</code>
                  </td>
                  <td>{targetLabel(event)}</td>
                  <td>
                    <span className={`outcome outcome-${event.outcome}`}>{event.outcome}</span>
                  </td>
                  <td className="details">
                    {Object.keys(event.metadata).length > 0 && (
                      <code className="metadata">{JSON.stringify(event.metadata)}</code>
                    )}
                    {event.request_id && <small className="muted"> request {event.request_id}</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {page !== null && page.total > 0 && (
        <div className="pager">
          <span>
            Showing {first}–{lastOnPage} of {page.total}
          </span>
          <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            Previous
          </button>
          <button
            type="button"
            disabled={offset + PAGE_SIZE >= page.total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next
          </button>
        </div>
      )}
    </section>
  )
}
