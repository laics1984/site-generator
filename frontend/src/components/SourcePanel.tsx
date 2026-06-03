import { useRef, useState } from 'react'

import type { GeneratorMode, SourceContent } from '@/lib/types'

interface SourcePanelProps {
  mode: GeneratorMode
  busy: boolean
  /** Called when the user wants to scrape a URL (URL mode). */
  onScrape: (url: string, opts: { crawl: boolean }) => void
  /** Called when a PDF/DOCX has been chosen and should be uploaded for preview. */
  onUpload: (file: File) => void
  /** Called when the user wants to generate from pasted content (Doc mode fallback). */
  onGenerate: (source: SourceContent) => void
  /** Whether a scrape preview is currently being shown. */
  scrapeBusy?: boolean
  /** Whether a document is currently being parsed. */
  uploadBusy?: boolean
}

export function SourcePanel({
  mode,
  busy,
  onScrape,
  onUpload,
  onGenerate,
  scrapeBusy,
  uploadBusy,
}: SourcePanelProps) {
  const [url, setUrl] = useState('')
  const [crawl, setCrawl] = useState(true)
  const [pastedText, setPastedText] = useState('')
  const [pastedTitle, setPastedTitle] = useState('')

  if (mode === 'url') {
    return (
      <div className="space-y-4">
        <label className="block">
          <span className="text-sm font-medium text-slate-700">Website URL</span>
          <div className="mt-1 flex gap-2">
            <input
              type="url"
              placeholder="https://example.com"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && url.trim() && !scrapeBusy) {
                  e.preventDefault()
                  onScrape(url.trim(), { crawl })
                }
              }}
              className="block flex-1 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
            />
            <button
              type="button"
              onClick={() => onScrape(url.trim(), { crawl })}
              disabled={!url.trim() || scrapeBusy}
              className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {scrapeBusy ? (crawl ? 'Crawling…' : 'Fetching…') : 'Fetch site'}
            </button>
          </div>
        </label>
        <label className="flex cursor-pointer items-start gap-2 rounded-xl border border-slate-200 bg-white p-3">
          <input
            type="checkbox"
            checked={crawl}
            onChange={(e) => setCrawl(e.target.checked)}
            disabled={scrapeBusy}
            className="mt-0.5 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-200"
          />
          <div className="flex-1">
            <div className="text-sm font-medium text-slate-800">
              Discover sub-pages from the site
            </div>
            <div className="text-xs text-slate-500">
              We follow same-domain links up to 3 clicks away from the homepage
              (max ~20 extra pages) — including pages hidden from the main menu
              but linked from sub-pages like /services or /about. Adds roughly
              15–30 seconds. Uncheck for a fast single-page generation.
            </div>
          </div>
        </label>
        <p className="text-xs text-slate-500">
          We'll render the page in headless Chromium, pull text + headings + image
          candidates, and try to detect the logo and brand palette. You'll see a
          preview before any AI work runs.
        </p>
      </div>
    )
  }

  // Document mode
  return (
    <div className="space-y-4">
      <DocumentDropZone busy={!!uploadBusy} onFile={onUpload} />
      <details className="rounded-xl border border-slate-200 bg-white p-3">
        <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wider text-slate-500">
          Or paste content directly
        </summary>
        <p className="mt-2 text-xs text-slate-500">
          Use this if your PDF is image-only (no text layer), or if you just want
          to try out the generator with arbitrary copy.
        </p>
        <label className="mt-3 block">
          <span className="text-sm font-medium text-slate-700">Title (optional)</span>
          <input
            type="text"
            value={pastedTitle}
            onChange={(e) => setPastedTitle(e.target.value)}
            placeholder="e.g. Acme Coffee Roasters — homepage"
            className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
        </label>
        <label className="mt-2 block">
          <span className="text-sm font-medium text-slate-700">Raw content</span>
          <textarea
            rows={8}
            value={pastedText}
            onChange={(e) => setPastedText(e.target.value)}
            placeholder="Paste the document body here…"
            className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
        </label>
        <button
          type="button"
          disabled={!pastedText.trim() || busy}
          onClick={() =>
            onGenerate({
              source_kind: 'pdf',
              source_ref: pastedTitle || 'pasted-document',
              title: pastedTitle || undefined,
              raw_text: pastedText,
            })
          }
          className="mt-3 inline-flex items-center justify-center rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {busy ? 'Generating…' : 'Generate from paste'}
        </button>
      </details>
    </div>
  )
}

// --- drop zone -----------------------------------------------------------------


function DocumentDropZone({
  busy,
  onFile,
}: {
  busy: boolean
  onFile: (file: File) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  function handleFiles(files: FileList | null) {
    const file = files?.[0]
    if (!file) return
    onFile(file)
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        if (!busy) handleFiles(e.dataTransfer.files)
      }}
      className={
        'rounded-2xl border-2 border-dashed p-6 text-center transition ' +
        (dragging
          ? 'border-blue-500 bg-blue-50'
          : 'border-slate-300 bg-slate-50 hover:border-slate-400')
      }
    >
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />
      <div className="flex flex-col items-center gap-2">
        <svg
          className="h-8 w-8 text-slate-400"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M14 3v4a1 1 0 0 0 1 1h4" />
          <path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z" />
          <path d="M9 13h6M9 17h6M9 9h1" />
        </svg>
        <div className="text-sm font-medium text-slate-800">
          {busy ? 'Parsing document…' : 'Drop a PDF or DOCX here'}
        </div>
        <div className="text-xs text-slate-500">
          We'll pull text, headings, images, and detect a brand logo if one is on the
          cover page. Max 20 MB.
        </div>
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          className="mt-1 rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
        >
          {busy ? 'Working…' : 'Choose file'}
        </button>
      </div>
    </div>
  )
}
