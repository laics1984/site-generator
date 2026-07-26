import clsx from 'clsx'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

import { Spinner } from './Spinner'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-brand-600 text-white shadow-card hover:bg-brand-700 disabled:bg-line-strong disabled:text-white/80',
  secondary:
    'border border-line bg-surface text-ink-soft shadow-card hover:border-line-strong hover:text-ink disabled:text-ink-faint',
  ghost: 'text-ink-soft hover:bg-surface-sunken hover:text-ink disabled:text-ink-faint',
  danger: 'bg-rose-600 text-white shadow-card hover:bg-rose-700 disabled:bg-line-strong',
}

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 gap-1.5 rounded-lg px-2.5 text-xs',
  md: 'h-9 gap-2 rounded-xl px-3.5 text-sm',
  lg: 'h-11 gap-2 rounded-xl px-5 text-sm',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Shows a spinner and blocks interaction without collapsing the layout. */
  busy?: boolean
  icon?: ReactNode
  fullWidth?: boolean
}

export function Button({
  variant = 'secondary',
  size = 'md',
  busy = false,
  icon,
  fullWidth = false,
  className,
  children,
  disabled,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={clsx(
        'inline-flex select-none items-center justify-center whitespace-nowrap font-semibold transition',
        'disabled:cursor-not-allowed',
        VARIANTS[variant],
        SIZES[size],
        fullWidth && 'w-full',
        className,
      )}
    >
      {busy ? <Spinner className="h-4 w-4" /> : icon}
      {children}
    </button>
  )
}
