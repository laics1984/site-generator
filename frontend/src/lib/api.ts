import type {
  BrandExtractionResult,
  BrandIdentity,
  BrandMood,
  ColorScheme,
  CmsConnectionTest,
  CmsPushReport,
  CrawlJob,
  DetectedBrand,
  ExtendCrawlResult,
  GeneratedSite,
  HeroHeight,
  IndustryCategory,
  PageRecipeResponse,
  PageScaffold,
  PreviewLayout,
  ScrapePreview,
  SitemapProbeResult,
  SourceContent,
} from '@/lib/types'

const API_BASE = ''

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(`${response.status} ${response.statusText}: ${text}`)
  }
  return (await response.json()) as T
}

export async function checkLlmHealth(): Promise<{ backend: string; model?: string; configured?: string }> {
  return jsonRequest('/health/llm')
}

export async function checkBackendHealth(
  backend: string,
): Promise<{ status: string; models?: string[]; error?: string }> {
  // The active backend (from LLM_BACKEND) decides which server we probe.
  return jsonRequest(backend === 'mlx' ? '/health/mlx' : '/health/ollama')
}

export async function checkPexelsHealth(): Promise<{ status: string; provider?: string; hint?: string }> {
  return jsonRequest('/health/pexels')
}

export interface GeneratePayload {
  source: SourceContent
  brand?: BrandIdentity | null
  mood_override?: BrandMood | null
  color_scheme_override?: ColorScheme | null
  hero_height?: HeroHeight
  contact?: Record<string, string> | null
}

export async function generateFromSource(payload: GeneratePayload): Promise<GeneratedSite> {
  return jsonRequest('/api/generate/from-source', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export interface GenerateWithPagesPayload {
  source: SourceContent
  selected_pages: PageScaffold[]
  industry: IndustryCategory
  brand?: BrandIdentity | null
  mood_override?: BrandMood | null
  color_scheme_override?: ColorScheme | null
  hero_height?: HeroHeight
  contact?: Record<string, string> | null
  jurisdiction?: string | null
  legal_contact_email?: string | null
  /** Pass the detected_brand from /api/pages/recipe to skip a duplicate LLM call. */
  detected_brand?: DetectedBrand | null
}

export async function generateWithPages(
  payload: GenerateWithPagesPayload,
): Promise<GeneratedSite> {
  return jsonRequest('/api/generate/with-pages', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function fetchPageRecipe(
  source: SourceContent,
  industryOverride?: IndustryCategory,
): Promise<PageRecipeResponse> {
  return jsonRequest('/api/pages/recipe', {
    method: 'POST',
    body: JSON.stringify({
      source,
      industry_override: industryOverride ?? null,
    }),
  })
}

export interface ScrapeOptions {
  respectRobots?: boolean
  crawl?: boolean
  crawlMaxPages?: number
  crawlMaxDepth?: number
}

export async function scrapeUrlPreview(
  url: string,
  opts: ScrapeOptions = {},
): Promise<ScrapePreview> {
  return jsonRequest('/api/scrape/preview', {
    method: 'POST',
    body: JSON.stringify({
      url,
      respect_robots: opts.respectRobots ?? true,
      crawl: opts.crawl ?? true,
      crawl_max_pages: opts.crawlMaxPages ?? 20,
      crawl_max_depth: opts.crawlMaxDepth ?? 3,
    }),
  })
}

/** Fast sitemap probe — returns total URL count before paying for Playwright. */
export async function probeSitemap(url: string): Promise<SitemapProbeResult> {
  return jsonRequest('/api/scrape/probe', {
    method: 'POST',
    body: JSON.stringify({ url }),
  })
}

/** Async crawl kickoff — returns a job_id to poll. */
export async function startCrawl(
  url: string,
  opts: ScrapeOptions = {},
): Promise<{ job_id: string; status: string }> {
  return jsonRequest('/api/scrape/start', {
    method: 'POST',
    body: JSON.stringify({
      url,
      respect_robots: opts.respectRobots ?? true,
      crawl: opts.crawl ?? true,
      crawl_max_pages: opts.crawlMaxPages ?? 20,
      crawl_max_depth: opts.crawlMaxDepth ?? 3,
    }),
  })
}

/** Read the current state of a crawl job. */
export async function getCrawlJob(jobId: string): Promise<CrawlJob> {
  return jsonRequest(`/api/scrape/jobs/${jobId}`)
}

/** Flag a running crawl job for cancellation. */
export async function cancelCrawlJob(jobId: string): Promise<void> {
  await jsonRequest(`/api/scrape/jobs/${jobId}/cancel`, { method: 'POST' })
}

/** Tidy up a finished job's DB rows. Fire-and-forget. */
export async function deleteCrawlJob(jobId: string): Promise<void> {
  await fetch(`/api/scrape/jobs/${jobId}`, { method: 'DELETE' }).catch(() => {})
}

/** Resume a crawl using the prior pass's unvisited frontier. No re-render of the entry. */
export async function extendCrawl(payload: {
  entryUrl: string
  seedUrls: string[]
  alreadySeen?: string[]
  maxMore?: number
}): Promise<ExtendCrawlResult> {
  return jsonRequest('/api/scrape/extend', {
    method: 'POST',
    body: JSON.stringify({
      entry_url: payload.entryUrl,
      seed_urls: payload.seedUrls,
      already_seen: payload.alreadySeen ?? [],
      max_more: payload.maxMore ?? 20,
    }),
  })
}

export async function uploadDocumentPreview(file: File): Promise<ScrapePreview> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch('/api/document/preview', {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(`${response.status} ${response.statusText}: ${text}`)
  }
  return (await response.json()) as ScrapePreview
}

/**
 * Render the scraped/uploaded source into an editable .docx content brief and
 * trigger a download. This is the scrape → document bridge: the user edits the
 * brief offline and re-uploads it via {@link uploadDocumentPreview} to generate
 * the site from the document.
 */
export async function exportSiteDocument(
  source: SourceContent,
  siteName: string,
): Promise<void> {
  const response = await fetch('/api/document/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, site_name: siteName || null }),
  })
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(`${response.status} ${response.statusText}: ${text}`)
  }

  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const filename =
    /filename="?([^"]+)"?/.exec(disposition)?.[1] ?? 'website-content.docx'

  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

export async function extractBrandFromLogo(
  file: File,
  name: string,
  mood: BrandMood,
): Promise<BrandExtractionResult> {
  const form = new FormData()
  form.append('file', file)
  const url = `/api/brand/extract-from-upload?name=${encodeURIComponent(name)}&mood=${mood}`
  const response = await fetch(url, { method: 'POST', body: form })
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(`${response.status} ${response.statusText}: ${text}`)
  }
  return (await response.json()) as BrandExtractionResult
}

// --- CMS push --------------------------------------------------------------

export interface CmsCredentials {
  email: string
  password: string
  entityToken: string
}

export async function testCmsConnection(
  creds: CmsCredentials,
): Promise<CmsConnectionTest> {
  return jsonRequest('/api/cms/test-connection', {
    method: 'POST',
    body: JSON.stringify({
      email: creds.email,
      password: creds.password,
      entity_token: creds.entityToken,
    }),
  })
}

export interface PushPayload {
  site: GeneratedSite
  creds: CmsCredentials
  publish?: boolean
  forceOverwrite?: boolean
  pushBuilderStyles?: boolean
  /** When true, create a fresh entity and push into it (entityToken ignored). */
  createEntity?: boolean
  newEntityName?: string
  newEntityUrl?: string
}

export async function pushToCms(payload: PushPayload): Promise<CmsPushReport> {
  return jsonRequest('/api/cms/push', {
    method: 'POST',
    body: JSON.stringify({
      site: payload.site,
      email: payload.creds.email,
      password: payload.creds.password,
      entity_token: payload.creds.entityToken,
      publish: payload.publish ?? false,
      force_overwrite: payload.forceOverwrite ?? false,
      push_builder_styles: payload.pushBuilderStyles ?? true,
      create_entity: payload.createEntity ?? false,
      new_entity_name: payload.newEntityName ?? null,
      new_entity_url: payload.newEntityUrl ?? null,
    }),
  })
}

/** Fetch the menus + wrapped header/footer this site would push. The preview
 * renderer needs them to resolve nav items and header overlay/shrink — the
 * backend builds them with the same code the push uses, so what the preview
 * renders and what gets published start from one payload. */
export async function fetchPreviewLayout(site: GeneratedSite): Promise<PreviewLayout> {
  return jsonRequest('/api/preview/layout', {
    method: 'POST',
    body: JSON.stringify(site),
  })
}
