import { useState, type FormEvent } from 'react'
import { api, ApiError, errorMessage } from '../api/client.ts'
import type { Me } from '../api/types.ts'
import type { Clarification, Interpretation, InterpretResult, Preview, Rejection, RejectionReason } from './types.ts'

const REASONS: Record<RejectionReason, string> = {
  mutation: 'This request changes data',
  analytics: 'This is an analytics question, not an audience',
  unknown_term: 'Something in the request is not in the semantic context',
  unsupported_construct: 'This kind of request is not supported yet',
}

function short(hash: string): string {
  return hash.slice(0, 12)
}

function parseColumns(text: string): string[] {
  return text
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
}

function ClarificationCard({ result }: { result: Clarification }) {
  return (
    <section className="card" aria-labelledby="clarification-title">
      <h3 id="clarification-title">A question before we continue</h3>
      <p>{result.question}</p>
      {result.unknown_values.length > 0 && (
        <ul>
          {result.unknown_values.map((v) => (
            <li key={`${v.column}-${v.value}`}>
              <code>{v.column}</code> has no value “{v.value}”
              {v.suggestions.length > 0 && <span> — did you mean {v.suggestions.join(', ')}?</span>}
            </li>
          ))}
        </ul>
      )}
      <p className="muted">Rephrase the request above and interpret it again.</p>
    </section>
  )
}

function RejectionCard({ result }: { result: Rejection }) {
  return (
    <section className="card" aria-labelledby="rejection-title">
      <h3 id="rejection-title">{REASONS[result.reason]}</h3>
      <p>{result.message}</p>
    </section>
  )
}

function InterpretationCard({
  result,
  canExport,
  busy,
  onConfirm,
}: {
  result: Interpretation
  canExport: boolean
  busy: boolean
  onConfirm: (columns: string[]) => void
}) {
  const [columns, setColumns] = useState('')
  return (
    <section className="card" aria-labelledby="interpretation-title">
      <h3 id="interpretation-title">Interpretation</h3>
      <p className="code-block">{result.readable}</p>
      <details className="details">
        <summary>Structured specification</summary>
        <pre className="code-block small">{JSON.stringify(result.spec, null, 2)}</pre>
      </details>
      <p className="muted small">
        Spec {short(result.spec_hash)} · semantic version {short(result.semantic_version)}
      </p>
      {canExport && (
        <label className="field">
          Extra export columns (optional, comma-separated)
          <input
            value={columns}
            placeholder="public.users.phone"
            onChange={(event) => setColumns(event.target.value)}
          />
        </label>
      )}
      <div className="actions">
        <button type="button" disabled={busy} onClick={() => onConfirm(parseColumns(columns))}>
          Confirm and preview
        </button>
      </div>
    </section>
  )
}

function PreviewCard({ preview }: { preview: Preview }) {
  const nulls = Object.entries(preview.null_counts)
  return (
    <section className="card" aria-labelledby="preview-title">
      <h3 id="preview-title">Preview</h3>
      <dl className="facts">
        <dt>Users in cohort</dt>
        <dd>{preview.total_count}</dd>
      </dl>
      {preview.disabled_reason && <p className="warning">{preview.disabled_reason}</p>}
      {preview.sample.rows.length > 0 && (
        <div className="table-wrap">
          <table aria-label="Sample rows">
            <thead>
              <tr>
                {preview.sample.columns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.sample.rows.map((row, index) => (
                <tr key={index}>
                  {row.map((cell, i) => (
                    <td key={i}>{cell === null ? <span className="muted">NULL</span> : String(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {nulls.length > 0 && (
        <table aria-label="NULL counts">
          <thead>
            <tr>
              <th>Export column</th>
              <th>Users with NULL</th>
            </tr>
          </thead>
          <tbody>
            {nulls.map(([column, count]) => (
              <tr key={column}>
                <td>{column}</td>
                <td>{count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h4>Generated SQL (read-only)</h4>
      <pre className="code-block small">{preview.sql}</pre>
      <p className="muted small">
        Parameters {JSON.stringify(preview.sql_params)} · evaluated at {preview.as_of} · SQL{' '}
        {short(preview.sql_hash)} · row cap {preview.row_cap.toLocaleString()}
      </p>
    </section>
  )
}

/** New cohort: request -> interpretation / clarification / rejection -> confirm -> preview. */
export function NewCohortTab({ me }: { me: Me }) {
  const [request, setRequest] = useState('')
  const [result, setResult] = useState<InterpretResult | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const canExport = me.permissions.includes('cohort.export')

  async function interpret(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setResult(null)
    setPreview(null)
    try {
      setResult(await api<InterpretResult>('/api/cohorts/interpret', { method: 'POST', body: { nl_request: request } }))
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setBusy(false)
    }
  }

  async function confirm(interpretation: Interpretation, columns: string[]) {
    setBusy(true)
    setError(null)
    try {
      setPreview(
        await api<Preview>('/api/cohorts/preview', {
          method: 'POST',
          body: {
            interpretation_id: interpretation.interpretation_id,
            spec_hash: interpretation.spec_hash,
            semantic_version: interpretation.semantic_version,
            export_columns: columns,
          },
        }),
      )
    } catch (failure) {
      setError(errorMessage(failure))
      // 404 (expired/unknown) and 409 (changed context/mismatch): interpret again.
      if (failure instanceof ApiError && (failure.status === 404 || failure.status === 409)) {
        setResult(null)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel stack" aria-labelledby="new-cohort-title">
      <h2 id="new-cohort-title">New cohort</h2>
      <form className="stack" onSubmit={(event) => void interpret(event)}>
        <label className="field">
          Describe the audience
          <textarea
            value={request}
            rows={3}
            maxLength={1000}
            placeholder="Users from Germany who abandoned a cart and never paid for an order"
            onChange={(event) => setRequest(event.target.value)}
          />
        </label>
        <div className="actions">
          <button type="submit" disabled={busy || request.trim() === ''}>
            Interpret
          </button>
        </div>
      </form>
      {error && (
        <div className="error" role="alert">
          <p>{error}</p>
        </div>
      )}
      {result?.kind === 'interpretation' && !preview && (
        <InterpretationCard
          result={result}
          canExport={canExport}
          busy={busy}
          onConfirm={(columns) => void confirm(result, columns)}
        />
      )}
      {result?.kind === 'clarification' && <ClarificationCard result={result} />}
      {result?.kind === 'rejection' && <RejectionCard result={result} />}
      {preview && <PreviewCard preview={preview} />}
    </section>
  )
}
