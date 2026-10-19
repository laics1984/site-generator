import type { CoverageItem, CoverageKind, SourceCoverage } from '@/lib/types'

/**
 * What made it onto the site: every fact, photo, review and offering the read
 * found, counted against the finished site (backend `source_coverage`).
 *
 * Advisory. It never blocks a publish — a gap here is something the operator
 * may want to fix with the page picker or the paste box, not an error.
 */

const LABELS: Record<CoverageKind, string> = {
  fact: 'Facts',
  offering: 'Offerings',
  photo: 'Photos',
  review: 'Recommendations',
}
const ORDER: CoverageKind[] = ['fact', 'offering', 'photo', 'review']

function missingLabel(item: CoverageItem): string {
  if (item.kind === 'photo') {
    try {
      return new URL(item.label).pathname.split('/').pop() || item.label
    } catch {
      return item.label
    }
  }
  return item.label
}

export function CoveragePanel({ coverage }: { coverage: SourceCoverage }) {
  const kinds = ORDER.filter((kind) => coverage.summary[kind]?.read)
  if (!kinds.length) return null
  const missing = coverage.items.filter((item) => !item.placed)

  return (
    <details className="rounded-xl border border-line bg-surface p-3 text-xs">
      <summary className="cursor-pointer select-none">
        <span className="font-semibold text-ink-soft">What made it onto the site</span>
        <span className="ml-2 text-ink-muted">
          {kinds
            .map((kind) => `${LABELS[kind]} ${coverage.summary[kind]!.placed}/${coverage.summary[kind]!.read}`)
            .join(' · ')}
        </span>
      </summary>
      {missing.length > 0 ? (
        <ul className="mt-2 space-y-1">
          {missing.map((item, index) => (
            <li key={index} className="flex items-baseline gap-1.5">
              <span aria-hidden className="text-ink-faint">—</span>
              <span className="font-medium text-ink">{LABELS[item.kind]}:</span>
              <span className="truncate text-ink-muted">{missingLabel(item)}</span>
              {item.reason && <span className="shrink-0 text-ink-faint">({item.reason})</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-ink-muted">Everything the source stated is on the site.</p>
      )}
    </details>
  )
}
