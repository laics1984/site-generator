import { clsx } from 'clsx'

import { pagePath } from '@/lib/previewNav'
import type { GeneratedPage } from '@/lib/types'

interface PageListProps {
  pages: GeneratedPage[]
  selectedSlug: string | null
  onSelect: (slug: string) => void
}

export function PageList({ pages, selectedSlug, onSelect }: PageListProps) {
  return (
    <ul className="space-y-0.5">
      {pages.map((page) => {
        const active = page.slug === selectedSlug
        // parent_slug is set on sub-pages; indenting them keeps the rail
        // readable once a crawl produces /services/design-style nesting.
        const nested = Boolean(page.parent_slug)
        return (
          <li key={page.slug || 'home'}>
            <button
              type="button"
              onClick={() => onSelect(page.slug)}
              aria-current={active ? 'page' : undefined}
              className={clsx(
                'flex w-full items-center gap-2 rounded-lg py-2 pr-2.5 text-left transition',
                nested ? 'pl-6' : 'pl-2.5',
                active
                  ? 'bg-brand-50 text-brand-700'
                  : 'text-ink-soft hover:bg-surface-sunken hover:text-ink',
              )}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{page.title}</span>
                <span
                  className={clsx(
                    'block truncate font-mono text-[11px]',
                    active ? 'text-brand-600' : 'text-ink-faint',
                  )}
                >
                  {pagePath(page)}
                </span>
              </span>
              {page.is_homepage && (
                <span className="shrink-0 rounded-full bg-surface-sunken px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-ink-muted">
                  home
                </span>
              )}
            </button>
          </li>
        )
      })}
    </ul>
  )
}
