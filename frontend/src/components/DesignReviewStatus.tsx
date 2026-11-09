import type { GeneratedPage } from '@/lib/types'
import type { DesignReviewState } from '@/lib/useDesignReview'
import { Button, Spinner } from '@/ui'

export function DesignReviewStatus({
  review, pages, showOriginal, onCompare, onStop, onNavigate,
}: {
  review: DesignReviewState
  pages: GeneratedPage[]
  showOriginal: boolean
  onCompare: () => void
  onStop: () => void
  onNavigate: (slug: string) => void
}) {
  const running = review.status === 'running'
  const result = review.status === 'finished' ? review.result : null
  const changes = result?.changes ?? []
  const suggestions = result?.suggestions ?? []
  const title = running ? 'Reviewing mobile and desktop layouts…'
    : result?.status === 'unavailable' ? 'Design review unavailable. Your preview is ready.'
    : result?.status === 'partial' ? 'Some pages or screenshots couldn’t be reviewed'
    : changes.length || suggestions.length ? 'Design review complete' : 'Design review complete · No changes suggested'

  return (
    <div className="shrink-0 border-x border-t border-line bg-surface px-3 py-1.5 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="inline-flex items-center gap-2 font-medium text-ink-soft" role="status">
          {running && <Spinner className="h-3.5 w-3.5 text-brand-600" />}
          {title}
          {!!changes.length && <span className="text-ink-muted">· {changes.length} verified {changes.length === 1 ? 'fix' : 'fixes'}</span>}
        </span>
        {running && <Button size="sm" variant="ghost" onClick={onStop}>Keep original</Button>}
        {!!changes.length && (
          <Button size="sm" variant="ghost" onClick={onCompare}>
            {showOriginal ? 'Use reviewed design' : 'Use original design'}
          </Button>
        )}
      </div>
      {(!!changes.length || !!suggestions.length) && (
        <details className="py-1 text-ink-muted">
          <summary className="cursor-pointer font-medium">
            {showOriginal ? 'Original design selected' : 'Review details'}
            {!!suggestions.length && ` · ${suggestions.length} ${suggestions.length === 1 ? 'suggestion' : 'suggestions'}`}
          </summary>
          <ul className="max-h-32 space-y-1 overflow-y-auto py-1">
            {[...changes.map((note) => ({ ...note, fixed: true })), ...suggestions.map((note) => ({ ...note, fixed: false }))].map((note, index) => (
              <li key={`${note.slug}:${note.fixed}:${index}`}>
                <button className="font-medium text-brand-700 underline" onClick={() => onNavigate(note.slug)}>
                  {pages.find((page) => page.slug === note.slug)?.title || 'Page'}
                </button>
                {' · '}{note.fixed ? (showOriginal ? 'Verified fix' : 'Applied') : 'Suggestion'} · {note.message}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
