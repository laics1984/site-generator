import { PalettePanel } from './components/PalettePanel'
import { useEffect, useMemo, useState } from 'react'

import { BrandPanel } from '@/components/BrandPanel'
import { CrawlProgress } from '@/components/CrawlProgress'
import { PageList } from '@/components/PageList'
import { PagePicker } from '@/components/PagePicker'
import { PagePreview } from '@/components/PagePreview'
import { PublishDrawer } from '@/components/PublishDrawer'
import { ScopeChoice } from '@/components/ScopeChoice'
import { fullCrawlCap } from '@/lib/crawlScope'
import { ScrapePreview } from '@/components/ScrapePreview'
import { SiteSummaryBar } from '@/components/SiteSummaryBar'
import { SourcePanel } from '@/components/SourcePanel'
import { WorkspaceHeader } from '@/components/WorkspaceHeader'
import {
  cancelCrawlJob,
  exportSiteDocument,
  extendCrawl,
  generateWithPages,
  generateRemaining,
  listGenerationProjects,
  getCrawlJob,
  mergeSourceContents,
  probeSitemap,
  readPastedContent,
  startCrawl,
  uploadDocumentPreview,
  type GenerateWithPagesPayload,
} from '@/lib/api'
import { companionPageUrl, isFacebookUrl } from '@/lib/sourceDetect'
import { combineSourcePreviews } from '@/lib/sourceCombine'
import { withPastedContent } from '@/lib/sourcePaste'
import type {
  BrandIdentity,
  BrandMoodChoice,
  ColorSchemeChoice,
  BuilderStylesShape,
  CrawlJob,
  DetectedBrand,
  GeneratedSite,
  GenerationProjectSummary,
  HeroHeightChoice,
  IndustryCategory,
  MultilingualChoice,
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
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Brand state — populated either manually (BrandPanel upload) or auto-detected
  // from a scrape. `manualBrand` tracks whether the user uploaded a logo — manual
  // ALWAYS wins per the agreed precedence.
  const [brand, setBrand] = useState<BrandIdentity | null>(null)
  const [manualBrand, setManualBrand] = useState(false)
  const [brandName, setBrandName] = useState('')
  // 'auto' leaves mood to the backend (industry lock, then detection); a
  // clicked mood is the user's and wins everywhere it is sent.
  const [mood, setMood] = useState<BrandMoodChoice>('auto')
  const chosenMood = mood === 'auto' ? null : mood
  // 'auto' → send null so the backend decides from the logo (light logo ⇒ dark).
  const [colorScheme, setColorScheme] = useState<ColorSchemeChoice>('auto')
  // Hero photo-background height, site-wide. 'full' = full-screen hero (default).
  const [heroHeight, setHeroHeight] = useState<HeroHeightChoice>('auto')
  // Dress the whole site in Pexels stock and ignore the source's own photos.
  const [stockImagesOnly, setStockImagesOnly] = useState(false)
  const [themePreview, setThemePreview] = useState<BuilderStylesShape | null>(null)
  const [googleFonts, setGoogleFonts] = useState<string[]>([])

  // The three source fields, all optional and independent. Lifted out of
  // SourcePanel (rather than living as its local state) so a failed submit
  // returns the user to a fully-repopulated form instead of an empty one —
  // SourcePanel unmounts while CrawlProgress/ScopeChoice is on screen, so
  // local state there would be lost the moment a crawl needs to pause or
  // fails.
  const [sourceUrl, setSourceUrl] = useState('')
  // A website's Facebook Page, read alongside it (decision Q3,
  // docs/design-identity-plan.md). Only offered when the main link is a website.
  const [sourceFbUrl, setSourceFbUrl] = useState('')
  const [sourceCrawl, setSourceCrawl] = useState(true)
  const [sourceFbToken, setSourceFbToken] = useState('')
  const [sourceFile, setSourceFile] = useState<File | null>(null)
  const [pastedText, setPastedText] = useState('')
  const [pasteTitle, setPasteTitle] = useState('')

  // Busy per leg — a submit may run the crawl and the document parse
  // concurrently, then the paste merge after both land.
  const [scrapeBusy, setScrapeBusy] = useState(false)
  const [uploadBusy, setUploadBusy] = useState(false)
  const [pasteBusy, setPasteBusy] = useState(false)
  const [scrapeResult, setScrapeResult] = useState<ScrapePreviewType | null>(null)
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
  const [generationMode, setGenerationMode] = useState<'full' | 'two_page_preview'>('full')
  const [previewSecondSlug, setPreviewSecondSlug] = useState<string | null>(null)
  const [savedProjects, setSavedProjects] = useState<GenerationProjectSummary[]>([])
  const [industryOverride, setIndustryOverride] = useState<IndustryCategory | null>(null)
  // How to present the source's languages (services/multilingual_strategy.py).
  const [multilingual, setMultilingual] = useState<MultilingualChoice>({ mode: 'auto', locales: [] })
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
  const [paletteOnly, setPaletteOnly] = useState(false)
  const [exportBusy, setExportBusy] = useState(false)

  function refreshProjects() {
    listGenerationProjects().then(setSavedProjects).catch(() => undefined)
  }
  useEffect(refreshProjects, [])

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

  type UrlPlan = { crawl: boolean; maxPages: number; accessToken?: string }

  /** Fire the crawl job and poll it to completion; returns the landed result,
   *  or null on failure/cancel (having already set `error` on failure). Uses
   *  the async job model so long crawls don't hang the HTTP request and the
   *  user can see progress. */
  async function runScrape(
    url: string,
    opts: UrlPlan,
  ): Promise<ScrapePreviewType | null> {
    setScrapeBusy(true)
    setActiveJob(null)
    setActiveJobCap(opts.maxPages || null)
    try {
      const started = await startCrawl(url, {
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
        if (job.status === 'done') return job.result
        if (job.status === 'failed') {
          setError(job.error || 'Crawl failed')
          return null
        }
        if (job.status === 'cancelled') return null // user cancelled — no error
        await new Promise((r) => setTimeout(r, 1000))
      }
      return null
      // Deliberately NOT deleted here. The backend hands an identical crawl
      // (same URL + options, within the retention window) straight back
      // instead of re-rendering every page, and deleting the row the instant
      // polling finished meant that could never hit — a double-click or a
      // Back-then-Fetch re-crawled the whole site. Rows are swept by the
      // backend on the next kickoff.
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Scrape failed')
      return null
    } finally {
      setScrapeBusy(false)
      setActiveJob(null)
      setActiveJobCap(null)
    }
  }

  /**
   * Fold every read that landed — website, its Facebook Page, document — into
   * one SourceContent, base first, then layer the paste on top: one rule for
   * "everything the user provided feeds one context", however many were
   * filled in.
   *
   * Each addition joins through the backend merge
   * (services/source_merge.merge_sources, which also reports the facts two
   * sources disagree on), and the base outranks it (lib/sourceCombine.ts). The
   * paste always merges last, through the same `base` mechanism a paste has
   * always used.
   */
  async function composeSources(
    reads: Array<{ preview: ScrapePreviewType | null; label: string }>,
  ): Promise<ScrapePreviewType | null> {
    let rich: ScrapePreviewType | null = null
    for (const { preview, label } of reads) {
      if (!preview) continue
      if (!rich) {
        rich = preview
        continue
      }
      try {
        const merged = await mergeSourceContents(rich.source_content, preview.source_content)
        rich = combineSourcePreviews(rich, preview, merged)
      } catch (err) {
        // The earlier reads worked — keep them rather than losing everything
        // because one addition couldn't be folded in.
        setError(
          `Your ${label} couldn't be combined with the rest: ${
            err instanceof Error ? err.message : 'unknown error'
          }`,
        )
      }
    }

    const text = pastedText.trim()
    if (!text) return rich

    setPasteBusy(true)
    try {
      const pasted = await readPastedContent({
        text,
        title: pasteTitle,
        base: rich?.source_content ?? null,
      })
      return rich ? withPastedContent(rich, pasted) : pasted
    } catch (err) {
      if (!rich) {
        setError(err instanceof Error ? err.message : 'Could not read that content')
        return null
      }
      // The other reads worked. Keep them and say what the paste didn't do,
      // rather than throwing away a crawl the user waited a minute for.
      setError(
        `Your pasted content couldn't be added: ${
          err instanceof Error ? err.message : 'unknown error'
        }`,
      )
      return rich
    } finally {
      setPasteBusy(false)
    }
  }

  /** Runs the URL reads (if planned) and the document parse (if a file is
   *  staged) concurrently, combines whatever lands, then hands the result to
   *  `scrapeResult`. What every Continue click — and a resumed ScopeChoice —
   *  ultimately calls.
   *
   *  A website and its Facebook Page are read one after the other, not side by
   *  side: both are crawl jobs and share the one progress display, and the
   *  Page's own read is short beside a crawl. */
  async function runComposed(urlPlan: UrlPlan | null) {
    setError(null)
    setScrapeResult(null)

    const file = sourceFile
    if (file) setUploadBusy(true)
    const pageUrl = companionPageUrl(sourceUrl, sourceFbUrl)

    async function readUrls(): Promise<[ScrapePreviewType | null, ScrapePreviewType | null]> {
      const website = urlPlan ? await runScrape(sourceUrl.trim(), urlPlan) : null
      const page = pageUrl
        ? await runScrape(pageUrl, { crawl: false, maxPages: 0, accessToken: sourceFbToken })
        : null
      return [website, page]
    }

    const [[urlPreview, pagePreview], docPreview] = await Promise.all([
      readUrls(),
      file
        ? uploadDocumentPreview(file).catch((err) => {
            setError(err instanceof Error ? err.message : 'Document parse failed')
            return null
          })
        : Promise.resolve(null),
    ])
    if (file) setUploadBusy(false)

    const landed = await composeSources([
      { preview: urlPreview, label: 'website' },
      { preview: pagePreview, label: 'Facebook Page' },
      { preview: docPreview, label: 'document' },
    ])
    if (!landed) return
    setScrapeResult(landed)
    if (!brandName && landed.brand_candidate?.name) {
      setBrandName(landed.brand_candidate.name)
    }
  }

  /**
   * The Continue button. Probes the sitemap when a URL was given — pausing on
   * ScopeChoice for a large site, exactly as before — then runs the composed
   * read. A blank URL skips probing entirely, so Document-only, Paste-only
   * and every other combination fall out of this for free.
   */
  async function handleComposeSubmit() {
    setPendingScope(null)
    const url = sourceUrl.trim()
    if (!url) {
      await runComposed(null)
      return
    }
    // A Facebook Page has no sitemap and nothing to crawl — probing it would
    // just cost a round trip before the read the user actually asked for.
    if (isFacebookUrl(url)) {
      await runComposed({ crawl: false, maxPages: 0, accessToken: sourceFbToken })
      return
    }
    if (!sourceCrawl) {
      // Crawl disabled → no need to probe; go direct, single page only.
      await runComposed({ crawl: false, maxPages: 0 })
      return
    }
    setScrapeBusy(true)
    try {
      const probe = await probeSitemap(url)
      if (probe.has_sitemap && probe.total_urls > QUICK_SCAN_CAP) {
        // Defer the read until the user picks scope.
        setPendingScope({ url, probe })
        setScrapeBusy(false)
        return
      }
    } catch (err) {
      // Probe failure is non-fatal — log and proceed with default cap.
      console.warn('Sitemap probe failed; falling back to default cap', err)
    }
    // No big sitemap (or probe failed) — proceed with default
    await runComposed({ crawl: true, maxPages: QUICK_SCAN_CAP })
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
    setPendingScope(null)
    runComposed({ crawl: true, maxPages: QUICK_SCAN_CAP })
  }

  function handleScopeFull() {
    if (!pendingScope) return
    const maxPages = fullCrawlCap(pendingScope.probe)
    setPendingScope(null)
    runComposed({ crawl: true, maxPages })
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
          crawl_stop_reason: ext.crawl_stop_reason,
        }
        return merged
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Extend crawl failed')
    } finally {
      setExtendBusy(false)
    }
  }

  function applyScrapedBrand() {
    if (!scrapeResult?.brand_candidate || manualBrand) return
    const candidate = scrapeResult.brand_candidate
    setBrand({ ...candidate, mood: chosenMood })
    setBrandName(candidate.name || brandName)
    // Reset themePreview so BrandPanel can refresh it when needed.
    setThemePreview(null)
  }

  function effectiveBrand(): BrandIdentity | null {
    const candidate = scrapeResult?.source_content.source_kind === 'facebook'
      ? scrapeResult.brand_candidate
      : null
    const resolved = brand?.logo_url || brand?.logo_data_url || manualBrand
      ? brand
      : candidate ?? brand
    if (resolved) return { ...resolved, name: brandName || resolved.name || 'Untitled', mood: chosenMood }
    if (brandName) return { name: brandName, extracted_palette: [], mood: chosenMood }
    return null
  }

  /** Land a freshly generated site: show it, and keep the wizard intact behind
   * the summary bar so Adjust/Regenerate cost nothing. */
  function acceptSite(result: GeneratedSite, origin: LastGenerate | null) {
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
      generation_mode: generationMode,
      preview_second_slug: generationMode === 'two_page_preview'
        ? previewSecondSlug ?? selectedPages.find((p) => !p.is_homepage && !p.is_legal)?.slug ?? null
        : null,
      industry: industryOverride || 'other',
      brand: effectiveBrand(),
      mood_override: chosenMood,
      color_scheme_override: colorScheme === 'auto' ? null : colorScheme,
      hero_height: heroHeight === 'auto' ? null : heroHeight,
      stock_images_only: stockImagesOnly,
      detected_brand: detectedBrand,
      ...(site?.builder_styles?.colorSystemVersion === 2 ? { palette_override: { primary: site.builder_styles.colors.primary, secondary: site.builder_styles.colors.secondary, accent: site.builder_styles.colors.accent, surface: site.builder_styles.colors.surface } } : {}),
      // The Page stays the authority on its own contact details, hours,
      // reviews and counts — the backend rewrites those blocks from it after
      // the LLM has run. Riding on the payload means a regenerate keeps it too.
      facebook_facts: scrapeResult?.facebook_facts ?? null,
      multilingual,
    }
    setBusy(true)
    setError(null)
    try {
      const result = await generateWithPages(payload)
      acceptSite(result, { kind: 'with-pages', payload })
      if (result.generation_project_id) refreshProjects()
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
      const colors = site?.builder_styles?.colorSystemVersion === 2 ? site.builder_styles.colors : null
      const payload = { ...lastGenerate.payload, ...(colors ? { palette_override: { primary: colors.primary, secondary: colors.secondary, accent: colors.accent, surface: colors.surface } } : {}) }
      const result = await generateWithPages(payload)
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
    setGenerationMode('full')
    setPreviewSecondSlug(null)
    setDetectedBrand(null)
    setMultilingual({ mode: 'auto', locales: [] })
    setScrapeResult(null)
    setSourceUrl('')
    setSourceCrawl(true)
    setSourceFbToken('')
    setSourceFile(null)
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

  async function handleGenerateRemaining(projectId: string) {
    setBusy(true)
    setError(null)
    try {
      const result = await generateRemaining(projectId)
      acceptSite(result, null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not generate the remaining pages')
    } finally {
      setBusy(false)
    }
  }

  function backToSource() {
    setConfirmedSource(null)
    setSelectedPages([])
    setDetectedBrand(null)
    setMultilingual({ mode: 'auto', locales: [] })
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
          onPublish={() => { setPaletteOnly(false); setPublishOpen(true) }}
          onGenerateRemaining={
            site.generation_mode === 'two_page_preview' &&
            savedProjects.some((project) => project.id === site.generation_project_id && project.status === 'preview_published')
              ? () => handleGenerateRemaining(site.generation_project_id!) : undefined
          }
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

        {!!site.generation_warnings?.length && (
          <div className="px-4 pt-3 sm:px-6">
            <Banner tone="warn" title="Menu images need review">
              {site.generation_warnings.map((warning, index) => <p key={index}>{warning}</p>)}
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
            {site.builder_styles?.colorSystemVersion === 2 && <PalettePanel site={site} onChange={setSite} onPublish={() => { setPaletteOnly(true); setPublishOpen(true) }} />}
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
          key={`${site.generation_mode ?? 'full'}:${site.generation_project_id ?? 'new'}:${paletteOnly}`}
          open={publishOpen}
          onClose={() => setPublishOpen(false)}
          site={site}
          onPublished={refreshProjects}
          paletteOnly={paletteOnly}
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
        : 'Add a website, a document, or paste your content',
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
        {savedProjects.filter((project) => project.status === 'preview_published').length > 0 && !site && (
          <div className="mb-5 rounded-xl border border-brand-200 bg-brand-50 p-4">
            <div className="text-sm font-semibold text-brand-900">Continue a two-page preview</div>
            <div className="mt-2 space-y-2">
              {savedProjects.filter((project) => project.status === 'preview_published').map((project) => (
                <div key={project.id} className="flex items-center justify-between gap-3 text-xs">
                  <span className="truncate text-brand-900">{project.public_url || project.entity_id || project.id}</span>
                  <Button size="sm" busy={busy} onClick={() => handleGenerateRemaining(project.id)}>Generate remaining pages</Button>
                </div>
              ))}
            </div>
          </div>
        )}
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            {site ? 'Adjust and regenerate' : 'Generate a website'}
          </h1>
          <p className="mt-1 text-sm text-ink-muted">
            Point us at a site, a document, your own text — any combination. We extract the
            content and brand, you choose the pages, and the theme is built from your logo's
            palette and mood — applied across every page, header and footer.
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
              <div className="rounded-2xl border border-line bg-surface p-5 shadow-card">
                {confirmedSource ? (
                  <PagePicker
                    source={confirmedSource}
                    industryOverride={industryOverride}
                    setIndustryOverride={setIndustryOverride}
                    multilingual={multilingual}
                    setMultilingual={setMultilingual}
                    selectedPages={selectedPages}
                    setSelectedPages={setSelectedPages}
                    generationMode={generationMode}
                    setGenerationMode={setGenerationMode}
                    previewSecondSlug={previewSecondSlug}
                    setPreviewSecondSlug={setPreviewSecondSlug}
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
                    url={sourceUrl}
                    onUrlChange={setSourceUrl}
                    pageUrl={sourceFbUrl}
                    onPageUrlChange={setSourceFbUrl}
                    crawl={sourceCrawl}
                    onCrawlChange={setSourceCrawl}
                    fbToken={sourceFbToken}
                    onFbTokenChange={setSourceFbToken}
                    file={sourceFile}
                    onFileChange={setSourceFile}
                    pastedText={pastedText}
                    onPastedTextChange={setPastedText}
                    pasteTitle={pasteTitle}
                    onPasteTitleChange={setPasteTitle}
                    onSubmit={handleComposeSubmit}
                    busy={scrapeBusy || uploadBusy || pasteBusy}
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
