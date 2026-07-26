import type { ReactNode } from 'react'

import { LlmStatus } from '@/components/LlmStatus'

/** The one persistent bar. Deliberately slim: in the preview stage every pixel
 * it takes comes out of the rendered site below it. */
export function WorkspaceHeader({ actions }: { actions?: ReactNode }) {
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-surface/85 backdrop-blur">
      <div className="flex h-14 items-center justify-between gap-4 px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-brand-600 text-[13px] font-bold text-white">
            W
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold text-ink">
              Webtree Site Generator
            </span>
            <span className="block truncate text-[11px] text-ink-faint">
              Local · AI-powered · Builder-compatible
            </span>
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {actions}
          <LlmStatus />
        </div>
      </div>
    </header>
  )
}
