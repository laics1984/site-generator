import { useEffect, useMemo, useState } from 'react'

import { BrandPanel } from '@/components/BrandPanel'
import { CmsPushPanel } from '@/components/CmsPushPanel'
import { CrawlProgress } from '@/components/CrawlProgress'
import { ModeTabs } from '@/components/ModeTabs'
import { OllamaStatus } from '@/components/OllamaStatus'
import { PageList } from '@/components/PageList'
import { PagePicker } from '@/components/PagePicker'
import { PagePreview } from '@/components/PagePreview'
import { ScopeChoice } from '@/components/ScopeChoice'
import { ScrapePreview } from '@/components/ScrapePreview'
import { SourcePanel } from '@/components/SourcePanel'
import {
  cancelCrawlJob,
  deleteCrawlJob,
  extendCrawl,
  generateFromSource,
  generateWithPages,
  getCrawlJob,
  probeSitemap,
  startCrawl,
  uploadDocumentPreview,
} from '@/lib/api'
import type {
  BrandIdentity,
  BrandMood,
  ColorSchemeChoice,
  BuilderStylesShape,
  CrawlJob,
  DetectedBrand,
  GeneratedSite,
  GeneratorMode,
  IndustryCategory,
  PageScaffold,
  ScrapePreview as ScrapePreviewType,
  SitemapProbeResult,
  SourceContent,
} from '@/lib/types'

const GOOGLE_FONTS_BASE = 'https://fonts.googleapis.com/css2?'

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
  const [themePreview, setThemePreview] = useState<BuilderStylesShape | null>(null)
  const [googleFonts, setGoogleFonts] = useState<string[]>([])

  // Scrape state (URL mode) + upload state (Doc mode). Both flow into the
  // same `scrapeResult` slot so ScrapePreview renders either source kind.
  const [scrapeBusy, setScrapeBusy] = useState(false)
  const [uploadBusy, setUploadBusy] = useState(false)
  const [scrapeResult, setScrapeResult] = useState<ScrapePreviewType | null>(null)
  // Scope-choice modal state. Set after a successful sitemap probe that
  // reveals more pages than our quick-scan default (20).
  const [pendingScope, setPendingScope] = useState<{
    url: string
    probe: SitemapProbeResult
  } | null>(null)
  // Wall-clock label for the inline "Crawl more" step.
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

  // Live-load Google Fonts when the theme picks them.
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
    opts: { crawl: boolean } = { crawl: true },
  ) {
    setError(null)
    setScrapeResult(null)
    setPendingScope(null)
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
    opts: { crawl: boolean; maxPages: number },
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
      })
      // Poll loop. 1s cadence — backend job emits progress per page.
      // Hard ceiling at 10 minutes to avoid runaway loops on stuck jobs.
      const startedAt = Date.now()
      while (Date.now() - startedAt < 10 * 60 * 1000) {
        const job = await getCrawlJob(started.job_id)
        setActiveJob(job)
        if (job.status === 'done') {
          if (job.result) {
            setScrapeResult(job.result)
            if (!brandName && job.result.brand_candidate?.name) {
              setBrandName(job.result.brand_candidate.name)
            }
          }
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
      // Best-effort cleanup of the job row.
      if (started) deleteCrawlJob(started.job_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Scrape failed')
    } finally {
      setScrapeBusy(false)
      setActiveJob(null)
      setActiveJobCap(null)
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
      const result = await uploadDocumentPreview(file)
      setScrapeResult(result)
      if (!brandName && result.brand_candidate?.name) {
        setBrandName(result.brand_candidate.name)
      }
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

  /** Legacy free-form generate (doc paste path — no page picker). */
  async function handleGenerateFreeform(source: SourceContent) {
    setBusy(true)
    setError(null)
    try {
      const result = await generateFromSource({
        source,
        brand: effectiveBrand(),
        mood_override: mood,
        color_scheme_override: colorScheme === 'auto' ? null : colorScheme,
      })
      setSite(result)
      setSelectedSlug(result.pages[0]?.slug ?? null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed')
    } finally {
      setBusy(false)
    }
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
    setBusy(true)
    setError(null)
    try {
      const result = await generateWithPages({
        source: confirmedSource,
        selected_pages: selectedPages,
        industry: industryOverride || 'other',
        brand: effectiveBrand(),
        mood_override: mood,
        color_scheme_override: colorScheme === 'auto' ? null : colorScheme,
        detected_brand: detectedBrand,
      })
      setSite(result)
      setSelectedSlug(result.pages[0]?.slug ?? null)
      // Reset wizard for next run
      setConfirmedSource(null)
      setSelectedPages([])
      setScrapeResult(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600 text-white font-bold">
              W
            </div>
            <div>
              <div className="text-sm font-semibold text-slate-900">
                Webtree Site Generator
              </div>
              <div className="text-xs text-slate-500">
                Local · AI-powered · Builder-compatible
              </div>
            </div>
          </div>
          <OllamaStatus />
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="grid gap-8 lg:grid-cols-[460px_1fr]">
          <section className="space-y-6">
            <div>
              <h1 className="text-lg font-semibold text-slate-900">Generate</h1>
              <p className="mt-1 text-sm text-slate-600">
                Brand → Source → Generate. The theme is built from your logo's palette
                and mood, applied across every page, header, and footer.
              </p>
            </div>

            <div>
              <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-500">
                1 · Brand
              </h2>
              <div className="mt-2 rounded-2xl border border-slate-200 bg-white p-5">
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
                />
              </div>
            </div>

            <div>
              <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-500">
                2 · Source
              </h2>
              <div className="mt-2 space-y-3">
                <ModeTabs
                  mode={mode}
                  onChange={(m) => {
                    setMode(m)
                    setScrapeResult(null)
                    setError(null)
                  }}
                />
                <div className="rounded-2xl border border-slate-200 bg-white p-5">
                  {confirmedSource ? (
                    <PagePicker
                      source={confirmedSource}
                      industryOverride={industryOverride}
                      setIndustryOverride={setIndustryOverride}
                      selectedPages={selectedPages}
                      setSelectedPages={setSelectedPages}
                      setDetectedBrand={setDetectedBrand}
                      onConfirm={handlePagesConfirm}
                      onBack={() => {
                        setConfirmedSource(null)
                        setSelectedPages([])
                        setDetectedBrand(null)
                      }}
                      busy={busy}
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
                      busy={busy}
                      onScrape={handleScrape}
                      onUpload={handleUpload}
                      onGenerate={handleGenerateFreeform}
                      scrapeBusy={scrapeBusy}
                      uploadBusy={uploadBusy}
                    />
                  )}
                  {error && (
                    <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
                      {error}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </section>

          <section>
            {!site ? (
              <EmptyResultState />
            ) : (
              <div className="grid gap-6 lg:grid-cols-[240px_1fr]">
                <aside className="space-y-4">
                  <SiteSummary site={site} />
                  <div className="rounded-2xl border border-slate-200 bg-white p-3">
                    <div className="px-2 pb-2 pt-1 text-xs font-semibold uppercase tracking-wider text-slate-500">
                      Pages
                    </div>
                    <PageList
                      pages={site.pages}
                      selectedSlug={selectedSlug}
                      onSelect={setSelectedSlug}
                    />
                  </div>
                  <CmsPushPanel site={site} />
                </aside>
                {selectedPage && (
                  <PagePreview
                    page={selectedPage}
                    mediaCredits={site.media_credits}
                    site={site}
                  />
                )}
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  )
}

function SiteSummary({ site }: { site: GeneratedSite }) {
  const colors = site.builder_styles?.colors
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        {site.brand?.mood || 'modern'} · theme
      </div>
      <div className="mt-1 text-base font-semibold text-slate-900">{site.site_name}</div>
      {site.tagline && <div className="text-xs text-slate-500">{site.tagline}</div>}
      {colors && (
        <div className="mt-3 grid grid-cols-4 gap-1.5">
          {(['primary', 'secondary', 'accent', 'surface'] as const).map((key) => (
            <div key={key} className="flex flex-col items-stretch">
              <div
                className="h-6 w-full rounded border border-slate-200"
                style={{ backgroundColor: colors[key] }}
                title={`${key}: ${colors[key]}`}
              />
              <div className="mt-0.5 text-[10px] font-medium text-slate-500 capitalize">
                {key}
              </div>
            </div>
          ))}
        </div>
      )}
      {site.builder_styles && (
        <div className="mt-3 text-[11px] text-slate-500">
          <div>
            <span className="font-medium text-slate-600">Heading:</span>{' '}
            {site.builder_styles.typography.headingFont.split(',')[0].replace(/"/g, '')}
          </div>
          <div>
            <span className="font-medium text-slate-600">Radius:</span>{' '}
            {site.builder_styles.buttons.radius}px
          </div>
        </div>
      )}
    </div>
  )
}

function EmptyResultState() {
  return (
    <div className="flex h-full min-h-[400px] items-center justify-center rounded-2xl border-2 border-dashed border-slate-200 bg-white p-8 text-center">
      <div>
        <div className="text-base font-semibold text-slate-900">
          Your generated site appears here
        </div>
        <p className="mx-auto mt-2 max-w-sm text-sm text-slate-600">
          Paste a URL to scrape, or upload a document. We extract content + brand,
          show you a preview, then produce a fully themed multi-page site.
        </p>
      </div>
    </div>
  )
}
