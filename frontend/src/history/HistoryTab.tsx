import { useEffect, useState } from 'react'
import { api, ApiError, downloadFile, errorMessage } from '../api/client.ts'
import type { Me } from '../api/types.ts'
import type { RunList, RunSummary } from '../cohort/types.ts'

const PAGE_SIZE = 50

interface DownloadOptions {
  idsOnly: boolean
  confirmEmptyGroup: boolean
}

type Problem = { run: RunSummary; message: string; columnsRefused: boolean; options: DownloadOptions }

function formatTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

/** Past runs with their exact membership; re-download needs cohort.export (FR-9a, FR-A6). */
export function HistoryTab({ me }: { me: Me }) {
  const canExport = me.permissions.includes('cohort.export')
  const [runs, setRuns] = useState<RunList | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<RunSummary | null>(null)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    api<RunList>('/api/runs', { query: { limit: PAGE_SIZE } })
      .then((result) => {
        if (!cancelled) setRuns(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(errorMessage(err))
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function download(run: RunSummary, options: DownloadOptions) {
    setBusy(true)
    setProblem(null)
    setSaved(null)
    setConfirming(null)
    const body: Record<string, unknown> = { confirm_empty_group: options.confirmEmptyGroup }
    if (options.idsOnly) body.columns = []
    try {
      setSaved(await downloadFile(`/api/runs/${run.id}/download`, body, `cohortsplit_${run.experiment_key}.zip`))
    } catch (failure) {
      setProblem({
        run,
        message: errorMessage(failure),
        columnsRefused: failure instanceof ApiError && failure.code === 'export_column_denied',
        options,
      })
    } finally {
      setBusy(false)
    }
  }

  function start(run: RunSummary) {
    if (run.requires_empty_group_confirmation) {
      setProblem(null)
      setSaved(null)
      setConfirming(run)
      return
    }
    void download(run, { idsOnly: false, confirmEmptyGroup: false })
  }

  const showCreator = runs?.scope === 'all'

  return (
    <section className="panel stack" aria-labelledby="history-title">
      <h2 id="history-title">History</h2>
      <p className="muted">
        Every split is kept with its exact membership. Downloading again rebuilds the same groups; extra columns are
        fetched again under your current export grants.
      </p>
      {loadError && (
        <div className="error" role="alert">
          <p>{loadError}</p>
        </div>
      )}
      {confirming && (
        <div className="warning confirm-bar">
          <span>
            The {confirming.empty_groups.join(' and ')} group is empty in <code>{confirming.experiment_key}</code>.
          </span>
          <button type="button" disabled={busy} onClick={() => void download(confirming, { idsOnly: false, confirmEmptyGroup: true })}>
            Download anyway
          </button>
          <button type="button" className="quiet" onClick={() => setConfirming(null)}>
            Cancel
          </button>
        </div>
      )}
      {problem && (
        <div className="error" role="alert">
          <p>{problem.message}</p>
          {problem.columnsRefused && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void download(problem.run, { ...problem.options, idsOnly: true })}
            >
              Download user ids only
            </button>
          )}
        </div>
      )}
      {saved && <p className="muted">Downloaded {saved}.</p>}
      {runs && runs.items.length === 0 && <p className="muted">No runs yet. Split a cohort in the New cohort tab.</p>}
      {runs && runs.items.length > 0 && (
        <div className="table-wrap">
          <table className="runs" aria-label="Runs">
            <thead>
              <tr>
                <th>Created</th>
                <th>Experiment key</th>
                <th>Request</th>
                <th>Allocation</th>
                <th>Groups</th>
                {showCreator && <th>Created by</th>}
                <th>Downloads</th>
                {canExport && <th>Actions</th>}
              </tr>
            </thead>
            <tbody>
              {runs.items.map((run) => (
                <tr key={run.id}>
                  <td className="numbers">{formatTime(run.created_at)}</td>
                  <td>
                    <code>{run.experiment_key}</code>
                    {run.experiment_type === 'aa' && <span className="muted small"> A/A</span>}
                  </td>
                  <td>{run.nl_request}</td>
                  <td className="numbers">
                    {run.allocation.test} / {run.allocation.control}
                  </td>
                  <td className="numbers">
                    {run.counts.test} test · {run.counts.control} control
                  </td>
                  {showCreator && <td>{run.created_by.email}</td>}
                  <td className="numbers">{run.download_count}</td>
                  {canExport && (
                    <td>
                      <button type="button" className="quiet" disabled={busy} onClick={() => start(run)}>
                        Download again
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {runs && runs.total > runs.items.length && (
        <p className="muted small">
          Showing the newest {runs.items.length} of {runs.total} runs.
        </p>
      )}
    </section>
  )
}
