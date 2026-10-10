import { useState, type KeyboardEvent } from 'react'
import type { Me } from '../api/types.ts'
import { BusinessContextView } from './BusinessContextView.tsx'
import { CrawlerView } from './CrawlerView.tsx'
import { DocsView } from './DocsView.tsx'
import type { SemanticVersion } from './types.ts'
import { UseCaseQueue } from './UseCaseQueue.tsx'
import { useLoad } from './useLoad.ts'

type View = 'docs' | 'context' | 'use-cases' | 'crawler'

const VIEWS: { id: View; label: string }[] = [
  { id: 'docs', label: 'Generated docs' },
  { id: 'context', label: 'Business context' },
  { id: 'use-cases', label: 'Use cases' },
  { id: 'crawler', label: 'Crawler' },
]

/**
 * Admin "Semantic context" tab (FR-3, FR-A6). Needs semantic_context.read to be shown at all;
 * edit, review and crawl controls appear only with their permissions. Hiding is UX only:
 * the API enforces every permission itself.
 */
export function SemanticTab({ me }: { me: Me }) {
  const [view, setView] = useState<View>('docs')
  const version = useLoad<SemanticVersion>('/api/semantic/version')
  const can = (permission: string) => me.permissions.includes(permission)

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
    if (step === 0) return
    event.preventDefault()
    const next = VIEWS[(index + step + VIEWS.length) % VIEWS.length]
    if (next) {
      setView(next.id)
      document.getElementById(`semantic-tab-${next.id}`)?.focus()
    }
  }

  return (
    <section className="panel" aria-labelledby="semantic-title">
      <div className="panel-head">
        <h2 id="semantic-title">Semantic context</h2>
        {version.data && (
          <p className="muted" title={version.data.version}>
            Semantic version {version.data.version.slice(0, 12)}
          </p>
        )}
      </div>
      <div role="tablist" aria-label="Semantic context views" className="subtabs">
        {VIEWS.map((v, index) => (
          <button
            key={v.id}
            id={`semantic-tab-${v.id}`}
            type="button"
            role="tab"
            aria-selected={view === v.id}
            aria-controls={`semantic-panel-${v.id}`}
            tabIndex={view === v.id ? 0 : -1}
            onClick={() => setView(v.id)}
            onKeyDown={(e) => onTabKey(e, index)}
          >
            {v.label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`semantic-panel-${view}`}
        aria-labelledby={`semantic-tab-${view}`}
        className="subtab-panel"
      >
        {view === 'docs' && <DocsView />}
        {view === 'context' && (
          <BusinessContextView canEdit={can('semantic_context.edit')} onChanged={version.reload} />
        )}
        {view === 'use-cases' && <UseCaseQueue canReview={can('use_case.review')} onChanged={version.reload} />}
        {view === 'crawler' && <CrawlerView canCrawl={can('crawler.run')} onChanged={version.reload} />}
      </div>
    </section>
  )
}
