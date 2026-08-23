import { useEffect, useMemo, useState } from 'react'

import { BrandPanel } from '@/components/BrandPanel'
import { CrawlProgress } from '@/components/CrawlProgress'
import { ModeTabs } from '@/components/ModeTabs'
import { PageList } from '@/components/PageList'
import { PagePicker } from '@/components/PagePicker'
import { PagePreview } from '@/components/PagePreview'
import { PublishDrawer } from '@/components/PublishDrawer'
import { ScopeChoice } from '@/components/ScopeChoice'
import { ScrapePreview } from '@/components/ScrapePreview'
import { SiteSummaryBar } from '@/components/SiteSummaryBar'
import { SourcePanel } from '@/components/SourcePanel'
import { WorkspaceHeader } from '@/components/WorkspaceHeader'
import {
  cancelCrawlJob,
  exportSiteDocument,
  extendCrawl,
  generateWithPages,
  getCrawlJob,
  probeSitemap,
  readPastedContent,
  startCrawl,
  uploadDocumentPreview,
  type GenerateWithPagesPayload,
} from '@/lib/api'
import { isFacebookUrl } from '@/lib/sourceDetect'
import { withPastedContent } from '@/lib/sourcePaste'
import type {
  BrandIdentity,
  BrandMood,
  ColorSchemeChoice,
  BuilderStylesShape,
  CrawlJob,
  DetectedBrand,
  GeneratedSite,
  GeneratorMode,
  HeroHeightChoice,
  IndustryCategory,
  PageScaffold,
  ScrapePreview as ScrapePreviewType,
  SitemapProbeResult,
  SourceContent,
} from '@/lib/types'
import { Banner, Button, SectionLabel, Stepper, type Step } from '@/ui'

const GOOGLE_FONTS_BASE = 'https://fonts.googleapis.com/css2?'

/** The exact call that produced the current site, kept so "Regenerate" is one
 * click rather than a walk back through the wizard.
 *
 * There used to be a second, page-picker-less variant here for the hidden
 * "paste content directly" box. A paste is now a first-class source and takes
 * the same preview → pages → generate path as a crawl or an upload, so
 * /api/generate/from-source has no caller in the app — it stays as the
 * programmatic entry point, not as a second thing this wizard can do. */
type LastGenerate = { kind: 'with-pages'; payload: GenerateWithPagesPayload }

export default function App() {
  const [mode, setMode] = useState<GeneratorMode>('url')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Brand state — populated either manually (BrandPanel upload) or auto-detected
  // from a scrape. `manualBrand` tracks whether the user uploaded a logo — manual
  // ALWAYS wins per the agreed precedence.
  const [brand, setBrand] = useState<BrandIdentity | null>(null)
  const [manualBrand, setManualBrand] = useState(false)
  const [brandName, setBrandName] = useState('')
  const [mood, setMood] = useState<BrandMood>('modern')
  // 'auto' → send null so the backend decides from the logo (light logo ⇒ dark).
  const [colorScheme, setColorScheme] = useState<ColorSchemeChoice>('auto')
  // Hero photo-background height, site-wide. 'full' = full-screen hero (default).
  const [heroHeight, setHeroHeight] = useState<HeroHeightChoice>('auto')
  // Dress the whole site in Pexels stock and ignore the source's own photos.
  const [stockImagesOnly, setStockImagesOnly] = useState(false)
  const [themePreview, setThemePreview] = useState<BuilderStylesShape | null>(null)
  const [googleFonts, setGoogleFonts] = useState<string[]>([])

  // Scrape state (URL mode) + upload state (Doc mode). Both flow into the
  // same `scrapeResult` slot so ScrapePreview renders either source kind.
  const [scrapeBusy, setScrapeBusy] = useState(false)
  const [uploadBusy, setUploadBusy] = useState(false)
  const [pasteBusy, setPasteBusy] = useState(false)
  const [scrapeResult, setScrapeResult] = useState<ScrapePreviewType | null>(null)
  // The paste box. Lifted because it outlives SourcePanel: in link and document
  // mode the paste rides along with the read and is merged into the result.
  const [pastedText, setPastedText] = useState('')
  const [pasteTitle, setPasteTitle] = useState('')
  // Scope-choice modal state. Set after a successful sitemap probe that
  // reveals more pages than our quick-scan default (20).
  const [pendingScope, setPendingScope] = useState<{
    url: string
    probe: SitemapProbeResult
  } | null>(null)
  // Wall-clock label fo189 "Crawl more" step.
  const [extendBusy, setExtendBusy] = useState(false)
  // Live job state during async crawls (queued/running). null once done/cancelled.
  const [activeJob, setActiveJob] = useState<CrawlJob | null>(null)
  // Page-cap chosen for the active job — drives the progress bar's denominator.
  const [activeJobCap, setActiveJobCap] = useState<number | null>(null)

  // Page picker state — between source confirmation and generation
  const [confirmedSource, setConfirmedSource] = useState<SourceContent | null>(null)
  const [selectedPages, setSelectedPages] = useState<PageScaffold[]>([])
  const [industryOverride, setIndustryOverride] = useState<IndustryCategory | null>(null)
  // Captured from /api/pages/recipe and passed to /generate/with-pages so we
  // don't re-run brand detection.
  const [detectedBrand, setDetectedBrand] = useState<DetectedBrand | null>(null)

  const [site, setSite] = useState<GeneratedSite | null>(null)
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null)

  // Workspace stage. The wizard is never destroyed on success — it is hidden,
  // so "Adjust & regenerate" lands back on the page picker with the user's
  // selections intact and "Back to preview" is free.
  const [formOpen, setFormOpen] = useState(true)
  const [lastGenerate, setLastGenerate] = useState<LastGenerate | null>(null)
  const [publishOpen, setPublishOpen] = useState(false)
  const [exportBusy, setExportBusy] = useState(false)

  // Live-load Google Fonts when the theme picks them. This is for the tool's own
  // brand/theme swatches; the preview frame links its own copy (PagePreview).
  const allGoogleFonts = useMemo(
    () => [...(googleFonts || []), ...(site?.google_fonts || [])],
    [googleFonts, site?.google_fonts],
  )
  useEffect(() => {
    if (allGoogleFonts.length === 0) return
    const params = allGoogleFonts.map((f) => `family=${f.replace(/ /g, '+')}`).join('&')
    const href = `${GOOGLE_FONTS_BASE}${params}&display=swap`
    const id = 'webtree-sitegen-google-fonts'
    let link = document.getElementById(id) as HTMLLinkElement | null
    if (!link) {
      link = document.createElement('link')
      link.id = id
      link.rel = 'stylesheet'
      document.head.appendChild(link)
    }
    link.href = href
  }, [allGoogleFonts])

  const selectedPage = useMemo(
    () => site?.pages.find((p) => p.slug === selectedSlug) ?? null,
    [site, selectedSlug],
  )

  // A site with nothing selectable (0 pages) has nothing to preview, so it must
  // not collapse the form — that would strand the user on a blank canvas.
  const stage: 'compose' | 'preview' =
    site && selectedPage && !formOpen ? 'preview' : 'compose'

  /** The source that fed the current site — used for the summary bar and the
   * .docx export, which both outlive the scrape preview. */
  const activeSource = useMemo<SourceContent | null>(
    () =>
      confirmedSource ??
      (lastGenerate ? lastGenerate.payload.source : null) ??
      scrapeResult?.source_content ??
      null,
    [confirmedSource, lastGenerate, scrapeResult],
  )

  function setBrandFromManualUpload(b: BrandIdentity | null) {
    setBrand(b)
    setManualBrand(b != null)
  }

  const QUICK_SCAN_CAP = 20  // matches backend default
  const FULL_CAP_MAX = 40    // backend ceiling

  /**
   * URL-mode flow:
   *   1. probe sitemap (1-3s, no Playwright)
   *   2. if sitemap shows > QUICK_SCAN_CAP pages → open ScopeChoice modal
   *   3. otherwise → kick off the scrape silently with the default cap
   */
  async function handleScrape(
    url: string,
    opts: { crawl: boolean; accessToken?: string } = { crawl: true },
  ) {
    setError(null)
    setScrapeResult(null)
    setPendingScope(null)
    // A Facebook Page has no sitemap and nothing to crawl — probing it would
    // just cost a round trip before the read the user actually asked for.
    if (isFacebookUrl(url)) {
      await runScrape(url, {
        crawl: false,
        maxPages: 0,
        accessToken: opts.accessToken,
      })
      return
    }
    if (!opts.crawl) {
      // Crawl disabled → no need to probe; go direct, single page only.
      await runScrape(url, { crawl: false, maxPages: 0 })
      return
    }
    // Probe phase
    setScrapeBusy(true)
    try {
      const probe = await probeSitemap(url)
      if (probe.has_sitemap && probe.total_urls > QUICK_SCAN_CAP) {
        // Defer scrape until the user picks scope
        setPendingScope({ url, probe })
        setScrapeBusy(false)
        return
      }
    } catch (err) {
      // Probe failure is non-fatal — log and proceed with default cap.
      console.warn('Sitemap probe failed; falling back to default cap', err)
    }
    // No big sitemap (or probe failed) — proceed with default
    await runScrape(url, { crawl: true, maxPages: QUICK_SCAN_CAP })
  }

  /** Actually fire the scrape; uses the async job model + polling so long
   *  crawls don't hang the HTTP request and the user can see progress. */
  async function runScrape(
    url: string,
    opts: { crawl: boolean; maxPages: number; accessToken?: string },
  ) {
    setScrapeBusy(true)
    setError(null)
    setScrapeResult(null)
    setActiveJob(null)
    setActiveJobCap(opts.maxPages || null)
    let started: { job_id: string } | null = null
    try {
      started = await startCrawl(url, {
        crawl: opts.crawl,
        crawlMaxPages: opts.maxPages || undefined,
        accessToken: opts.accessToken,
      })
      // Poll loop. 1s cadence — backend job emits progress per page.
      // Hard ceiling at 10 minutes to avoid runaway loops on stuck jobs.
      const startedAt = Date.now()
      while (Date.now() - startedAt < 10 * 60 * 1000) {
        const job = await getCrawlJob(started.job_id)
        setActiveJob(job)
        if (job.status === 'done') {
          if (job.result) await landPreview(job.result)
          break
        }
        if (job.status === 'failed') {
          setError(job.error || 'Crawl failed')
          break
        }
        if (job.status === 'cancelled') {
          // user cancelled — just clear; no error
          break
        }
        await new Promise((r) => setTimeout(r, 1000))
      }
      // Deliberately NOT deleted here. The backend hands an identical crawl
      // (same URL + options, within the retention window) straight back
      // instead of re-rendering every page, and deleting the row the instant
      // polling finished meant that could never hit — a double-click or a
      // Back-then-Fetch re-crawled the whole site. Rows are swept by the
      // backend on the next kickoff.
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Scrape failed')
    } finally {
      setScrapeBusy(false)
      setActiveJob(null)
      setActiveJobCap(null)
    }
  }

  /**
   * Where every source read lands, whichever reader produced it.
   *
   * If the paste box holds anything, it is merged into what the reader found
   * before the preview is shown — so "the pasted content rides along with the
   * link or the file" is one rule in one place, not one per reader. The merge
   * itself is the backend's (services/paste_source.merge_sources).
   */
  async function landPreview(preview: ScrapePreviewType) {
    let landed = preview
    const text = pastedText.trim()
    if (text) {
      try {
        const pasted = await readPastedContent({
          text,
          base: preview.source_content,
        })
        landed = withPastedContent(preview, pasted)
      } catch (err) {
        // The read itself worked. Keep it and say what the paste didn't do,
        // rather than throwing away a crawl the user waited a minute for.
        setError(
          `Your pasted content couldn't be added: ${
            err instanceof Error ? err.message : 'unknown error'
          }`,
        )
      }
    }
    setScrapeResult(landed)
    if (!brandName && landed.brand_candidate?.name) {
      setBrandName(landed.brand_candidate.name)
    }
  }

  /** Paste-mode submit: the pasted content is the whole source. */
  async function handleReadPaste() {
    const text = pastedText.trim()
    if (!text || pasteBusy) return
    setPasteBusy(true)
    setError(null)
    setScrapeResult(null)
    try {
      const preview = await readPastedContent({ text, title: pasteTitle })
      setScrapeResult(preview)
      // A paste carries no logo to detect a brand from, so its title is the
      // only name we have — seed the Brand panel with it rather than leaving
      // the user to retype what they just typed.
      const name = preview.source_content.title
      if (!brandName && name) setBrandName(name)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read that content')
    } finally {
      setPasteBusy(false)
    }
  }

  async function handleJobCancel() {
    if (!activeJob) return
    try {
      await cancelCrawlJob(activeJob.id)
    } catch (err) {
      console.warn('Cancel failed', err)
    }
  }

  function handleScopeQuick() {
    if (!pendingScope) return
    const url = pendingScope.url
    setPendingScope(null)
    runScrape(url, { crawl: true, maxPages: QUICK_SCAN_CAP })
  }

  function handleScopeFull() {
    if (!pendingScope) return
    const url = pendingScope.url
    setPendingScope(null)
    runScrape(url, {
      crawl: true,
      maxPages: Math.min(pendingScope.probe.total_urls, FULL_CAP_MAX),
    })
  }

  function handleScopeCancel() {
    setPendingScope(null)
  }

  /** Inline "Crawl N more" — resumes BFS from prior result's unvisited frontier. */
  async function handleCrawlMore(maxMore: number) {
    if (!scrapeResult?.unvisited_urls?.length) return
    setExtendBusy(true)
    setError(null)
    try {
      const alreadySeen = [
        scrapeResult.final_url,
        ...(scrapeResult.source_content.discovered_pages?.map((p) => p.source_ref) || []),
      ]
      const ext = await extendCrawl({
        entryUrl: scrapeResult.final_url,
        seedUrls: scrapeResult.unvisited_urls,
        alreadySeen,
        maxMore,
      })
      // Merge: append new discovered pages into existing source_content
      setScrapeResult((prev) => {
        if (!prev) return prev
        const merged: ScrapePreviewType = {
          ...prev,
          source_content: {
            ...prev.source_content,
            discovered_pages: [
              ...(prev.source_content.discovered_pages || []),
              ...ext.additional_pages,
            ],
          },
          discovered_count: (prev.discovered_count ?? 0) + ext.added_count,
          unvisited_urls: ext.unvisited_urls,
          unvisited_count: ext.unvisited_count,
        }
        return merged
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Extend crawl failed')
    } finally {
      setExtendBusy(false)
    }
  }

  /** Doc-mode upload: parse PDF/DOCX → same preview shape as scrape → ScrapePreview. */
  async function handleUpload(file: File) {
    setUploadBusy(true)
    setError(null)
    setScrapeResult(null)
    try {
      await landPreview(await uploadDocumentPreview(file))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Document parse failed')
    } finally {
      setUploadBusy(false)
    }
  }

  function applyScrapedBrand() {
    if (!scrapeResult?.brand_candidate || manualBrand) return
    const candidate = scrapeResult.brand_candidate
    setBrand({ ...candidate, mood })
    setBrandName(candidate.name || brandName)
    // Reset themePreview so BrandPanel can refresh it when needed.
    setThemePreview(null)
  }

  function effectiveBrand(): BrandIdentity | null {
    if (brand) return { ...brand, name: brand.name || brandName || 'Untitled', mood }
    if (brandName) return { name: brandName, extracted_palette: [], mood }
    return null
  }

  /** Land a freshly generated site: show it, and keep the wizard intact behind
   * the summary bar so Adjust/Regenerate cost nothing. */
  function acceptSite(result: GeneratedSite, origin: LastGenerate) {
    setSite(result)
    setLastGenerate(origin)
    // Stay on the same page across a regenerate when it still exists.
    setSelectedSlug((prev) =>
      prev && result.pages.some((p) => p.slug === prev) ? prev : result.pages[0]?.slug ?? null,
    )
    setFormOpen(false)
  }

  /** User confirmed the scrape preview → move to the page picker. */
  function handleScrapeConfirm(editedText: string, title: string) {
    if (!scrapeResult) return
    const updated: SourceContent = {
      ...scrapeResult.source_content,
      raw_text: editedText,
      title: title || scrapeResult.source_content.title,
    }
    setConfirmedSource(updated)
  }

  /** User confirmed page selections → run scaffolded generation. */
  async function handlePagesConfirm() {
    if (!confirmedSource || selectedPages.length === 0) return
    const payload: GenerateWithPagesPayload = {
      source: confirmedSource,
      selected_pages: selectedPages,
      industry: industryOverride || 'other',
      brand: effectiveBrand(),
      mood_override: mood,
      color_scheme_override: colorScheme === 'auto' ? null : colorScheme,
      hero_height: heroHeight === 'auto' ? null : heroHeight,
      stock_images_only: stockImagesOnly,
      detected_brand: detectedBrand,
      // The Page stays the authority on its own contact details, hours,
      // reviews and counts — the backend rewrites those blocks from it after
      // the LLM has run. Riding on the payload means a regenerate keeps it too.
      facebook_facts: scrapeResult?.facebook_facts ?? null,
    }
    setBusy(true)
    setError(null)
    try {
      const result = await generateWithPages(payload)
      acceptSite(result, { kind: 'with-pages', payload })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed')
    } finally {
      setBusy(false)
    }
  }

  /** Re-run the last generation verbatim. The model varies its output run to
   * run, so this is the "give me another take" button. */
  async function handleRegenerate() {
    if (!lastGenerate) return
    setBusy(true)
    setError(null)
    try {
      const result = await generateWithPages(lastGenerate.payload)
      // Keep the previous site on screen until the new one is in hand.
      acceptSite(result, lastGenerate)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Regeneration failed')
      // Reopen the form so the error is visible next to the inputs that caused it.
      setFormOpen(true)
    } finally {
      setBusy(false)
    }
  }

  function handleStartOver() {
    setSite(null)
    setSelectedSlug(null)
    setLastGenerate(null)
    setConfirmedSource(null)
    setSelectedPages([])
    setDetectedBrand(null)
    setScrapeResult(null)
    setPastedText('')
    setPasteTitle('')
    setError(null)
    setFormOpen(true)
  }

  async function handleExport() {
    if (!activeSource) return
    setExportBusy(true)
    setError(null)
    try {
      await exportSiteDocument(activeSource, site?.site_name ?? brandName)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed')
    } finally {
      setExportBusy(false)
    }
  }

  function backToSource() {
    setConfirmedSource(null)
    setSelectedPages([])
    setDetectedBrand(null)
  }

  if (stage === 'preview' && site && selectedPage) {
    return (
      <div className="flex h-screen flex-col overflow-hidden bg-canvas">
        <WorkspaceHeader />
        <SiteSummaryBar
          site={site}
          sourceLabel={sourceLabel(activeSource)}
          canRegenerate={lastGenerate !== null}
          busy={busy}
          exportBusy={exportBusy}
          onRegenerate={handleRegenerate}
          onAdjust={() => setFormOpen(true)}
          onPublish={() => setPublishOpen(true)}
          onStartOver={handleStartOver}
          onExport={handleExport}
        />

        {error && (
          <div className="px-4 pt-3 sm:px-6">
            <Banner tone="danger" title="Something went wrong">
              {error}
            </Banner>
          </div>
        )}

        <main className="flex min-h-0 flex-1">
          <aside className="scrollbar-slim hidden w-56 shrink-0 overflow-y-auto border-r border-line bg-surface p-2 lg:block">
            <SectionLabel className="px-2.5 pb-1.5 pt-2">Pages</SectionLabel>
            <PageList
              pages={site.pages}
              selectedSlug={selectedSlug}
              onSelect={setSelectedSlug}
            />
          </aside>
          <section className="flex min-w-0 flex-1 flex-col overflow-hidden p-3 sm:p-4">
            <PagePreview
              page={selectedPage}
              site={site}
              mediaCredits={site.media_credits}
              onNavigate={setSelectedSlug}
              regenerating={busy}
            />
          </section>
        </main>

        <PublishDrawer
          open={publishOpen}
          onClose={() => setPublishOpen(false)}
          site={site}
        />
      </div>
    )
  }

  const steps: Step[] = [
    {
      id: 'brand',
      label: 'Brand',
      detail: brand || brandName ? brandName || brand?.name || 'Set' : 'Optional',
      status: brand || brandName ? 'done' : 'upcoming',
    },
    {
      id: 'source',
      label: 'Source',
      detail: confirmedSource
        ? sourceLabel(confirmedSource) ?? 'Confirmed'
        : mode === 'url'
          ? 'Paste a website or Facebook link'
          : mode === 'document'
            ? 'Upload a document'
            : 'Paste your content',
      status: confirmedSource ? 'done' : 'current',
      onClick: confirmedSource ? backToSource : undefined,
    },
    {
      id: 'pages',
      label: 'Pages',
      detail: confirmedSource
        ? `${selectedPages.length} selected`
        : 'Chosen after the source is ready',
      status: confirmedSource ? 'current' : 'upcoming',
    },
  ]

  const idle = !confirmedSource && !scrapeResult && !activeJob && !pendingScope

  return (
    <div className="min-h-screen bg-canvas">
      <WorkspaceHeader />

      {site && (
        <div className="sticky top-14 z-20 border-b border-line bg-surface/85 px-4 py-2.5 backdrop-blur sm:px-6">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-3">
            <p className="min-w-0 truncate text-xs text-ink-muted">
              Editing the recipe for <span className="font-medium text-ink">{site.site_name}</span>.
              Nothing changes until you generate again.
            </p>
            <Button size="sm" onClick={() => setFormOpen(false)}>
              ← Back to preview
            </Button>
          </div>
        </div>
      )}

      <main className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            {site ? 'Adjust and regenerate' : 'Generate a website'}
          </h1>
          <p className="mt-1 text-sm text-ink-muted">
            Point us at a site or a document. We extract the content and brand, you choose
            the pages, and the theme is built from your logo's palette and mood — applied
            across every page, header and footer.
          </p>
        </div>

        <Stepper steps={steps} className="mt-6" />

        <div className="mt-6 space-y-5">
          <section>
            <SectionLabel>1 · Brand</SectionLabel>
            <div className="mt-2 rounded-2xl border border-line bg-surface p-5 shadow-card">
              <BrandPanel
                brandName={brandName}
                onBrandNameChange={setBrandName}
                brand={brand}
                setBrand={setBrandFromManualUpload}
                themePreview={themePreview}
                setThemePreview={setThemePreview}
                googleFonts={googleFonts}
                setGoogleFonts={setGoogleFonts}
                mood={mood}
                setMood={setMood}
                colorScheme={colorScheme}
                setColorScheme={setColorScheme}
                heroHeight={heroHeight}
                setHeroHeight={setHeroHeight}
                stockImagesOnly={stockImagesOnly}
                setStockImagesOnly={setStockImagesOnly}
              />
            </div>
          </section>

          <section>
            <SectionLabel>{confirmedSource ? '3 · Pages' : '2 · Source'}</SectionLabel>
            <div className="mt-2 space-y-3">
              {idle && (
                <ModeTabs
                  mode={mode}
                  onChange={(m) => {
                    setMode(m)
                    setScrapeResult(null)
                    setError(null)
                  }}
                />
              )}
              <div className="rounded-2xl border border-line bg-surface p-5 shadow-card">
                {confirmedSource ? (
                  <PagePicker
                    source={confirmedSource}
                    industryOverride={industryOverride}
                    setIndustryOverride={setIndustryOverride}
                    selectedPages={selectedPages}
                    setSelectedPages={setSelectedPages}
                    setDetectedBrand={setDetectedBrand}
                    onConfirm={handlePagesConfirm}
                    onBack={backToSource}
                    busy={busy}
                    singlePage={isSinglePageSource(confirmedSource)}
                    homepageSections={scrapeResult?.facebook_sections}
                  />
                ) : scrapeResult ? (
                  <ScrapePreview
                    preview={scrapeResult}
                    hasManualBrand={manualBrand}
                    onApplyBrand={applyScrapedBrand}
                    onDiscard={() => setScrapeResult(null)}
                    onConfirm={handleScrapeConfirm}
                    onCrawlMore={handleCrawlMore}
                    extendBusy={extendBusy}
                    busy={busy}
                  />
                ) : activeJob ? (
                  <CrawlProgress
                    job={activeJob}
                    onCancel={handleJobCancel}
                    pagesCap={activeJobCap ?? undefined}
                  />
                ) : pendingScope ? (
                  <ScopeChoice
                    url={pendingScope.url}
                    probe={pendingScope.probe}
                    quickCap={QUICK_SCAN_CAP}
                    onQuick={handleScopeQuick}
                    onFull={handleScopeFull}
                    onCancel={handleScopeCancel}
                  />
                ) : (
                  <SourcePanel
                    mode={mode}
                    onScrape={handleScrape}
                    onUpload={handleUpload}
                    onReadPaste={handleReadPaste}
                    pastedText={pastedText}
                    onPastedTextChange={setPastedText}
                    pasteTitle={pasteTitle}
                    onPasteTitleChange={setPasteTitle}
                    scrapeBusy={scrapeBusy}
                    uploadBusy={uploadBusy}
                    pasteBusy={pasteBusy}
                  />
                )}
                {error && (
                  <Banner tone="danger" className="mt-4" title="Something went wrong">
                    {error}
                  </Banner>
                )}
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  )
}

/**
 * True when the source carries one page's worth of grounded facts, so the page
 * picker should default to home + legal instead of the industry template's
 * fan-out.
 *
 * A Facebook Page always is. A document is when `split_into_pages` found no
 * page-topic headings and handed back no `discovered_pages`: `infer_page_scaffolds`
 * then falls into the industry-template branch and returns 7-9 scaffolds, 5-7 of
 * them content pages that each cost an LLM call to invent material the document
 * never described — which the fidelity net strips back to almost nothing anyway.
 * A document that DID yield discovered pages keeps its fan-out, and the picker's
 * optional pool still lets the user add pages back either way.
 */
function isSinglePageSource(source: SourceContent): boolean {
  if (source.source_kind === 'url') return false
  // True for a Page, a document or a paste that yielded no pages of its own.
  // A Facebook Page is one page's worth of facts — unless pasted content added
  // pages to it, which is exactly what the paste box is for.
  return (source.discovered_pages?.length ?? 0) === 0
}

/** A short human label for where the content came from — host for scrapes,
 * filename for uploads. */
function sourceLabel(source: SourceContent | null): string | null {
  if (!source) return null
  const ref = source.source_ref || ''
  // A Facebook ref's host is always "facebook.com", which tells the user
  // nothing — the Page's own name is the identifying part.
  if (source.source_kind === 'facebook') return source.title || 'Facebook Page'
  if (!ref) return source.title || null
  if (/^https?:\/\//i.test(ref)) {
    try {
      return new URL(ref).host.replace(/^www\./, '')
    } catch {
      return ref
    }
  }
  return ref.split(/[\\/]/).pop() || ref
}
