import { pagePath } from '@/lib/previewNav'
import type { CmsPushReport, GeneratedSite } from '@/lib/types'
import { Banner, Button, Card, SectionLabel } from '@/ui'

interface PushReportViewProps {
  report: CmsPushReport
  site: GeneratedSite
  /** Whether the push updated a site or created one — only the wording differs. */
  mode: 'update' | 'create'
}

/**
 * The per-step outcome of a push, rendered as the backend reported it. The push
 * never throws: every step is in `report.steps` with ok / warning / error, and
 * a failed push still lists everything that landed before the failure.
 */
export function PushReportView({ report, site, mode }: PushReportViewProps) {
  // The backend hands the generated token back on the create_entity step — this
  // is the only place the user can ever learn it.
  const createdToken = report.steps.find(
    (step) => step.name === 'create_entity' && step.ok,
  )?.data?.entity_token
  const pushedSlugs = Object.values(report.page_urls ?? {})

  return (
    <div className="space-y-3">
      {report.success && (
        <Banner tone="success" title={mode === 'update' ? 'Site updated' : 'Push complete'}>
          <p>
            {pushedSlugs.length} page{pushedSlugs.length === 1 ? '' : 's'} written to the
            builder{report.admin_url ? '.' : ' — open the admin suite to review them.'}
          </p>
          {report.admin_url && (
            <a
              href={report.admin_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2.5 inline-flex h-9 items-center gap-2 rounded-xl bg-emerald-700 px-3.5 text-sm font-semibold text-white transition hover:bg-emerald-800"
            >
              Open in webtree admin
              <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                <path d="M6 3h7v7M13 3L6.5 9.5" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M11 11.5V13H3V5h1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </a>
          )}
        </Banner>
      )}

      {typeof createdToken === 'string' && createdToken.length > 0 && (
        <Card title="Your new site's entity token" padding="sm">
          <p className="text-xs text-ink-muted">
            The site now appears in your site list, so you rarely need this — but it
            is what identifies the site to any other tool.
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded-lg bg-surface-sunken px-2.5 py-2 font-mono text-xs text-ink">
              {createdToken}
            </code>
            <Button
              size="sm"
              onClick={() => navigator.clipboard?.writeText(createdToken)}
            >
              Copy
            </Button>
          </div>
        </Card>
      )}

      <Card title="Push report" padding="sm">
        <ul className="space-y-1.5">
          {report.steps.map((step, i) => (
            <li
              key={i}
              className={
                'flex items-start gap-2 rounded-lg border p-2 text-xs ' +
                (!step.ok
                  ? 'border-rose-200 bg-rose-50'
                  : step.warning
                    ? 'border-amber-200 bg-amber-50'
                    : 'border-emerald-200 bg-emerald-50')
              }
            >
              <span
                className={
                  'mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white ' +
                  (!step.ok ? 'bg-rose-600' : step.warning ? 'bg-amber-600' : 'bg-emerald-600')
                }
              >
                {step.ok ? (step.warning ? '!' : '✓') : '!'}
              </span>
              <div className="min-w-0 flex-1">
                <div className="font-medium text-ink">{step.name}</div>
                <div className="text-ink-muted">
                  {step.ok ? step.detail || 'OK' : step.error || step.detail || 'Failed'}
                </div>
                {/* Succeeded, but not as asked — the push carried on and there is
                    something left to fix in the CMS. */}
                {step.warning && (
                  <div className="mt-1 font-medium text-amber-800">{step.warning}</div>
                )}
              </div>
            </li>
          ))}
        </ul>

        {pushedSlugs.length > 0 && (
          <div className="mt-3">
            <SectionLabel>Pages written</SectionLabel>
            {/* page_urls maps pageId → slug, not a URL — so render paths, not links. */}
            <ul className="mt-1.5 flex flex-wrap gap-1.5">
              {site.pages.map((page) => (
                <li
                  key={page.slug || 'home'}
                  className="rounded-md bg-surface-sunken px-2 py-1 font-mono text-[11px] text-ink-soft"
                >
                  {pagePath(page)}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>
    </div>
  )
}
