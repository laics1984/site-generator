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
    <div className="grid gap-3 sm:grid-cols-2">
      {TABS.map((tab) => {
        const active = tab.id === mode
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onChange(tab.id)}
            className={clsx(
              'rounded-2xl border p-5 text-left transition',
              active
                ? 'border-blue-600 bg-white shadow-sm ring-2 ring-blue-100'
                : 'border-slate-200 bg-white hover:border-slate-300',
            )}
          >
            <div className="text-base font-semibold text-slate-900">{tab.label}</div>
            <div className="mt-1 text-sm text-slate-600">{tab.description}</div>
          </button>
        )
      })}
    </div>
  )
}
