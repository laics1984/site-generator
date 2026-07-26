import clsx from 'clsx'
import type { InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from 'react'

const CONTROL =
  'block w-full rounded-xl border border-line bg-surface px-3.5 py-2.5 text-sm text-ink shadow-card transition ' +
  'placeholder:text-ink-faint hover:border-line-strong focus:border-brand-500 disabled:bg-surface-sunken disabled:text-ink-muted'

export interface FieldProps {
  label: ReactNode
  /** Rendered under the control — guidance, not errors. */
  hint?: ReactNode
  /** Rendered under the control in red and wired to aria-invalid by the caller. */
  error?: ReactNode
  optional?: boolean
  className?: string
  children: ReactNode
}

/** Label + control + hint. Wraps the control in a <label> so the whole row is a
 * hit target, which is why children are elements rather than a render prop. */
export function Field({ label, hint, error, optional, className, children }: FieldProps) {
  return (
    <label className={clsx('block', className)}>
      <span className="flex items-baseline gap-1.5 text-xs font-semibold text-ink-soft">
        {label}
        {optional && <span className="font-normal text-ink-faint">optional</span>}
      </span>
      <div className="mt-1.5">{children}</div>
      {error ? (
        <span className="mt-1 block text-xs text-rose-700">{error}</span>
      ) : hint ? (
        <span className="mt-1 block text-xs text-ink-muted">{hint}</span>
      ) : null}
    </label>
  )
}

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={clsx(CONTROL, className)} />
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...rest} className={clsx(CONTROL, 'resize-y', className)} />
}

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label: ReactNode
  description?: ReactNode
}

export function Checkbox({ label, description, className, ...rest }: CheckboxProps) {
  return (
    <label
      className={clsx(
        'flex cursor-pointer items-start gap-2.5 text-sm text-ink-soft',
        rest.disabled && 'cursor-not-allowed opacity-60',
        className,
      )}
    >
      <input
        {...rest}
        type="checkbox"
        className="mt-0.5 h-4 w-4 shrink-0 rounded border-line-strong text-brand-600"
      />
      <span className="min-w-0">
        <span className="block font-medium text-ink">{label}</span>
        {description && <span className="mt-0.5 block text-xs text-ink-muted">{description}</span>}
      </span>
    </label>
  )
}
