import { useEffect, useState } from 'react'

import type { CrawlJob } from '@/lib/types'
import { Button, Spinner } from '@/ui'

interface CrawlProgressProps {
  job: CrawlJob
  /** Caller-provided cancel handler. We only show the Cancel button while running. */
  onCancel: () => void
  /** Optional cap from the user's scope choice — drives the bar. */
  pagesCap?: number
}

/**
 * Live progress card shown while a crawl job is queued/running.
 *
 * Polling is the caller's responsibility (App.tsx). This component is pure
 * presentation: it renders whatever the latest CrawlJob snapshot says.
 */
export function CrawlProgress({ job, onCancel, pagesCap }: CrawlProgressProps) {
  const [clientElapsed, setClientElapsed] = useState(0)

  // Local seconds counter so the "Elapsed" line updates every second between
  // polls. Reset when the job's started_at changes.
  useEffect(() => {
    if (!job.started_at) return
    const tick = () => setClientElapsed(Date.now() / 1000 - (job.started_at ?? 0))
    tick()
    const interval = setInterval(tick, 1000)
    return () => clearInterval(interval)
  }, [job.started_at])

  const pagesDone = job.progress?.pages_done ?? 0
  const pct = pagesCap && pagesCap > 0 ? Math.min(100, Math.round((pagesDone / pagesCap) * 100)) : null

  const isQueued = job.status === 'queued'
  const isRunning = job.status === 'running'
  const elapsed = job.elapsed_seconds ?? clientElapsed

  return (
    <div className="space-y-3">
      <div
        className="rounded-2xl border border-brand-200 bg-brand-50 p-4"
        role="status"
        aria-live="polite"
      >
        <div className="flex items-center gap-3">
          <Spinner className="h-4 w-4 text-brand-600" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-ink">
              {isQueued ? 'Queued…' : 'Crawling…'}
            </div>
            <div className="truncate text-xs text-ink-muted">{job.entry_url}</div>
          </div>
        </div>

        <div className="mt-3">
          <div className="flex items-baseline justify-between text-xs">
            <div className="text-ink-soft">
              Pages fetched: <span className="font-semibold">{pagesDone}</span>
              {pagesCap ? <span className="text-ink-muted"> / {pagesCap}</span> : null}
            </div>
            <div className="text-ink-muted">
              {elapsed > 0 ? `${elapsed.toFixed(0)}s elapsed` : ''}
            </div>
          </div>
          <div
            className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-brand-100"
            role="progressbar"
            aria-valuenow={pct ?? undefined}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full bg-brand-500 transition-all duration-300 ease-out"
              style={{ width: `${pct ?? (isRunning ? 8 : 0)}%` }}
            />
          </div>
        </div>

        {job.progress?.current_url && (
          <div className="mt-2 truncate text-[11px] text-ink-muted">
            <span className="font-medium text-ink-soft">Now:</span>{' '}
            <span className="font-mono">{job.progress.current_url}</span>
          </div>
        )}
      </div>

      <Button variant="ghost" onClick={onCancel} disabled={!isRunning} className="text-rose-700 hover:bg-rose-50 hover:text-rose-800">
        Cancel crawl
      </Button>
    </div>
  )
}
