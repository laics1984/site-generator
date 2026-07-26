import clsx from 'clsx'
import type { ReactNode } from 'react'

export interface CardProps {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  padding?: 'none' | 'sm' | 'md'
  className?: string
  children?: ReactNode
}

const PADDING = { none: '', sm: 'p-4', md: 'p-5' } as const

export function Card({
  title,
  description,
  actions,
  padding = 'md',
  className,
  children,
}: CardProps) {
  return (
    <section
      className={clsx('rounded-2xl border border-line bg-surface shadow-card', PADDING[padding], className)}
    >
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            {title && <h3 className="text-sm font-semibold text-ink">{title}</h3>}
            {description && <p className="mt-0.5 text-xs text-ink-muted">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      {children && <div className={clsx((title || actions) && 'mt-4')}>{children}</div>}
    </section>
  )
}

/** The small all-caps label used above groups throughout the tool. */
export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={clsx(
        'text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint',
        className,
      )}
    >
      {children}
    </div>
  )
}
