import clsx from 'clsx'

import type { CmsPageAction, CmsPageChange, CmsSyncPlan } from '@/lib/types'
import { Banner, Spinner } from '@/ui'

interface PushPlanCardProps {
  plan: CmsSyncPlan | null
  busy: boolean
  error: string | null
  /** Whether the push publishes — decides what happens to the pages the new
   * site lacks (archived now, or left live until the operator publishes). */
  publish: boolean
  /** Whether the push resets the site's templates to the new design. */
  replaceTemplates: boolean
}

/** Everything an update leaves exactly as the site's owner has it. Stated
 * here, next to the changes, because "what stays" is the half of an update
 * the operator worries about. */
const KEPT =
  'articles, events, categories, tags, contacts, subscribers, insights and WhatsApp settings'

/** What each templateFor value is to the person reading the plan. */
const TEMPLATE_LABEL: Record<string, string> = {
  article: 'article page',
  articleListing: 'article listing',
  event: 'event page',
  eventListing: 'event listing',
}

const TONE: Record<CmsPageAction, string> = {
  update: 'bg-brand-50 text-brand-900 ring-brand-200',
  create: 'bg-emerald-50 text-emerald-900 ring-emerald-200',
  archive: 'bg-amber-50 text-amber-900 ring-amber-200',
}

/**
 * What pushing into the chosen site will do, as the backend planned it
 * (POST /api/cms/plan). Rendered before the push so the operator confirms a
 * concrete list — "3 updated, 1 added, 1 archived" and which — rather than a
 * mode name. The same plan runs on push; this card does no planning of its own.
 */
export function PushPlanCard({
  plan,
  busy,
  error,
  publish,
  replaceTemplates,
}: PushPlanCardProps) {
  if (busy) {
    return (
      <div className="flex items-center gap-2 text-xs text-ink-muted" role="status">
        <Spinner className="h-3.5 w-3.5" />
        Comparing with the site's pages…
      </div>
    )
  }
  if (error) {
    return (
      <Banner tone="danger" title="Couldn't compare with the site's pages">
        {error}
      </Banner>
    )
  }
  if (!plan) return null

  const updates = plan.changes.filter((c) => c.action === 'update')
  const creates = plan.changes.filter((c) => c.action === 'create')
  const archives = plan.changes.filter((c) => c.action === 'archive')
  const templates = plan.template_pages.length
  const resetsTemplates = replaceTemplates && templates > 0

  return (
    <div className="space-y-3 rounded-xl border border-line bg-surface-sunken/60 p-3">
      {plan.first_push ? (
        <p className="text-xs text-ink-soft">
          This site has no pages yet, so this is a first push: every page, the theme, and
          any migrated articles, events and WhatsApp button all go in.
        </p>
      ) : (
        <div className="flex flex-wrap gap-1.5" aria-label="Summary of changes">
          <CountChip action="update" n={updates.length} label="updated in place" />
          <CountChip action="create" n={creates.length} label="added" />
          <CountChip
            action="archive"
            n={archives.length}
            label={publish ? 'archived' : 'left live until you publish'}
          />
        </div>
      )}

      <ChangeList title="Updated in place" changes={updates} action="update" />
      <ChangeList title="Added" changes={creates} action="create" />
      <ChangeList
        title={publish ? 'Archived (restorable in the admin)' : 'Not in the new site'}
        changes={archives}
        action="archive"
        note={
          publish
            ? undefined
            : 'Pushed as drafts, so these stay live. Archive them in the admin once you publish.'
        }
      />

      {plan.template_routes.length > 0 && (
        <p className="text-[11px] leading-relaxed text-ink-muted">
          Left out: {plan.template_routes.map((slug) => `/${slug}`).join(', ')} — a template
          page already renders {plan.template_routes.length === 1 ? 'that URL' : 'those URLs'}.
        </p>
      )}

      {resetsTemplates && (
        <p className="text-[11px] leading-relaxed text-amber-900">
          Reset to the new design as drafts:{' '}
          {plan.template_pages.map((kind) => TEMPLATE_LABEL[kind] ?? kind).join(', ')}.
          Open each in the builder and publish it to take it live.
        </p>
      )}

      <p className="text-[11px] leading-relaxed text-ink-muted">
        Kept as they are: {KEPT}
        {templates > 0 && !resetsTemplates && (
          <>
            , plus the {templates} article/event template page{templates === 1 ? '' : 's'}
          </>
        )}
        .
      </p>
    </div>
  )
}

function CountChip({
  action,
  n,
  label,
}: {
  action: CmsPageAction
  n: number
  label: string
}) {
  if (n === 0) return null
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset',
        TONE[action],
      )}
    >
      <span className="font-semibold">{n}</span> {label}
    </span>
  )
}

function ChangeList({
  title,
  changes,
  action,
  note,
}: {
  title: string
  changes: CmsPageChange[]
  action: CmsPageAction
  note?: string
}) {
  if (changes.length === 0) return null
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
        {title}
      </div>
      {note && <p className="mt-0.5 text-[11px] text-ink-muted">{note}</p>}
      <ul className="mt-1.5 flex flex-wrap gap-1.5">
        {changes.map((change) => (
          <li
            key={change.slug || '/'}
            title={change.title}
            className={clsx(
              'rounded-md px-2 py-1 font-mono text-[11px] ring-1 ring-inset',
              TONE[action],
            )}
          >
            /{change.slug}
            {change.restore && (
              <span className="ml-1 font-sans font-normal opacity-70">restored</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
