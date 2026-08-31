/** Mirrors the backend Literal on SourceContent.source_kind — change both together. */
export type SourceKind = 'url' | 'pdf' | 'docx' | 'facebook' | 'paste'

export interface ImageMetadata {
  url: string
  alt?: string
  intent?: 'hero' | 'about' | 'logo' | 'generic'
  width?: number | null
  height?: number | null
}

export interface ProfileCandidate {
  name: string
  role?: string | null
  description?: string | null
  bio?: string | null
  photo_url?: string | null
  photo_alt?: string | null
  source_url?: string | null
  confidence?: number
}

export interface SourceContent {
  source_kind: SourceKind
  source_ref: string
  title?: string | null
  description?: string | null
  raw_text: string
  headings?: string[]
  images?: string[]
  image_metadata?: ImageMetadata[]
  profile_candidates?: ProfileCandidate[]
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

/** Decorative background strategy. Mirrors BackgroundStrategy in
 * backend/app/models/builder_schema.py:130 and webtree-public's
 * types/public.ts. */
export type BackgroundStrategy = 'flat' | 'mesh' | 'grain' | 'mesh+grain'

export interface SectionDividerEdge {
  shape?: 'slant' | 'curve' | 'wave' | 'peak'
  height?: number
  color?: string
  flipX?: boolean
  texture?: BackgroundStrategy | null
}

export interface SectionDivider {
  top?: SectionDividerEdge | null
  bottom?: SectionDividerEdge | null
}

export interface BuilderElementMotion {
  preset: string
  delay?: number | null
  duration?: number | null
  stagger?: number | null
}

/** Mirrors BuilderElement in backend/app/models/builder_schema.py, which in
 * turn mirrors webtree/builder/src/lib/site-navigation.ts. The preview renderer
 * reads every field here — dropping one silently drops that visual from the
 * preview while the published site still shows it. */
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
  motion?: BuilderElementMotion | null
  divider?: SectionDivider | null
  /** Per-section override of the theme's decorative background strategy. */
  backgroundTexture?: BackgroundStrategy | null
  /** Semantic tag for a `text` node — the generator sets 'h1' on hero
   * headlines. The public renderer's TextBlock renders this as the element tag;
   * without it the preview emitted a <div> where the live page has an <h1>. */
  htmlTag?: string | null
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

/** Baked color scheme. 'auto' is a UI-only choice → sent as null so the backend
 * applies its logo-based default (a light logo defaults the site to dark). */
export type ColorScheme = 'light' | 'dark'
export type ColorSchemeChoice = 'auto' | ColorScheme

/** Hero photo-background height, site-wide. 'full' = full-bleed full-screen hero;
 * 'banded' = bounded-height full-bleed photo hero (content sits closer to top). */
export type HeroHeight = 'full' | 'banded'
/** 'auto' is a UI-only choice → sent as null so the design-brain pass picks from
 * the brand's mood and industry (backend: resolve_hero_height). */
export type HeroHeightChoice = 'auto' | HeroHeight

/** Where a scraped brand mark came from. 'og-image' is a palette source only —
 * mirrors LogoSource in backend/app/models/brand.py. */
export type LogoSource = 'logo' | 'icon' | 'og-image'

export interface BrandIdentity {
  name: string
  tagline?: string | null
  logo_url?: string | null
  logo_data_url?: string | null
  extracted_palette: string[]
  logo_is_light?: boolean | null
  /** The site icon: what a browser tab and a Google result show beside the
   * site's name. A separate question from the brand mark, and falls back to it
   * when the source declared no icon. */
  favicon_url?: string | null
  logo_source?: LogoSource | null
  /** False when the mark may seed the palette but must not be drawn as the
   * brand logo (a social card, or a favicon too small for the header lockup). */
  logo_render_ok?: boolean
  mood?: BrandMood | null
  color_scheme?: ColorScheme | null
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
  /** Transparent header floating over a full-bleed hero. Drives the preview's
   * overlay spacer exactly as it drives the public renderer's. */
  header_overlay?: boolean
  /** (label, url) pairs — source the footer's social menu. */
  social_links?: Array<[string, string]>
}

// --- preview layout (POST /api/preview/layout) --------------------------------
// The payload the push writes to the CMS. `menu` elements resolve their items
// from `menus[]` by slot, and the header's `behavior` drives overlay/shrink —
// so the preview needs this to render the header/footer the visitor gets.
// Mirrors backend/app/services/menu_builder.py's wrap_header / wrap_footer.

export interface PreviewMenuItem {
  id: string
  label: string
  href: string
  visible?: boolean
  target?: '_self' | '_blank'
  rel?: string
  children?: PreviewMenuItem[]
}

export interface PreviewMenu {
  id: string
  name: string
  purpose: string
  items: PreviewMenuItem[]
}

export interface PreviewHeaderBehavior {
  position?: 'sticky' | 'static'
  overlay?: boolean
  scrollRevealOffset?: number
  shrinkOnScroll?: boolean
  scrollShrinkOffset?: number
  shrinkAmount?: number
}

export interface PreviewHeader {
  elements: BuilderElement[]
  behavior: PreviewHeaderBehavior
  preset: { id: string | null }
  slots: { primaryMenuId: string | null; utilityMenuId: string | null }
}

export interface PreviewFooter {
  elements: BuilderElement[]
  preset: { id: string | null }
  slots: {
    footerMenuId: string | null
    legalMenuId: string | null
    socialMenuId: string | null
  }
}

export interface PreviewLayout {
  menus: PreviewMenu[]
  header: PreviewHeader
  footer: PreviewFooter
}

export type IndustryCategory =
  | 'restaurant'
  | 'agency'
  | 'saas'
  | 'professional-services'
  | 'ecommerce'
  | 'consultancy'
  | 'nonprofit'
  | 'childcare'
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
  /** Position in the source site's header nav (0-based); null/absent ⇒ not in it. */
  nav_rank?: number | null
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

export interface FacebookHours {
  day: string
  opens: string
  closes: string
}

export interface FacebookPost {
  message?: string | null
  created_time?: string | null
  image_url?: string | null
  permalink?: string | null
}

export interface FacebookReview {
  text: string
  author: string
  rating?: number | null
  created_time?: string | null
}

/**
 * Everything we read off the Page, and the only thing the site is allowed to
 * claim about the business. Round-tripped opaquely from the preview into
 * /api/generate/with-pages, where the backend rewrites the contact, hours,
 * locations, testimonials and stats blocks from these values.
 */
export interface FacebookFacts {
  name: string
  canonical_url: string
  page_id?: string | null
  username?: string | null
  category?: string | null
  categories?: string[]
  about?: string | null
  description?: string | null
  mission?: string | null
  products?: string | null
  founded?: string | null
  price_range?: string | null
  phone?: string | null
  emails?: string[]
  website?: string | null
  single_line_address?: string | null
  street?: string | null
  city?: string | null
  state?: string | null
  zip_code?: string | null
  country?: string | null
  hours?: FacebookHours[]
  fan_count?: number | null
  rating_count?: number | null
  overall_star_rating?: number | null
  reviews?: FacebookReview[]
  profile_picture_url?: string | null
  cover_photo_url?: string | null
  posts?: FacebookPost[]
  /** Which reader ran. Mirrors `FetchPath` in backend/app/models/facebook.py.
   * `render_session` is a render signed in with a saved browser session: it
   * sees the About panel a logged-out render doesn't, but still no structured
   * posts or recommendations — only Graph has those. */
  fetched_via: 'graph' | 'render' | 'render_session'
  /** Some fields couldn't be read — the UI offers a token to fill the gaps. */
  partial?: boolean
  missing_fields?: string[]
}

/** The signed-in Facebook session shared by every Page read.
 *
 * Captured on the operator's own machine by `./dev.sh fb-login` — the backend
 * runs in a container with no display and cannot open the window a login needs.
 * Carries no cookies: only what the UI shows. */
export interface FacebookSession {
  name: string
  connected: boolean
  saved_at: number | null
  expires_at: number | null
  expires_in_days: number | null
  label: string | null
  /** False when FACEBOOK_SESSION_ENABLED is off — hide the affordance entirely
   * rather than offering a button that can't work. */
  enabled: boolean
}

/** What a paste contributed, for the confirmation step to report back. */
export interface PasteReport {
  /** Read as markup rather than prose. */
  is_html: boolean
  characters: number
  added_pages: number
  /** Images the markup pointed at with a site-relative path — no origin to resolve. */
  unresolved_images: number
  /** False when the paste is the whole source. */
  merged: boolean
  /** Which reader worked out the page structure. 'heuristic' means the local
   * model was unavailable (or the paste was too big) and line shape decided. */
  structured_by?: 'llm' | 'heuristic'
  /** Unfilled `[...]` placeholders left in the copy — nothing upstream can fill them. */
  placeholders?: number
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
  /** Present only when pasted content went into this source — standalone or merged. */
  paste?: PasteReport
  /** Present only on a Facebook read. Additive — the rest of the shape is identical. */
  facebook_facts?: FacebookFacts
  facebook_contact?: Record<string, string>
  facebook_industry?: IndustryCategory
  facebook_sections?: string[]
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
  /** Facebook reads have no page count — they report a percentage instead. */
  percent?: number
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

/** A CMS this generator can push into. The backend owns the list — the frontend
 * reads no import.meta.env, so it can't know the hosts, and a push names a
 * target by `name` rather than by URL (see backend services/cms_targets.py). */
export interface CmsTarget {
  name: string
  /** host:port of the CMS API — derived, so it always says where bytes go. */
  label: string
  api_base_url: string
  /** True when the target is not this machine. Drives the warning treatment. */
  is_remote: boolean
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
  /** The step succeeded, but not the way it was asked to — the push carried on
   * and there is something to fix in the CMS afterwards. Distinct from `error`,
   * which aborts. */
  warning?: string | null
}

export interface CmsPushReport {
  success: boolean
  error: string | null
  steps: CmsPushStep[]
  /** pageId → slug (not a URL, despite the name — see push_orchestrator.py). */
  page_urls: Record<string, string>
  /** Deep link into the webtree admin suite for the pushed entity, for the
   * target this push actually went to. Present only when that target has an
   * admin origin configured; the UI hides the CTA otherwise rather than
   * guessing a URL — or, worse, offering a localhost link after a remote push. */
  admin_url?: string | null
}
