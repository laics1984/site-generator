import { useEffect, useRef, useState } from 'react'

import type { GeneratedSite } from '@/lib/types'
import { Button } from '@/ui'

interface SiteSummaryBarProps {
  site: GeneratedSite
  /** Where the content came from — a host or a filename. */
  sourceLabel?: string | null
  onRegenerate: () => void
  onAdjust: () => void
  onPublish: () => void
  onGenerateRemaining?: () => void
  onStartOver: () => void
  onExport: () => void
  exportBusy?: boolean
  busy?: boolean
  /** False when the last run came from the paste fallback and has no stored payload. */
  canRegenerate: boolean
}

const SWATCH_KEYS = ['primary', 'secondary', 'accent', 'surface'] as const

/**
 * The collapsed form. Once a site exists the wizard is worth one line — brand,
 * source, page count, theme — and the rest of the screen belongs to the
 * preview. Everything here reopens or re-runs the wizard; nothing is destructive
 * without going through the overflow menu.
 */
export function SiteSummaryBar({
  site,
  sourceLabel,
  onRegenerate,
  onAdjust,
  onPublish,
  onGenerateRemaining,
  onStartOver,
  onExport,
  exportBusy = false,
  busy = false,
  canRegenerate,
}: SiteSummaryBarProps) {
  const colors = site.builder_styles?.colors
  const logo = site.brand?.logo_data_url || site.brand?.logo_url || null

  return (
    <div className="sticky top-14 z-20 border-b border-line bg-surface/85 px-4 py-2.5 backdrop-blur sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 items-center gap-3">
          {logo ? (
            <img
              src={logo}
              alt=""
              className="h-8 w-8 shrink-0 rounded-lg border border-line bg-surface object-contain p-1"
            />
          ) : (
            <span
              className="h-8 w-8 shrink-0 rounded-lg border border-line"
              style={{ backgroundColor: colors?.primary || '#e4e7ec' }}
            />
          )}

          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-ink">{site.site_name}</div>
            <div className="flex items-center gap-1.5 truncate text-[11px] text-ink-muted">
              {sourceLabel && (
                <>
                  <span className="truncate">{sourceLabel}</span>
                  <span aria-hidden="true">·</span>
                </>
              )}
              <span>
                {site.generation_mode === 'two_page_preview'
                  ? `${site.pages.length} of ${site.planned_page_count ?? site.pages.length} planned pages`
                  : `${site.pages.length} page${site.pages.length === 1 ? '' : 's'}`}
              </span>
              {site.brand?.mood && (
                <>
                  <span aria-hidden="true">·</span>
                  <span className="capitalize">{site.brand.mood}</span>
                </>
              )}
            </div>
          </div>

          {colors && (
            <div className="hidden items-center gap-1 sm:flex" aria-hidden="true">
              {SWATCH_KEYS.map((key) => (
                <span
                  key={key}
                  title={`${key}: ${colors[key]}`}
                  className="h-4 w-4 rounded-full border border-line"
                  style={{ backgroundColor: colors[key] }}
                />
              ))}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {onGenerateRemaining && (
            <Button size="sm" variant="primary" onClick={onGenerateRemaining} busy={busy}>
              Generate remaining pages
            </Button>
          )}
          {canRegenerate && (
            <Button size="sm" onClick={onRegenerate} busy={busy}>
              Regenerate
            </Button>
          )}
          <Button size="sm" onClick={onAdjust} disabled={busy}>
            Adjust &amp; regenerate
          </Button>
          <Button size="sm" variant="primary" onClick={onPublish} disabled={busy}>
            Publish
          </Button>
          <OverflowMenu
            site={site}
            onStartOver={onStartOver}
            onExport={onExport}
            exportBusy={exportBusy}
            disabled={busy}
          />
        </div>
      </div>
    </div>
  )
}

/** Saves the generated site exactly as the preview holds it, so a defect seen
 * here can be audited offline (backend/scripts/audit_site_inks.py). */
function downloadSiteJson(site: GeneratedSite) {
  const blob = new Blob([JSON.stringify(site, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${(site.site_name || 'site').replace(/[^a-z0-9]+/gi, '-').toLowerCase()}-site.json`
  link.click()
  URL.revokeObjectURL(url)
}

function OverflowMenu({
  site,
  onStartOver,
  onExport,
  exportBusy,
  disabled,
}: {
  site: GeneratedSite
  onStartOver: () => void
  onExport: () => void
  exportBusy: boolean
  disabled: boolean
}) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div ref={wrapRef} className="relative">
      <Button
        size="sm"
        variant="ghost"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="More actions"
      >
        <svg viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor" aria-hidden="true">
          <circle cx="3" cy="8" r="1.4" />
          <circle cx="8" cy="8" r="1.4" />
          <circle cx="13" cy="8" r="1.4" />
        </svg>
      </Button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-1.5 w-60 animate-slide-up overflow-hidden rounded-xl border border-line bg-surface p-1 shadow-raised"
        >
          <button
            role="menuitem"
            type="button"
            onClick={() => {
              setOpen(false)
              onExport()
            }}
            disabled={exportBusy}
            className="block w-full rounded-lg px-3 py-2 text-left text-sm text-ink-soft transition hover:bg-surface-sunken hover:text-ink disabled:opacity-60"
          >
            {exportBusy ? 'Preparing document…' : 'Export content as .docx'}
            <span className="mt-0.5 block text-[11px] text-ink-faint">
              Edit the brief offline, then re-upload it in Document mode.
            </span>
          </button>
          <button
            role="menuitem"
            type="button"
            onClick={() => {
              setOpen(false)
              downloadSiteJson(site)
            }}
            className="block w-full rounded-lg px-3 py-2 text-left text-sm text-ink-soft transition hover:bg-surface-sunken hover:text-ink"
          >
            Download site JSON
            <span className="mt-0.5 block text-[11px] text-ink-faint">
              The generated tree as-is, for debugging a layout or colour.
            </span>
          </button>
          <button
            role="menuitem"
            type="button"
            onClick={() => {
              setOpen(false)
              onStartOver()
            }}
            className="block w-full rounded-lg px-3 py-2 text-left text-sm text-rose-700 transition hover:bg-rose-50"
          >
            Start over
            <span className="mt-0.5 block text-[11px] text-rose-500">
              Discards this site and the scraped source. Your brand settings stay.
            </span>
          </button>
        </div>
      )}
    </div>
  )
}
