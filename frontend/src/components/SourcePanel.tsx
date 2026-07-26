import { useRef, useState } from 'react'

import type { GeneratorMode, SourceContent } from '@/lib/types'
import { Button, Checkbox, Field, Input, Textarea } from '@/ui'

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
        {/* Not a <Field>: the submit button sits next to the input, and wrapping
         * a button in the field's <label> makes clicking it also focus the input. */}
        <div>
          <label htmlFor="source-url" className="text-xs font-semibold text-ink-soft">
            Website URL
          </label>
          <div className="mt-1.5 flex gap-2">
            <Input
              id="source-url"
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
              className="flex-1"
            />
            <Button
              variant="primary"
              size="lg"
              onClick={() => onScrape(url.trim(), { crawl })}
              disabled={!url.trim()}
              busy={!!scrapeBusy}
            >
              {scrapeBusy ? (crawl ? 'Crawling…' : 'Fetching…') : 'Fetch site'}
            </Button>
          </div>
          <p className="mt-1.5 text-xs text-ink-muted">
            We render the page in headless Chromium, pull text, headings and image
            candidates, and try to detect the logo and brand palette. You'll see a preview
            before any AI work runs.
          </p>
        </div>

        <div className="rounded-xl border border-line bg-surface p-3">
          <Checkbox
            checked={crawl}
            onChange={(e) => setCrawl(e.target.checked)}
            disabled={scrapeBusy}
            label="Discover sub-pages from the site"
            description="We follow same-domain links up to 3 clicks away from the homepage (max ~20 extra pages) — including pages hidden from the main menu but linked from sub-pages like /services or /about. Adds roughly 15–30 seconds. Uncheck for a fast single-page generation."
          />
        </div>
      </div>
    )
  }

  // Document mode
  return (
    <div className="space-y-4">
      <DocumentDropZone busy={!!uploadBusy} onFile={onUpload} />
      <details className="rounded-xl border border-line bg-surface p-3">
        <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
          Or paste content directly
        </summary>
        <p className="mt-2 text-xs text-ink-muted">
          Use this if your PDF is image-only (no text layer), or if you just want
          to try out the generator with arbitrary copy.
        </p>
        <Field label="Title" optional className="mt-3">
          <Input
            type="text"
            value={pastedTitle}
            onChange={(e) => setPastedTitle(e.target.value)}
            placeholder="e.g. Acme Coffee Roasters — homepage"
          />
        </Field>
        <Field label="Raw content" className="mt-3">
          <Textarea
            rows={8}
            value={pastedText}
            onChange={(e) => setPastedText(e.target.value)}
            placeholder="Paste the document body here…"
          />
        </Field>
        <Button
          variant="primary"
          size="lg"
          className="mt-3"
          disabled={!pastedText.trim()}
          busy={busy}
          onClick={() =>
            onGenerate({
              source_kind: 'pdf',
              source_ref: pastedTitle || 'pasted-document',
              title: pastedTitle || undefined,
              raw_text: pastedText,
            })
          }
        >
          {busy ? 'Generating…' : 'Generate from paste'}
        </Button>
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
          ? 'border-brand-500 bg-brand-50'
          : 'border-line-strong bg-surface-sunken hover:border-ink-faint')
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
          className="h-8 w-8 text-ink-faint"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M14 3v4a1 1 0 0 0 1 1h4" />
          <path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z" />
          <path d="M9 13h6M9 17h6M9 9h1" />
        </svg>
        <div className="text-sm font-medium text-ink">
          {busy ? 'Parsing document…' : 'Drop a PDF or DOCX here'}
        </div>
        <div className="text-xs text-ink-muted">
          We'll pull text, headings, images, and detect a brand logo if one is on the
          cover page. Max 20 MB.
        </div>
        <Button className="mt-1" onClick={() => inputRef.current?.click()} busy={busy}>
          {busy ? 'Working…' : 'Choose file'}
        </Button>
      </div>
    </div>
  )
}
