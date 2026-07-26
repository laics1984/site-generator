import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

export interface DrawerProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  description?: ReactNode
  /** Tailwind max-width class for the panel. */
  widthClassName?: string
  children: ReactNode
}

/**
 * Right-side slide-over. Used for flows that need room but shouldn't take the
 * user away from the preview — currently "Publish to webtree".
 *
 * Deliberately not a <dialog>: Safari still lacks a usable animated open/close
 * for it, and we need the preview iframe behind to stay mounted (re-mounting it
 * would re-run the whole portal + layout fetch).
 */
export function Drawer({
  open,
  onClose,
  title,
  description,
  widthClassName = 'max-w-lg',
  children,
}: DrawerProps) {
  const panelRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    // Move focus in so Escape and Tab act on the drawer, not the page behind.
    panelRef.current?.focus()
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0 animate-fade-in bg-ink/25 backdrop-blur-[1px]"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        tabIndex={-1}
        className={`relative flex h-full w-full ${widthClassName} animate-slide-in-right flex-col bg-canvas shadow-pop outline-none`}
      >
        <header className="flex items-start justify-between gap-3 border-b border-line bg-surface px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-ink">{title}</h2>
            {description && <p className="mt-0.5 text-xs text-ink-muted">{description}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-mr-1 -mt-1 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
          >
            <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
            </svg>
          </button>
        </header>
        <div className="scrollbar-slim min-h-0 flex-1 overflow-y-auto px-5 py-5">{children}</div>
      </div>
    </div>
  )
}
