import { useEffect, useState } from 'react'

import type { CrawlJob } from '@/lib/types'

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
      <div className="rounded-2xl border border-blue-200 bg-blue-50 p-4">
        <div className="flex items-center gap-3">
          <Spinner size={18} />
          <div className="flex-1">
            <div className="text-sm font-semibold text-slate-900">
              {isQueued ? 'Queued…' : 'Crawling…'}
            </div>
            <div className="truncate text-xs text-slate-600">
              {job.entry_url}
            </div>
          </div>
        </div>

        <div className="mt-3">
          <div className="flex items-baseline justify-between text-xs">
            <div className="text-slate-700">
              Pages fetched:{' '}
              <span className="font-semibold">{pagesDone}</span>
              {pagesCap ? <span className="text-slate-500"> / {pagesCap}</span> : null}
            </div>
            <div className="text-slate-500">
              {elapsed > 0 ? `${elapsed.toFixed(0)}s elapsed` : ''}
            </div>
          </div>
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-blue-100">
            <div
              className="h-full bg-blue-500 transition-all duration-300 ease-out"
              style={{ width: `${pct ?? (isRunning ? 8 : 0)}%` }}
            />
          </div>
        </div>

        {job.progress?.current_url && (
          <div className="mt-2 truncate text-[11px] text-slate-500">
            <span className="font-medium text-slate-600">Now:</span>{' '}
            <span className="font-mono">{job.progress.current_url}</span>
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={onCancel}
        disabled={!isRunning}
        className="rounded-xl border border-rose-200 bg-white px-4 py-2 text-sm font-medium text-rose-700 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-60"
      >
        Cancel crawl
      </button>
    </div>
  )
}

function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg
      className="animate-spin text-blue-600"
      style={{ width: size, height: size }}
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
      <path d="M4 12a8 8 0 018-8" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}
