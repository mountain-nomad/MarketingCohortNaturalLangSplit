import { useState } from 'react'
import { api, errorMessage } from '../api/client.ts'
import type { ListOf } from '../api/types.ts'
import type { CrawlRun } from './types.ts'
import { useLoad } from './useLoad.ts'

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—'
}

/** Crawl runs and (with crawler.run) a button to crawl now. One crawl runs at a time. */
export function CrawlerView({ canCrawl, onChanged }: { canCrawl: boolean; onChanged: () => void }) {
  const { data, error, reload } = useLoad<ListOf<CrawlRun>>('/api/crawler/runs')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  async function crawl() {
    setBusy(true)
    setResult(null)
    setFailure(null)
    try {
      const run = await api<CrawlRun>('/api/crawler/runs', { method: 'POST' })
      setResult(`Crawl run ${run.id} ${run.status}.`)
      onChanged()
    } catch (err) {
      setFailure(errorMessage(err))
    } finally {
      setBusy(false)
      reload()
    }
  }

  return (
    <div className="stack">
      <p className="muted">
        The crawler reads the warehouse with the read-only role and replaces generated docs and pending
        suggestions. Business context and reviewed use cases are never overwritten.
      </p>
      {canCrawl && (
        <div className="row">
          <button type="button" disabled={busy} onClick={() => void crawl()}>
            {busy ? 'Crawling…' : 'Run crawler'}
          </button>
        </div>
      )}
      {result && (
        <p className="success" role="status">
          {result}
        </p>
      )}
      {failure && (
        <p className="error" role="alert">
          {failure}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {data && data.items.length === 0 && <p className="muted">No crawl has run yet.</p>}
      {data && data.items.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Status</th>
                <th scope="col">Started</th>
                <th scope="col">Finished</th>
                <th scope="col">Triggered by</th>
                <th scope="col">Details</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((run) => (
                <tr key={run.id}>
                  <td>#{run.id}</td>
                  <td className={`status status-${run.status}`}>{run.status}</td>
                  <td>{when(run.started_at)}</td>
                  <td>{when(run.finished_at)}</td>
                  <td>{run.triggered_by ?? '—'}</td>
                  <td className="details">
                    {run.error ?? (typeof run.summary.tables === 'number' ? `${run.summary.tables} tables` : '—')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
