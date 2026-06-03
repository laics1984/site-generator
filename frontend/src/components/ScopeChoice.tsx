import type { SitemapProbeResult } from '@/lib/types'

interface ScopeChoiceProps {
  url: string
  probe: SitemapProbeResult
  /** Default quick-scan page cap (typically 20). */
  quickCap: number
  /** User picked Quick — proceed with `quickCap` pages. */
  onQuick: () => void
  /** User picked Full — proceed with the higher cap. */
  onFull: () => void
  onCancel: () => void
}

/**
 * Shown after the sitemap probe reveals a site with significantly more pages
 * than our default cap. The user makes ONE decision, with the real number in
 * front of them, before we pay any Playwright cost.
 *
 * If the probe finds no sitemap, the App skips this entirely and uses the
 * default cap silently. Crawl-more is offered inline in ScrapePreview after.
 */
export function ScopeChoice({
  url,
  probe,
  quickCap,
  onQuick,
  onFull,
  onCancel,
}: ScopeChoiceProps) {
  const total = probe.total_urls
  // Cap the "full" scope at our ceiling (40) to avoid unbounded crawls. If the
  // sitemap shows more than that, the user can still keep going via the inline
  // "Crawl more" banner after the first pass completes.
  const fullCap = Math.min(total, 40)
  const fullTimeEst = `~${Math.max(1, Math.round((fullCap * 4) / 60))}–${Math.round((fullCap * 6) / 60)} min`
  const quickTimeEst = `~${Math.max(1, Math.round((quickCap * 3) / 60))}–${Math.round((quickCap * 5) / 60)} min`

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900">
        <div className="font-semibold">Sitemap found</div>
        <div className="mt-1 truncate">
          {url}
          <span className="ml-2 inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-800">
            {total.toLocaleString()} pages
          </span>
        </div>
      </div>

      <div>
        <div className="text-sm font-medium text-slate-700">
          Choose crawl scope
        </div>
        <p className="mt-0.5 text-xs text-slate-500">
          You can crawl more later — pick what's enough to get started.
        </p>

        <div className="mt-3 space-y-2">
          <button
            type="button"
            onClick={onQuick}
            className="block w-full rounded-xl border border-slate-200 bg-white p-4 text-left transition hover:border-blue-300 hover:bg-blue-50"
          >
            <div className="flex items-baseline justify-between">
              <div className="text-sm font-semibold text-slate-900">
                Quick scan
              </div>
              <div className="text-xs text-slate-500">{quickTimeEst}</div>
            </div>
            <div className="mt-1 text-xs text-slate-600">
              Top <strong>{quickCap}</strong> most-linked pages. Good for a fast
              first pass — you can crawl more after the preview.
            </div>
          </button>

          {total > quickCap && (
            <button
              type="button"
              onClick={onFull}
              className="block w-full rounded-xl border border-slate-200 bg-white p-4 text-left transition hover:border-blue-300 hover:bg-blue-50"
            >
              <div className="flex items-baseline justify-between">
                <div className="text-sm font-semibold text-slate-900">
                  Full crawl
                  {total > 40 && (
                    <span className="ml-2 text-xs font-normal text-slate-500">
                      (capped at 40, rest available after)
                    </span>
                  )}
                </div>
                <div className="text-xs text-slate-500">{fullTimeEst}</div>
              </div>
              <div className="mt-1 text-xs text-slate-600">
                Crawl <strong>{fullCap}</strong> pages now. Use this if you want
                broad coverage upfront — sub-pages, case studies, blog posts.
              </div>
            </button>
          )}
        </div>
      </div>

      <button
        type="button"
        onClick={onCancel}
        className="text-xs text-slate-500 underline hover:text-slate-900"
      >
        Cancel
      </button>
    </div>
  )
}
