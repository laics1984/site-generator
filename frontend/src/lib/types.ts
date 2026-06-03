export type SourceKind = 'url' | 'pdf' | 'docx'

export interface SourceContent {
  source_kind: SourceKind
  source_ref: string
  title?: string | null
  description?: string | null
  raw_text: string
  headings?: string[]
  images?: string[]
  links?: string[]
  /** Set on entries inside `discovered_pages`; null on the primary page. */
  url_path?: string | null
  /** Additional same-domain pages found by the bounded crawler. */
  discovered_pages?: SourceContent[]
}

export interface PageSeo {
  title?: string | null
  description?: string | null
  keywords?: string[] | null
  ogTitle?: string | null
  ogDescription?: string | null
  ogImage?: string | null
  noindex?: boolean
}

export interface BuilderElement {
  id: string
  name: string
  type: string
  styles: Record<string, unknown>
  content: BuilderElement[] | Record<string, unknown>
  classes?: string
  visible?: boolean
  responsiveStyles?: {
    mobile?: Record<string, unknown>
    tablet?: Record<string, unknown>
  }
}

export interface BodySchema {
  elements: BuilderElement[]
}

export interface GeneratedPage {
  slug: string
  title: string
  description?: string | null
  is_homepage: boolean
  body_schema: BodySchema
  seo: PageSeo
  /** Set on sub-pages — references the parent's slug. */
  parent_slug?: string | null
}

export interface PageNode {
  slug: string
  title: string
  is_homepage: boolean
  children: PageNode[]
}

export type BrandMood =
  | 'modern'
  | 'luxury'
  | 'friendly'
  | 'technical'
  | 'editorial'
  | 'playful'

export interface BrandIdentity {
  name: string
  tagline?: string | null
  logo_url?: string | null
  logo_data_url?: string | null
  extracted_palette: string[]
  mood?: BrandMood | null
  industry?: string | null
}

export interface BuilderStylesShape {
  colors: {
    primary: string
    secondary: string
    accent: string
    text: string
    background: string
    surface: string
  }
  typography: {
    headingFont: string
    bodyFont: string
  }
  buttons: {
    background: string
    text: string
    radius: number
  }
  page: {
    widthMode: 'contained' | 'full'
    maxWidth: number
    background: string
  }
}

export interface BrandExtractionResult {
  brand: BrandIdentity
  theme_preview: BuilderStylesShape
  google_fonts: string[]
}

export interface GeneratedSite {
  site_name: string
  tagline?: string | null
  primary_color?: string | null
  secondary_color?: string | null
  pages: GeneratedPage[]
  /** Tree of top-level pages with nested children; mirrors `pages`. */
  page_tree?: PageNode[]
  media_credits?: string[]
  theme?: unknown
  builder_styles?: BuilderStylesShape | null
  google_fonts?: string[]
  brand?: BrandIdentity | null
  header_schema?: BuilderElement | null
  footer_schema?: BuilderElement | null
}

export type GeneratorMode = 'url' | 'document'

export type IndustryCategory =
  | 'restaurant'
  | 'agency'
  | 'saas'
  | 'professional-services'
  | 'ecommerce'
  | 'consultancy'
  | 'nonprofit'
  | 'personal'
  | 'other'

export interface PageScaffold {
  page_type: string
  slug: string
  title: string
  sections: string[]
  description: string
  is_homepage: boolean
  is_legal: boolean
  rationale: string
  /** Set on sub-page scaffolds — references the parent scaffold's slug. */
  parent_slug?: string | null
  /** Original URL where this sub-page was discovered, if from the crawler. */
  source_url?: string | null
}

export interface IndustryTemplate {
  industry: IndustryCategory
  label: string
  description: string
  core_pages: PageScaffold[]
  suggested_pages: PageScaffold[]
  optional_pages: PageScaffold[]
}

export interface IndustryOption {
  id: IndustryCategory
  label: string
  description: string
}

/** Opaque payload — frontend just passes this verbatim back to /generate/with-pages. */
export type DetectedBrand = Record<string, unknown>

export interface PageRecipeResponse {
  industry: IndustryCategory
  template: IndustryTemplate
  /** Tree of pages inferred from the crawl (pre-checked in the picker). */
  inferred_pages: PageScaffold[]
  all_industries: IndustryOption[]
  detected_brand: DetectedBrand | null
}

export interface ImageCandidate {
  url: string
  alt: string
  width: number | null
  height: number | null
  intent: 'hero' | 'about' | 'logo' | 'generic'
}

export interface ScrapePreview {
  url: string
  final_url: string
  source_content: SourceContent
  brand_candidate: BrandIdentity | null
  image_candidates: ImageCandidate[]
  fetched_at: number
  /** Number of additional pages the bounded crawler found (0 if crawl was off / found nothing). */
  discovered_count?: number
  /** URLs the BFS frontier had queued but didn't process. Powers "Crawl N more". */
  unvisited_urls?: string[]
  unvisited_count?: number
}

export interface SitemapProbeResult {
  has_sitemap: boolean
  total_urls: number
  urls: string[]
  sources: string[]
}

export interface ExtendCrawlResult {
  additional_pages: SourceContent[]
  added_count: number
  unvisited_urls: string[]
  unvisited_count: number
}

export type CrawlJobStatus =
  | 'queued'
  | 'running'
  | 'done'
  | 'failed'
  | 'cancelled'

export interface CrawlJobProgress {
  pages_done?: number
  pages_estimate?: number
  current_url?: string
  current_step?: string
}

export interface CrawlJob {
  id: string
  entry_url: string
  host: string
  status: CrawlJobStatus
  options: Record<string, unknown>
  progress: CrawlJobProgress
  result: ScrapePreview | null
  error: string | null
  started_at: number | null
  finished_at: number | null
  created_at: number
  elapsed_seconds: number | null
}

export interface CmsConnectionTest {
  ok: boolean
  existing_page_count: number
  existing_pages: Array<{
    id: string
    title: string
    slug: string
    isHomepage: boolean
  }>
}

export interface CmsPushStep {
  name: string
  ok: boolean
  detail: string
  data: Record<string, unknown>
  error: string | null
}

export interface CmsPushReport {
  success: boolean
  error: string | null
  steps: CmsPushStep[]
  page_urls: Record<string, string>
}
