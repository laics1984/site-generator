import { useState } from 'react'

import { exportSiteDocument } from '@/lib/api'
import type { ScrapePreview as ScrapePreviewType } from '@/lib/types'

interface ScrapePreviewProps {
  preview: ScrapePreviewType
  hasManualBrand: boolean
  /** Caller chose to apply the scraped brand candidate to the Brand panel. */
  onApplyBrand: () => void
  /** Caller wants to drop scrape results and start over. */
  onDiscard: () => void
  /** Caller is happy with the preview and wants to fire the LLM. */
  onConfirm: (editedRawText: string, title: string) => void
  /** Caller wants to keep crawling — resumes from the unvisited frontier. */
  onCrawlMore?: (maxMore: number) => void
  /** Whether an extend-crawl is in flight. */
  extendBusy?: boolean
  /** Whether the parent is mid-generation. */
  busy: boolean
}

export function ScrapePreview({
  preview,
  hasManualBrand,
  onApplyBrand,
  onDiscard,
  onConfirm,
  onCrawlMore,
  extendBusy,
  busy,
}: ScrapePreviewProps) {
  const sc = preview.source_content
  const [text, setText] = useState(sc.raw_text)
  const [title, setTitle] = useState(sc.title || '')
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)
  const brand = preview.brand_candidate

  const handleExport = async () => {
    setExporting(true)
    setExportError(null)
    try {
      // Reflect any inline edits to the primary page's title/copy in the brief.
      await exportSiteDocument(
        { ...sc, raw_text: text, title: title || sc.title },
        title || sc.title || '',
      )
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Export failed.')
    } finally {
      setExporting(false)
    }
  }

  const heroImages = preview.image_candidates.filter((c) => c.intent === 'hero')
  const otherImages = preview.image_candidates.filter((c) => c.intent !== 'hero')

  const isDocument = sc.source_kind === 'pdf' || sc.source_kind === 'docx'
  const bannerLabel = isDocument
    ? `${sc.source_kind.toUpperCase()} parsed`
    : 'Scrape OK'
  const sourceLabel = isDocument ? 'File' : 'Source'

  const unvisitedCount = preview.unvisited_count ?? preview.unvisited_urls?.length ?? 0
  const canCrawlMore = !isDocument && unvisitedCount > 0 && !!onCrawlMore

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900">
        <div className="font-semibold">{bannerLabel}</div>
        <div className="truncate">
          {sourceLabel}: {preview.final_url}
        </div>
        {(preview.discovered_count ?? 0) > 0 && (
          <div className="mt-1">
            <span className="font-semibold">{preview.discovered_count}</span>{' '}
            additional page{preview.discovered_count === 1 ? '' : 's'} discovered.
          </div>
        )}
      </div>

      {canCrawlMore && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1">
              <div className="font-semibold">More pages available</div>
              <div className="mt-0.5">
                The crawler queued{' '}
                <span className="font-semibold">{unvisitedCount}</span> more URL
                {unvisitedCount === 1 ? '' : 's'} but stopped at the page cap.
                Want to keep going?
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <button
                type="button"
                disabled={extendBusy || busy}
                onClick={() => onCrawlMore?.(Math.min(unvisitedCount, 20))}
                className="rounded-lg bg-amber-700 px-3 py-1.5 text-[11px] font-semibold text-white shadow-sm transition hover:bg-amber-800 disabled:cursor-not-allowed disabled:bg-amber-300"
              >
                {extendBusy ? 'Crawling…' : `Crawl ${Math.min(unvisitedCount, 20)} more`}
              </button>
              {unvisitedCount > 20 && (
                <button
                  type="button"
                  disabled={extendBusy || busy}
                  onClick={() => onCrawlMore?.(Math.min(unvisitedCount, 40))}
                  className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-[11px] font-medium text-amber-900 hover:bg-amber-50 disabled:opacity-60"
                >
                  Crawl {Math.min(unvisitedCount, 40)} more
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {(sc.discovered_pages ?? []).length > 0 && (
        <details className="rounded-xl border border-slate-200 bg-white p-3" open>
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wider text-slate-500">
            Sub-pages found ({(sc.discovered_pages ?? []).length})
          </summary>
          <ul className="mt-2 max-h-48 space-y-1 overflow-auto text-xs text-slate-700">
            {(sc.discovered_pages ?? []).map((p, i) => (
              <li key={i} className="flex items-baseline gap-2">
                <span className="font-mono text-slate-500">{p.url_path}</span>
                <span className="truncate text-slate-700">— {p.title ?? '(untitled)'}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {brand && (
        <div className="rounded-xl border border-slate-200 bg-white p-3">
          <div className="flex items-start justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                Detected brand
              </div>
              <div className="mt-1 text-sm font-semibold text-slate-900">
                {brand.name}
              </div>
            </div>
            {brand.logo_data_url && (
              <img
                src={brand.logo_data_url}
                alt={brand.name}
                className="h-10 max-w-[120px] rounded object-contain"
              />
            )}
          </div>
          {brand.extracted_palette && brand.extracted_palette.length > 0 && (
            <div className="mt-2 flex gap-1.5">
              {brand.extracted_palette.slice(0, 5).map((color) => (
                <div
                  key={color}
                  className="h-5 w-5 rounded border border-slate-200"
                  style={{ backgroundColor: color }}
                  title={color}
                />
              ))}
            </div>
          )}
          <button
            type="button"
            onClick={onApplyBrand}
            disabled={hasManualBrand}
            className="mt-3 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
            title={
              hasManualBrand
                ? 'You uploaded a logo manually — that takes precedence. Clear it in the Brand panel to switch.'
                : 'Use this brand for the generated site'
            }
          >
            {hasManualBrand ? 'Manual brand uploaded' : 'Use this brand'}
          </button>
        </div>
      )}

      {sc.headings && sc.headings.length > 0 && (
        <details className="rounded-xl border border-slate-200 bg-white p-3" open>
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wider text-slate-500">
            Headings extracted ({sc.headings.length})
          </summary>
          <ul className="mt-2 max-h-32 space-y-1 overflow-auto text-xs text-slate-700">
            {sc.headings.slice(0, 20).map((h, i) => (
              <li key={i} className="truncate">• {h}</li>
            ))}
          </ul>
        </details>
      )}

      {preview.image_candidates.length > 0 && (
        <details className="rounded-xl border border-slate-200 bg-white p-3">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wider text-slate-500">
            Image candidates ({preview.image_candidates.length})
          </summary>
          {heroImages.length > 0 && (
            <div className="mt-2">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-blue-600">
                Hero candidates
              </div>
              <div className="mt-1 grid grid-cols-3 gap-2">
                {heroImages.slice(0, 6).map((c) => (
                  <ImageThumb key={c.url} url={c.url} alt={c.alt} />
                ))}
              </div>
            </div>
          )}
          {otherImages.length > 0 && (
            <div className="mt-2">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                Other
              </div>
              <div className="mt-1 grid grid-cols-4 gap-1.5">
                {otherImages.slice(0, 12).map((c) => (
                  <ImageThumb key={c.url} url={c.url} alt={c.alt} small />
                ))}
              </div>
            </div>
          )}
        </details>
      )}

      <label className="block">
        <span className="text-sm font-medium text-slate-700">
          Title (used by the LLM)
        </span>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
        />
      </label>

      <label className="block">
        <span className="text-sm font-medium text-slate-700">
          Extracted content (edit to fix bad extractions)
        </span>
        <textarea
          rows={10}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-mono shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
        />
        <span className="mt-1 block text-xs text-slate-500">
          {text.length.toLocaleString()} characters
        </span>
      </label>

      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy || !text.trim()}
          onClick={() => onConfirm(text, title)}
          className="flex-1 rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {busy ? 'Generating…' : 'Generate website'}
        </button>
        <button
          type="button"
          onClick={onDiscard}
          disabled={busy}
          className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
        >
          Discard
        </button>
      </div>

      {!isDocument && (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs text-slate-600">
              Prefer to edit the content first? Download a Word brief, revise the
              titles and copy, then re-upload it to build the site from the
              document.
            </div>
            <button
              type="button"
              onClick={handleExport}
              disabled={busy || exporting}
              className="shrink-0 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {exporting ? 'Exporting…' : 'Export as document'}
            </button>
          </div>
          {exportError && (
            <div className="mt-2 text-xs text-red-600">{exportError}</div>
          )}
        </div>
      )}
    </div>
  )
}

function ImageThumb({ url, alt, small }: { url: string; alt: string; small?: boolean }) {
  return (
    <div className={small ? 'aspect-square overflow-hidden rounded' : 'aspect-video overflow-hidden rounded-lg border border-slate-200'}>
      <img
        src={url}
        alt={alt}
        className="h-full w-full object-cover"
        loading="lazy"
        referrerPolicy="no-referrer"
      />
    </div>
  )
}
