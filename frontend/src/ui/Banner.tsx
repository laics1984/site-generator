import clsx from 'clsx'
import type { ReactNode } from 'react'

export type BannerTone = 'info' | 'success' | 'warn' | 'danger'

const TONES: Record<BannerTone, { box: string; title: string }> = {
  info: { box: 'border-brand-200 bg-brand-50 text-brand-900', title: 'text-brand-900' },
  success: { box: 'border-emerald-200 bg-emerald-50 text-emerald-900', title: 'text-emerald-900' },
  warn: { box: 'border-amber-200 bg-amber-50 text-amber-900', title: 'text-amber-900' },
  danger: { box: 'border-rose-200 bg-rose-50 text-rose-900', title: 'text-rose-900' },
}

export interface BannerProps {
  tone?: BannerTone
  title?: ReactNode
  actions?: ReactNode
  className?: string
  children?: ReactNode
}

export function Banner({ tone = 'info', title, actions, className, children }: BannerProps) {
  return (
    <div
      role={tone === 'danger' ? 'alert' : 'status'}
      className={clsx('rounded-xl border p-3 text-sm', TONES[tone].box, className)}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {title && <div className={clsx('font-semibold', TONES[tone].title)}>{title}</div>}
          {children && <div className={clsx(title && 'mt-1', 'text-[13px]')}>{children}</div>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
    </div>
  )
}
