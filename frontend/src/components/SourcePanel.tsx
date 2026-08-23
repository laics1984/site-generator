import { useRef, useState } from 'react'

import { PasteBox } from '@/components/PasteBox'
import { facebookLinkWarning, isFacebookUrl } from '@/lib/sourceDetect'
import type { GeneratorMode } from '@/lib/types'
import { Button, Checkbox, Field, Input } from '@/ui'

interface SourcePanelProps {
  mode: GeneratorMode
  /** Called when the user wants to read a link. The backend picks the reader
   * from the URL itself, so this one handler covers websites and Facebook
   * Pages alike — `accessToken` is only ever populated for the latter. */
  onScrape: (url: string, opts: { crawl: boolean; accessToken?: string }) => void
  /** Called when a PDF/DOCX has been chosen and should be uploaded for preview. */
  onUpload: (file: File) => void
  /** Called when the paste is the whole source and should be read on its own. */
  onReadPaste: () => void
  /** The paste box's contents, lifted to the workspace: in link and document
   * mode it rides along with the read and is merged into whatever comes back,
   * so it has to outlive this panel. */
  pastedText: string
  onPastedTextChange: (text: string) => void
  pasteTitle: string
  onPasteTitleChange: (title: string) => void
  /** Whether a scrape preview is currently being fetched. */
  scrapeBusy?: boolean
  /** Whether a document is currently being parsed. */
  uploadBusy?: boolean
  /** Whether a standalone paste is being read. */
  pasteBusy?: boolean
}

export function SourcePanel({
  mode,
  onScrape,
  onUpload,
  onReadPaste,
  pastedText,
  onPastedTextChange,
  pasteTitle,
  onPasteTitleChange,
  scrapeBusy,
  uploadBusy,
  pasteBusy,
}: SourcePanelProps) {
  const [url, setUrl] = useState('')
  const [crawl, setCrawl] = useState(true)
  const [fbToken, setFbToken] = useState('')

  if (mode === 'paste') {
    return (
      <div className="space-y-4">
        <Field label="Title" optional>
          <Input
            type="text"
            value={pasteTitle}
            onChange={(e) => onPasteTitleChange(e.target.value)}
            placeholder="e.g. Acme Coffee Roasters"
            disabled={pasteBusy}
          />
        </Field>
        <PasteBox
          value={pastedText}
          onChange={onPastedTextChange}
          onSubmit={onReadPaste}
          disabled={pasteBusy}
          rows={12}
          label="Content"
          placeholder={
            'Paste your copy, or a page’s HTML.\n\n' +
            'Headings become pages: a line like “Contact” or an <h2>Our Services</h2> ' +
            'opens that page, and everything under it is that page’s content.'
          }
          hint="Copy, markdown or HTML — we work out which."
        />
        <Button
          variant="primary"
          size="lg"
          fullWidth
          disabled={!pastedText.trim()}
          busy={!!pasteBusy}
          onClick={onReadPaste}
        >
          {pasteBusy ? 'Reading…' : 'Read content'}
        </Button>
        <p className="text-xs text-ink-muted">
          We keep any images the markup points at by full web address, detect the
          page structure, and show you a preview before choosing pages. No AI work
          runs until then.
        </p>
      </div>
    )
  }

  if (mode === 'url') {
    // Detected on every keystroke so the form reacts to what the user already
    // typed rather than asking them to classify their own link first.
    const isFb = isFacebookUrl(url)
    const warning = facebookLinkWarning(url)
    const submit = () => {
      if (!url.trim() || scrapeBusy) return
      onScrape(url.trim(), {
        crawl: isFb ? false : crawl,
        accessToken: isFb ? fbToken : undefined,
      })
    }

    return (
      <div className="space-y-4">
        {/* Not a <Field>: the submit button sits next to the input, and wrapping
         * a button in the field's <label> makes clicking it also focus the input. */}
        <div>
          <label htmlFor="source-url" className="text-xs font-semibold text-ink-soft">
            {isFb ? 'Facebook Page link' : 'Website URL'}
          </label>
          <div className="mt-1.5 flex gap-2">
            <div className="relative flex-1">
              <Input
                id="source-url"
                type="text"
                placeholder="https://example.com or facebook.com/yourpage"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    submit()
                  }
                }}
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
            <Button
              variant="primary"
              size="lg"
              onClick={submit}
              disabled={!url.trim()}
              busy={!!scrapeBusy}
            >
              {scrapeBusy
                ? isFb
                  ? 'Reading…'
                  : crawl
                    ? 'Crawling…'
                    : 'Fetching…'
                : isFb
                  ? 'Read Page'
                  : 'Fetch site'}
            </Button>
          </div>
          <p className="mt-1.5 text-xs text-ink-muted">
            {isFb
              ? "We'll read this Page's About section, contact details, opening hours, photos and posts, and use its profile picture as the brand mark. Nothing that isn't on the Page ends up on the site."
              : 'We render the page in headless Chromium, pull text, headings and image candidates, and try to detect the logo and brand palette.'}{' '}
            You'll see a preview before any AI work runs.
          </p>
          {warning && (
            <p className="mt-1.5 text-xs font-medium text-amber-700">{warning}</p>
          )}
        </div>

        {/* Same slot, different control. Swapping contents rather than
         * unmounting keeps the layout from jumping mid-keystroke, which reads
         * as the form breaking. */}
        <div className="rounded-xl border border-line bg-surface p-3">
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
                onChange={(e) => setFbToken(e.target.value)}
                disabled={scrapeBusy}
                className="mt-2 w-full font-mono text-xs"
              />
              <p className="mt-1.5 text-[11px] text-ink-faint">
                Used for this read only — never saved, never logged.
              </p>
            </details>
          ) : (
            <Checkbox
              checked={crawl}
              onChange={(e) => setCrawl(e.target.checked)}
              disabled={scrapeBusy}
              label="Discover sub-pages from the site"
              description="We follow same-domain links up to 3 clicks away from the homepage (max ~20 extra pages) — including pages hidden from the main menu but linked from sub-pages like /services or /about. Adds roughly 15–30 seconds. Uncheck for a fast single-page generation."
            />
          )}
        </div>

        <ExtraContent
          text={pastedText}
          onChange={onPastedTextChange}
          disabled={scrapeBusy}
          submitLabel={isFb ? 'read the Page' : 'fetch the site'}
        />
      </div>
    )
  }

  // Document mode
  return (
    <div className="space-y-4">
      <DocumentDropZone busy={!!uploadBusy} onFile={onUpload} />
      <ExtraContent
        text={pastedText}
        onChange={onPastedTextChange}
        disabled={uploadBusy}
        submitLabel="choose a file"
      />
    </div>
  )
}

// --- extra content ride-along ---------------------------------------------------

/**
 * The paste box offered next to a link or a document.
 *
 * Collapsed by default so the primary action stays the obvious one, and open
 * whenever it already holds something — coming back from the preview should
 * show what you typed, not hide it. What's inside is merged into whatever the
 * reader finds, so it is submitted by the reader's own button rather than
 * carrying a second one that would make the user choose between them.
 */
function ExtraContent({
  text,
  onChange,
  disabled,
  submitLabel,
}: {
  text: string
  onChange: (text: string) => void
  disabled?: boolean
  submitLabel: string
}) {
  const [open, setOpen] = useState(text.length > 0)

  return (
    <div className="rounded-xl border border-line bg-surface p-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 text-left"
      >
        <span className="text-xs font-semibold text-ink-soft">
          {open ? '−' : '+'} Add extra content{' '}
          <span className="font-normal text-ink-faint">optional</span>
        </span>
        {!open && text.trim() && (
          <span className="shrink-0 rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] font-semibold text-brand-700">
            {text.length.toLocaleString()} characters ready
          </span>
        )}
      </button>
      {open && (
        <div className="mt-3">
          <PasteBox
            value={text}
            onChange={onChange}
            disabled={disabled}
            rows={6}
            label="Copy or HTML to include"
            placeholder="Paste anything the source doesn’t say — new copy, a price list, a page you want added…"
            hint={`Merged into what we read when you ${submitLabel}. Headings that name a page (“Contact”, “Our Team”) join that page, or add it.`}
          />
        </div>
      )}
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
