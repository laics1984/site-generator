import { clsx } from 'clsx'

import type { GeneratedPage } from '@/lib/types'

interface PageListProps {
  pages: GeneratedPage[]
  selectedSlug: string | null
  onSelect: (slug: string) => void
}

export function PageList({ pages, selectedSlug, onSelect }: PageListProps) {
  return (
    <ul className="space-y-1">
      {pages.map((page) => {
        const active = page.slug === selectedSlug
        return (
          <li key={page.slug || 'home'}>
            <button
              type="button"
              onClick={() => onSelect(page.slug)}
              className={clsx(
                'w-full rounded-lg px-3 py-2 text-left text-sm transition',
                active
                  ? 'bg-blue-50 text-blue-900'
                  : 'text-slate-700 hover:bg-slate-100',
              )}
            >
              <div className="font-medium">
                {page.title}
                {page.is_homepage && (
                  <span className="ml-2 rounded-full bg-blue-600 px-2 py-0.5 text-xs font-medium text-white">
                    home
                  </span>
                )}
              </div>
              <div className="mt-0.5 text-xs text-slate-500">
                /{page.slug || ''}
              </div>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
