import clsx from 'clsx'
import type { ReactNode } from 'react'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  /** Announced to screen readers when `label` is an icon. */
  title?: string
}

export interface SegmentedProps<T extends string> {
  value: T
  onChange: (value: T) => void
  options: ReadonlyArray<SegmentedOption<T>>
  size?: 'sm' | 'md'
  ariaLabel: string
  className?: string
}

/** The pill switcher used for viewport / mode / destination choices. */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  size = 'md',
  ariaLabel,
  className,
}: SegmentedProps<T>) {
  return (
    // radiogroup, not tablist: these switch a setting, they don't reveal
    // tabpanels — and a tablist with no panels reads as broken to a screen reader.
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={clsx('inline-flex gap-0.5 rounded-xl bg-surface-sunken p-1', className)}
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={option.title}
            onClick={() => onChange(option.value)}
            className={clsx(
              'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition',
              size === 'sm' ? 'h-7 px-2.5 text-xs' : 'h-8 px-3 text-sm',
              active
                ? 'bg-surface text-ink shadow-card'
                : 'text-ink-muted hover:text-ink',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
