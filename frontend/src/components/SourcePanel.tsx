import { useRef, useState } from 'react'

import { PasteBox } from '@/components/PasteBox'
import { facebookLinkWarning, isFacebookUrl } from '@/lib/sourceDetect'
import { Button, Checkbox, Field, Input } from '@/ui'

// Three independent optional fields, not three modes: a website, a document
// and pasted text can all be filled in at once, in any combination, and one
// shared submit reads whichever are non-empty and combines them
// (App.tsx's handleComposeSubmit → services/source_merge.merge_sources on the
// backend). There is deliberately no "choose one" affordance here anymore.
interface SourcePanelProps {
  url: string
  onUrlChange: (url: string) => void
  crawl: boolean
  onCrawlChange: (crawl: boolean) => void
  fbToken: string
  onFbTokenChange: (token: string) => void

  file: File | null
  onFileChange: (file: File | null) => void

  pastedText: string
  onPastedTextChange: (text: string) => void
  pasteTitle: string
  onPasteTitleChange: (title: string) => void

  onSubmit: () => void
  /** True while any of the three legs (crawl, document parse, paste read) is running. */
  busy: boolean
}

export function SourcePanel({
  url,
  onUrlChange,
  crawl,
  onCrawlChange,
  fbToken,
  onFbTokenChange,
  file,
  onFileChange,
  pastedText,
  onPastedTextChange,
  pasteTitle,
  onPasteTitleChange,
  onSubmit,
  busy,
}: SourcePanelProps) {
  // Detected on every keystroke so the form reacts to what the user already
  // typed rather than asking them to classify their own link first.
  const isFb = isFacebookUrl(url)
  const warning = facebookLinkWarning(url)
  const canSubmit = Boolean(url.trim() || file || pastedText.trim()) && !busy

  function submit() {
    if (!canSubmit) return
    onSubmit()
  }

  return (
    <div className="space-y-5">
      <p className="text-xs text-ink-muted">
        Add any combination of these — a website, a document, your own text —
        and we'll read all of them into one source.
      </p>

      {/* --- website / Facebook Page --------------------------------------- */}
      <div>
        {/* Not a <Field>: the submit button lives at the bottom of the whole
         * panel now, so this is a plain labelled input. */}
        <label htmlFor="source-url" className="text-xs font-semibold text-ink-soft">
          {isFb ? 'Facebook Page link' : 'Website URL'}{' '}
          <span className="font-normal text-ink-faint">optional</span>
        </label>
        <div className="relative mt-1.5">
          <Input
            id="source-url"
            type="text"
            placeholder="https://example.com or facebook.com/yourpage"
            value={url}
            onChange={(e) => onUrlChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                submit()
              }
            }}
            disabled={busy}
            className={isFb ? 'w-full pr-28' : 'w-full'}
          />
          {isFb && (
            <span
              className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded-md bg-[#1877F2]/10 px-2 py-0.5 text-[11px] font-semibold text-[#1877F2]"
              aria-live="polite"
            >
              Facebook Page
            </span>
          )}
        </div>
        <p className="mt-1.5 text-xs text-ink-muted">
          {isFb
            ? "We'll read this Page's About section, contact details, opening hours, photos and posts, and use its profile picture as the brand mark."
            : 'We render the page in headless Chromium, pull text, headings and image candidates, and try to detect the logo and brand palette.'}
        </p>
        {warning && <p className="mt-1.5 text-xs font-medium text-amber-700">{warning}</p>}

        {url.trim() && (
          <div className="mt-3 rounded-xl border border-line bg-surface p-3">
            {isFb ? (
              <details>
                <summary className="cursor-pointer text-xs font-semibold text-ink-soft">
                  Reading more from this Page (optional)
                </summary>
                <p className="mt-2 text-xs text-ink-muted">
                  Without a token we read what the Page shows publicly. A Page access
                  token — from a Page you administer — also gives us emails, structured
                  opening hours, posts and recommendations. Try it without one first;
                  we'll tell you exactly what was missing.
                </p>
                <Input
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="Page access token"
                  value={fbToken}
                  onChange={(e) => onFbTokenChange(e.target.value)}
                  disabled={busy}
                  className="mt-2 w-full font-mono text-xs"
                />
                <p className="mt-1.5 text-[11px] text-ink-faint">
                  Used for this read only — never saved, never logged.
                </p>
              </details>
            ) : (
              <Checkbox
                checked={crawl}
                onChange={(e) => onCrawlChange(e.target.checked)}
                disabled={busy}
                label="Discover sub-pages from the site"
                description="We follow same-domain links up to 3 clicks away from the homepage (max ~20 extra pages). Adds roughly 15–30 seconds. Uncheck for a fast single-page read."
              />
            )}
          </div>
        )}
      </div>

      {/* --- document upload ------------------------------------------------ */}
      <div>
        <div className="text-xs font-semibold text-ink-soft">
          Upload a document <span className="font-normal text-ink-faint">optional</span>
        </div>
        <div className="mt-1.5">
          <DocumentDropZone file={file} onFileChange={onFileChange} disabled={busy} />
        </div>
      </div>

      {/* --- paste ----------------------------------------------------------- */}
      <div className="space-y-3">
        <div className="text-xs font-semibold text-ink-soft">
          Paste your content <span className="font-normal text-ink-faint">optional</span>
        </div>
        <Field label="Title" optional>
          <Input
            type="text"
            value={pasteTitle}
            onChange={(e) => onPasteTitleChange(e.target.value)}
            placeholder="e.g. Acme Coffee Roasters"
            disabled={busy}
          />
        </Field>
        <PasteBox
          value={pastedText}
          onChange={onPastedTextChange}
          onSubmit={submit}
          disabled={busy}
          rows={8}
          label="Content"
          placeholder={
            'Paste your copy, or a page’s HTML.\n\n' +
            'Headings become pages: a line like “Contact” or an <h2>Our Services</h2> ' +
            'opens that page, and everything under it is that page’s content.'
          }
          hint="Copy, markdown or HTML — we work out which. Merged with whatever else you add above."
        />
      </div>

      <Button
        variant="primary"
        size="lg"
        fullWidth
        disabled={!canSubmit}
        busy={busy}
        onClick={submit}
      >
        {busy ? 'Reading your content…' : 'Continue'}
      </Button>
      <p className="text-xs text-ink-muted">
        You'll see a combined preview before any AI work runs.
      </p>
    </div>
  )
}

// --- drop zone -----------------------------------------------------------------

function DocumentDropZone({
  file,
  onFileChange,
  disabled,
}: {
  file: File | null
  onFileChange: (file: File | null) => void
  disabled?: boolean
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  function handleFiles(files: FileList | null) {
    const picked = files?.[0]
    if (picked) onFileChange(picked)
  }

  if (file) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-2xl border border-line bg-surface-sunken p-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <svg
            className="h-6 w-6 shrink-0 text-ink-faint"
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
          </svg>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-ink">{file.name}</div>
            <div className="text-xs text-ink-muted">
              {(file.size / 1024).toFixed(0)} KB — ready to read
            </div>
          </div>
        </div>
        <Button size="sm" onClick={() => onFileChange(null)} disabled={disabled}>
          Remove
        </Button>
      </div>
    )
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
        if (!disabled) handleFiles(e.dataTransfer.files)
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
        <div className="text-sm font-medium text-ink">Drop a PDF or DOCX here</div>
        <div className="text-xs text-ink-muted">
          We'll pull text, headings, images, and detect a brand logo if one is on the
          cover page. Max 20 MB.
        </div>
        <Button className="mt-1" onClick={() => inputRef.current?.click()} disabled={disabled}>
          Choose file
        </Button>
      </div>
    </div>
  )
}
