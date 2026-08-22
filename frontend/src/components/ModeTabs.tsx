import { clsx } from 'clsx'

import type { GeneratorMode } from '@/lib/types'

interface ModeTabsProps {
  mode: GeneratorMode
  onChange: (mode: GeneratorMode) => void
}

// Two tabs, because these are two genuinely different input affordances: you
// type a link, or you drop a file. There is deliberately no "Facebook" tab —
// a Facebook Page is a link, and which reader handles it is our problem, not
// something the user should have to classify. See lib/sourceDetect.ts.
const TABS: { id: GeneratorMode; label: string; description: string }[] = [
  {
    id: 'url',
    label: 'Paste a link',
    description: 'A website or a Facebook Page — we work out how to read it.',
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
