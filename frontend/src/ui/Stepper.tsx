import clsx from 'clsx'

export interface Step {
  id: string
  label: string
  /** Short state summary shown under the label, e.g. the scraped host. */
  detail?: string
  status: 'done' | 'current' | 'upcoming'
  /** Provided only for steps the user may return to. */
  onClick?: () => void
}

/** Horizontal progress across Brand → Source → Pages. Purely derived from the
 * wizard state in App — it holds no state of its own. */
export function Stepper({ steps, className }: { steps: Step[]; className?: string }) {
  return (
    <ol className={clsx('flex items-stretch gap-2', className)}>
      {steps.map((step, index) => {
        const interactive = Boolean(step.onClick)
        const Tag = interactive ? 'button' : 'div'
        return (
          <li key={step.id} className="min-w-0 flex-1">
            <Tag
              {...(interactive ? { type: 'button' as const, onClick: step.onClick } : {})}
              aria-current={step.status === 'current' ? 'step' : undefined}
              className={clsx(
                'flex w-full items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left transition',
                step.status === 'current' && 'border-brand-200 bg-brand-50',
                step.status === 'done' && 'border-line bg-surface',
                step.status === 'upcoming' && 'border-dashed border-line bg-transparent',
                interactive && 'hover:border-line-strong',
              )}
            >
              <span
                className={clsx(
                  'inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold',
                  step.status === 'current' && 'bg-brand-600 text-white',
                  step.status === 'done' && 'bg-emerald-600 text-white',
                  step.status === 'upcoming' && 'bg-surface-sunken text-ink-faint',
                )}
              >
                {step.status === 'done' ? (
                  <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true">
                    <path d="M3.5 8.5l3 3 6-7" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : (
                  index + 1
                )}
              </span>
              <span className="min-w-0">
                <span
                  className={clsx(
                    'block truncate text-sm font-semibold',
                    step.status === 'upcoming' ? 'text-ink-faint' : 'text-ink',
                  )}
                >
                  {step.label}
                </span>
                {step.detail && (
                  <span className="block truncate text-[11px] text-ink-muted">{step.detail}</span>
                )}
              </span>
            </Tag>
          </li>
        )
      })}
    </ol>
  )
}
