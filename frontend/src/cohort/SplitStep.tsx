import { useState, type FormEvent } from 'react'
import { api, ApiError, downloadFile, errorMessage } from '../api/client.ts'
import { ID_ONLY_RETRY } from './exportErrors.ts'
import type { Me } from '../api/types.ts'
import type { ExperimentType, Preview, SplitResult } from './types.ts'

/** Same rule as the server (ruling E2): the key names the exported files. */
const KEY_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9])?$/
const KEY_RULE = "Use 1-100 letters, digits, '_', '-' or '.', starting and ending with a letter or digit."

function keyProblem(key: string): string | null {
  const trimmed = key.trim()
  if (!trimmed) return null // required: the button stays disabled
  return KEY_PATTERN.test(trimmed) ? null : KEY_RULE
}

function shareProblem(test: string): string | null {
  const value = Number(test)
  return Number.isInteger(value) && value >= 1 && value <= 99
    ? null
    : 'Test share must be a whole number between 1 and 99.'
}

function RunResult({ result, canExport }: { result: SplitResult; canExport: boolean }) {
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [idsOnlyOffered, setIdsOnlyOffered] = useState(false)
  const needsConfirmation = result.requires_empty_group_confirmation

  async function download(idsOnly = false) {
    setBusy(true)
    setError(null)
    setSaved(null)
    setIdsOnlyOffered(false)
    const body: Record<string, unknown> = { confirm_empty_group: needsConfirmation && confirmed }
    if (idsOnly) body.columns = []
    try {
      setSaved(await downloadFile(`/api/runs/${result.id}/download`, body, `cohortsplit_${result.experiment_key}.zip`))
    } catch (failure) {
      setError(errorMessage(failure))
      setIdsOnlyOffered(!idsOnly && failure instanceof ApiError && ID_ONLY_RETRY.has(failure.code))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card" aria-labelledby="split-result-title">
      <h3 id="split-result-title">Split result</h3>
      <dl className="facts">
        <dt>Test</dt>
        <dd>{result.counts.test}</dd>
        <dt>Control</dt>
        <dd>{result.counts.control}</dd>
        <dt>Total</dt>
        <dd>{result.counts.total}</dd>
      </dl>
      <p className="muted small">
        Key <code>{result.experiment_key}</code> · {result.allocation.test} / {result.allocation.control}
        {result.experiment_type === 'aa' ? ' · A/A check: treat both groups the same' : ' · test receives the campaign'}
        {result.preview_count !== result.counts.total && ` · preview showed ${result.preview_count}`}
      </p>
      {result.warnings.map((warning) => (
        <p key={`${warning.code}-${warning.group}`} className="warning">
          {warning.message}
        </p>
      ))}
      {canExport ? (
        <>
          {needsConfirmation && (
            <label className="checkbox">
              <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
              Export with an empty group
            </label>
          )}
          <div className="actions">
            <button type="button" disabled={busy || (needsConfirmation && !confirmed)} onClick={() => void download()}>
              Download ZIP
            </button>
          </div>
          {saved && <p className="muted">Downloaded {saved}. The run is in the History tab.</p>}
          {error && (
            <div className="error" role="alert">
              <p>{error}</p>
              {idsOnlyOffered && (
                <button type="button" disabled={busy} onClick={() => void download(true)}>
                  Download user ids only
                </button>
              )}
            </div>
          )}
        </>
      ) : (
        <p className="muted">Downloading needs the cohort.export permission. The run is in the History tab.</p>
      )}
    </section>
  )
}

/** Campaign steps 6-8: experiment key, allocation, columns -> counts -> download. */
export function SplitStep({ me, preview }: { me: Me; preview: Preview }) {
  const canExport = me.permissions.includes('cohort.export')
  const grantable = canExport ? (me.export_columns ?? []) : []
  const [key, setKey] = useState('')
  const [test, setTest] = useState('50')
  const [type, setType] = useState<ExperimentType>('ab')
  const [columns, setColumns] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<SplitResult | null>(null)

  const keyError = keyProblem(key)
  const shareError = shareProblem(test)
  const control = shareError ? null : 100 - Number(test)
  const ready = key.trim() !== '' && !keyError && !shareError && !busy

  function toggle(column: string, on: boolean) {
    setColumns((current) => (on ? [...current, column] : current.filter((c) => c !== column)))
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!ready || control === null || !preview.prepared_cohort_id) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      setResult(
        await api<SplitResult>('/api/runs', {
          method: 'POST',
          body: {
            prepared_cohort_id: preview.prepared_cohort_id,
            experiment_key: key.trim(),
            allocation: { test: Number(test), control },
            experiment_type: type,
            export_columns: grantable.filter((c) => columns.includes(c)),
          },
        }),
      )
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <section className="card" aria-labelledby="split-title">
        <h3 id="split-title">Split into test and control</h3>
        <form className="stack" onSubmit={(event) => void submit(event)}>
          <label className="field">
            Experiment key
            <input
              value={key}
              maxLength={100}
              placeholder="push_black_friday_2026"
              aria-invalid={keyError ? true : undefined}
              onChange={(event) => setKey(event.target.value)}
            />
          </label>
          {keyError && <p className="field-error">{keyError}</p>}
          <label className="field">
            Test group (%)
            <input
              type="number"
              min={1}
              max={99}
              step={1}
              value={test}
              aria-invalid={shareError ? true : undefined}
              onChange={(event) => setTest(event.target.value)}
            />
          </label>
          {shareError ? (
            <p className="field-error">{shareError}</p>
          ) : (
            <p className="muted small">Control group: {control}%</p>
          )}
          <label className="checkbox">
            <input type="checkbox" checked={type === 'aa'} onChange={(event) => setType(event.target.checked ? 'aa' : 'ab')} />
            A/A check (treat both groups the same)
          </label>
          {grantable.length > 0 && (
            <fieldset className="stack">
              <legend>Extra export columns (the user id is always included)</legend>
              {grantable.map((column) => (
                <label key={column} className="checkbox">
                  <input
                    type="checkbox"
                    name={column}
                    checked={columns.includes(column)}
                    onChange={(event) => toggle(column, event.target.checked)}
                  />
                  {column}
                </label>
              ))}
            </fieldset>
          )}
          <p className="muted small">
            Assignment is deterministic: the same key and allocation always put a user in the same group.
          </p>
          <div className="actions">
            <button type="submit" disabled={!ready}>
              Split
            </button>
          </div>
        </form>
        {error && (
          <div className="error" role="alert">
            <p>{error}</p>
          </div>
        )}
      </section>
      {result && <RunResult key={result.id} result={result} canExport={canExport} />}
    </>
  )
}
