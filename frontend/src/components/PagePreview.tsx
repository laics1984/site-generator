import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import clsx from 'clsx'

import type { GeneratedPage, GeneratedSite, PreviewLayout } from '@/lib/types'
import { fetchPreviewLayout } from '@/lib/api'
import { pagePath, resolveInternalSlug } from '@/lib/previewNav'
import { Banner, Button, Segmented, SectionLabel, Spinner } from '@/ui'
import { PreviewFrame } from '@/preview/PreviewFrame'
import { PreviewSiteShell } from '@/preview/PreviewSiteShell'
import { SchemaRenderer } from '@/preview/SchemaRenderer'
import { buildResponsiveStylesheet } from '@/preview/lib/responsiveRuntime'
import { asPublicSchema } from '@/preview/lib/adapt'
import type { PublicStyleTokens } from '@/preview/lib/public'
import previewCss from '@/preview/preview.css?raw'

interface PagePreviewProps {
  page: GeneratedPage
  site: GeneratedSite
  mediaCredits?: string[]
  /** Navigate the preview to another generated page (page list + in-frame links). */
  onNavigate: (slug: string) => void
  /** A regeneration is in flight — dim the canvas but keep it mounted. */
  regenerating?: boolean
}

/** Widths that match the breakpoints in lib/responsiveRuntime.ts — the frame is
 * a real viewport, so these exercise the same media queries a device does. */
const VIEWPORTS = {
  desktop: { label: 'Desktop', width: null },
  tablet: { label: 'Tablet', width: 768 },
  mobile: { label: 'Mobile', width: 375 },
} as const

type ViewportKey = keyof typeof VIEWPORTS

const VIEWPORT_OPTIONS = (Object.keys(VIEWPORTS) as ViewportKey[]).map((key) => ({
  value: key,
  label: VIEWPORTS[key].label,
}))

export function PagePreview({
  page,
  site,
  mediaCredits,
  onNavigate,
  regenerating = false,
}: PagePreviewProps) {
  const [viewport, setViewport] = useState<ViewportKey>('desktop')
  const [scrollRoot, setScrollRoot] = useState<HTMLElement | null>(null)
  const [layout, setLayout] = useState<PreviewLayout | null>(null)
  const [layoutError, setLayoutError] = useState<string | null>(null)
  const [layoutAttempt, setLayoutAttempt] = useState(0)
  const [fullscreen, setFullscreen] = useState(false)
  const [showDetails, setShowDetails] = useState(false)

  // The header/footer the visitor sees are the *wrapped* ones the push builds:
  // they carry the menus a `menu` element resolves its items from, plus the
  // overlay/shrink behavior. Rendering site.header_schema raw would show a
  // header with no nav — which is what the old preview did.
  useEffect(() => {
    let cancelled = false
    // A regeneration invalidates the previous site's header/footer — clear it so
    // the skeleton shows rather than the old chrome under the new body.
    setLayout(null)
    setLayoutError(null)
    fetchPreviewLayout(site)
      .then((result) => {
        if (!cancelled) setLayout(result)
      })
      .catch((error: Error) => {
        if (!cancelled) setLayoutError(error.message)
      })
    return () => {
      cancelled = true
    }
  }, [site, layoutAttempt])

  const builderStyles = site.builder_styles as PublicStyleTokens | null | undefined

  const headerSchema = useMemo(() => asPublicSchema(layout?.header), [layout])
  const footerSchema = useMemo(() => asPublicSchema(layout?.footer), [layout])
  const bodySchema = useMemo(() => asPublicSchema(page.body_schema), [page.body_schema])

  const previewSite = useMemo(
    () => ({
      builderStyles,
      headerSchema,
      footerSchema,
      menus: layout?.menus ?? [],
    }),
    [builderStyles, headerSchema, footerSchema, layout]
  )

  // Per-node breakpoint rules. Generated from the same schema the published
  // page uses, so the tablet/mobile toggles below show real overrides.
  const responsiveCss = useMemo(
    () => buildResponsiveStylesheet({ headerSchema, bodySchema, footerSchema }),
    [headerSchema, bodySchema, footerSchema]
  )

  const styles = useMemo(
    () => [previewCss, responsiveCss].filter(Boolean),
    [responsiveCss]
  )

  const fontHrefs = useMemo(() => {
    const families = (site.google_fonts ?? []).filter((f) => f && f.trim() !== '')
    if (families.length === 0) return []
    const params = families.map((f) => `family=${f.replace(/ /g, '+')}`).join('&')
    return [`https://fonts.googleapis.com/css2?${params}&display=swap`]
  }, [site.google_fonts])

  const handleScrollRootChange = useCallback((element: HTMLElement | null) => {
    setScrollRoot(element)
  }, [])

  // Switching pages portals new content into the same frame document, which
  // keeps the old scrollTop — page two would otherwise open half way down.
  useEffect(() => {
    if (scrollRoot) scrollRoot.scrollTop = 0
  }, [page.slug, scrollRoot])

  // In-frame navigation. The ported blocks call preventDefault() on every
  // anchor (there is no published site to navigate to), and per
  // src/preview/README.md they must not diverge from webtree-public — so the
  // interception happens here instead. React attaches its portal listeners to
  // the portal container (the frame's <body>); a capture listener on the frame
  // *document* is one level up, so it runs first.
  const onNavigateRef = useRef(onNavigate)
  onNavigateRef.current = onNavigate
  const pages = site.pages
  useEffect(() => {
    const doc = scrollRoot?.ownerDocument
    if (!doc) return
    const handleClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0) return
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      const target = event.target as Element | null
      const anchor = target?.closest?.('a[href]') as HTMLAnchorElement | null
      if (!anchor) return
      const slug = resolveInternalSlug(anchor.getAttribute('href'), pages)
      if (slug == null) return
      event.preventDefault()
      event.stopPropagation()
      onNavigateRef.current(slug)
    }
    doc.addEventListener('click', handleClick, true)
    return () => doc.removeEventListener('click', handleClick, true)
  }, [scrollRoot, pages])

  // Escape leaves fullscreen — the toolbar button scrolls out of reach otherwise.
  useEffect(() => {
    if (!fullscreen) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setFullscreen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [fullscreen])

  const frameWidth = VIEWPORTS[viewport].width
  const layoutPending = layout === null && layoutError === null

  return (
    // Expects a flex-column parent when not fullscreen: `flex-1` rather than
    // `h-full` so the canvas fits *inside* the parent's padding instead of
    // overflowing it by that amount.
    <div
      className={clsx(
        'flex min-h-0 flex-col',
        fullscreen ? 'fixed inset-0 z-40 bg-canvas p-3' : 'flex-1',
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-t-2xl border border-b-0 border-line bg-surface px-3 py-2.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="inline-flex max-w-[16rem] items-center gap-1.5 truncate rounded-lg bg-surface-sunken px-2.5 py-1 font-mono text-xs text-ink-soft">
            <svg viewBox="0 0 16 16" className="h-3 w-3 shrink-0 text-ink-faint" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
              <rect x="2" y="4" width="12" height="9" rx="1.5" />
              <path d="M2 6.75h12" />
            </svg>
            {pagePath(page)}
          </span>
          <span className="truncate text-sm font-semibold text-ink">{page.title}</span>
          {page.is_homepage && (
            <span className="shrink-0 rounded-full bg-brand-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-brand-700">
              Home
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <Segmented
            ariaLabel="Preview viewport"
            size="sm"
            value={viewport}
            onChange={setViewport}
            options={VIEWPORT_OPTIONS}
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowDetails((v) => !v)}
            aria-pressed={showDetails}
          >
            Details
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setFullscreen((v) => !v)}
            aria-pressed={fullscreen}
            title={fullscreen ? 'Exit fullscreen (Esc)' : 'Fullscreen'}
          >
            {fullscreen ? 'Exit' : 'Fullscreen'}
          </Button>
        </div>
      </div>

      {layoutError && (
        <Banner
          tone="warn"
          className="rounded-none border-x border-b-0 border-line"
          title="Header and footer couldn't be loaded"
          actions={
            <Button size="sm" onClick={() => setLayoutAttempt((n) => n + 1)}>
              Retry
            </Button>
          }
        >
          The page body below still renders, but without the real navigation. ({layoutError})
        </Banner>
      )}

      <div
        className={clsx(
          'relative min-h-0 flex-1 overflow-hidden rounded-b-2xl border border-line bg-surface-sunken',
          !fullscreen && 'min-h-[520px]',
        )}
        aria-busy={regenerating || layoutPending || undefined}
      >
        <div className="flex h-full justify-center">
          <PreviewFrame
            styles={styles}
            stylesheetHrefs={fontHrefs}
            title={`Preview of ${page.title}`}
            className="h-full w-full border-0 bg-white"
            onScrollRootChange={handleScrollRootChange}
            // A width style (not an attribute) so the frame's own viewport —
            // and therefore its media queries — actually narrows.
            style={frameWidth ? { width: `${frameWidth}px`, maxWidth: '100%' } : undefined}
          >
            <PreviewSiteShell site={previewSite} bodySchema={bodySchema} scrollRoot={scrollRoot}>
              {({ headerOverlaySpacerPaddingTop, globalHeroMinHeight }) => (
                <SchemaRenderer
                  schema={bodySchema}
                  scope="body"
                  overlaySpacerPaddingTop={headerOverlaySpacerPaddingTop}
                  globalHeroMinHeight={globalHeroMinHeight}
                />
              )}
            </PreviewSiteShell>
          </PreviewFrame>
        </div>

        {layoutPending && <PreviewSkeleton />}

        {regenerating && (
          <div className="absolute inset-0 flex items-center justify-center bg-surface/70 backdrop-blur-[2px]">
            <div className="flex items-center gap-2.5 rounded-xl border border-line bg-surface px-4 py-3 text-sm font-medium text-ink shadow-raised">
              <Spinner className="h-4 w-4 text-brand-600" />
              Regenerating this site…
            </div>
          </div>
        )}
      </div>

      {showDetails && !fullscreen && (
        <PageMetaPanel page={page} mediaCredits={mediaCredits} />
      )}
    </div>
  )
}

/** Covers the frame until POST /api/preview/layout resolves. Without it the
 * body renders alone for a beat and the header/footer snap in afterwards. */
function PreviewSkeleton() {
  return (
    <div className="absolute inset-0 flex flex-col gap-4 bg-surface p-6">
      <div className="flex items-center justify-between">
        <div className="h-7 w-32 animate-pulse rounded-lg bg-surface-sunken" />
        <div className="flex gap-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-4 w-16 animate-pulse rounded bg-surface-sunken" />
          ))}
        </div>
      </div>
      <div className="flex-1 animate-pulse rounded-2xl bg-surface-sunken" />
      <div className="flex items-center gap-2 text-xs text-ink-muted">
        <Spinner className="h-3.5 w-3.5" />
        Assembling header, footer and menus…
      </div>
    </div>
  )
}

/** Tool metadata — deliberately outside the frame. It describes the page rather
 * than appearing on it, so rendering it inline (as the old preview did) put
 * things on screen that the visitor never sees. */
function PageMetaPanel({
  page,
  mediaCredits,
}: {
  page: GeneratedPage
  mediaCredits?: string[]
}) {
  return (
    <div className="mt-3 grid gap-3 sm:grid-cols-2">
      <div className="rounded-2xl border border-line bg-surface p-4 shadow-card">
        <SectionLabel>SEO</SectionLabel>
        <dl className="mt-2 space-y-2 text-xs">
          <div>
            <dt className="font-semibold text-ink-muted">Title</dt>
            <dd className="mt-0.5 text-ink-soft">{page.seo?.title || page.title}</dd>
          </div>
          <div>
            <dt className="font-semibold text-ink-muted">Description</dt>
            <dd className="mt-0.5 text-ink-soft">
              {page.seo?.description || page.description || '—'}
            </dd>
          </div>
        </dl>
      </div>

      {mediaCredits && mediaCredits.length > 0 && (
        <div className="rounded-2xl border border-line bg-surface p-4 shadow-card">
          <SectionLabel>Photo credits</SectionLabel>
          <ul className="mt-2 space-y-1 text-xs text-ink-muted">
            {mediaCredits.map((credit, i) => (
              <li key={i}>{credit}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
