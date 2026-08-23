import type { ReactNode } from 'react'

import { looksLikeHtml } from '@/lib/sourcePaste'
import { Textarea } from '@/ui'

interface PasteBoxProps {
  value: string
  onChange: (value: string) => void
  label: string
  hint?: ReactNode
  placeholder?: string
  rows?: number
  disabled?: boolean
  /** Submit on ⌘/Ctrl+Enter, the way every other paste-and-go box works. */
  onSubmit?: () => void
}

/**
 * The paste control: one textarea that takes copy or markup.
 *
 * Used in all three source modes — on its own it IS the source, and alongside a
 * link or a document it is merged into what that reader found. Both cases go
 * through the same backend endpoint, so this component only has to be a good
 * textarea: it reports what it thinks you pasted, and how much of it there is.
 */
export function PasteBox({
  value,
  onChange,
  label,
  hint,
  placeholder,
  rows = 10,
  disabled,
  onSubmit,
}: PasteBoxProps) {
  const isHtml = looksLikeHtml(value)
  return (
    <div>
      <label className="block">
        <span className="text-xs font-semibold text-ink-soft">{label}</span>
        <Textarea
          rows={rows}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (onSubmit && e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              onSubmit()
            }
          }}
          disabled={disabled}
          spellCheck={!isHtml}
          placeholder={placeholder}
          className={`mt-1.5 ${isHtml ? 'font-mono text-xs' : ''}`}
        />
      </label>
      <div className="mt-1.5 flex items-baseline justify-end gap-2 text-[11px] text-ink-faint">
        {isHtml && (
          <span
            className="rounded-md bg-brand-50 px-1.5 py-0.5 font-semibold text-brand-700"
            aria-live="polite"
          >
            HTML detected
          </span>
        )}
        {value.length > 0 && <span>{value.length.toLocaleString()} characters</span>}
      </div>
      {hint && <p className="mt-1 text-xs text-ink-muted">{hint}</p>}
    </div>
  )
}
