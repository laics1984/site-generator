import { clsx } from 'clsx'

import type { GeneratorMode } from '@/lib/types'

interface ModeTabsProps {
  mode: GeneratorMode
  onChange: (mode: GeneratorMode) => void
}

const TABS: { id: GeneratorMode; label: string; description: string }[] = [
  {
    id: 'url',
    label: 'Scrape a URL',
    description: 'Pull content from an existing website and rebuild it.',
  },
  {
    id: 'document',
    label: 'Upload a document',
    description: 'Generate the site from a PDF or Word doc — its titles become the pages.',
  },
]

export function ModeTabs({ mode, onChange }: ModeTabsProps) {
  return (
    <div role="radiogroup" aria-label="Content source" className="grid gap-3 sm:grid-cols-2">
      {TABS.map((tab) => {
        const active = tab.id === mode
        return (
          <button
            key={tab.id}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(tab.id)}
            className={clsx(
              'rounded-2xl border p-4 text-left transition',
              active
                ? 'border-brand-500 bg-brand-50/50 shadow-card'
                : 'border-line bg-surface shadow-card hover:border-line-strong',
            )}
          >
            <span
              className={clsx(
                'block text-sm font-semibold',
                active ? 'text-brand-700' : 'text-ink',
              )}
            >
              {tab.label}
            </span>
            <span className="mt-1 block text-xs text-ink-muted">{tab.description}</span>
          </button>
        )
      })}
    </div>
  )
}
