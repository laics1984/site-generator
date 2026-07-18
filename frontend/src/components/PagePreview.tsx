import { useCallback, useEffect, useMemo, useState } from 'react'
import type { GeneratedPage, GeneratedSite, PreviewLayout } from '@/lib/types'
import { fetchPreviewLayout } from '@/lib/api'
import { PreviewFrame } from '@/preview/PreviewFrame'
import { PreviewSiteShell } from '@/preview/PreviewSiteShell'
import { SchemaRenderer } from '@/preview/SchemaRenderer'
import { buildResponsiveStylesheet } from '@/preview/lib/responsiveRuntime'
import { asPublicSchema } from '@/preview/lib/adapt'
import type { PublicStyleTokens } from '@/preview/lib/public'
import previewCss from '@/preview/preview.css?raw'

interface PagePreviewProps {
  page: GeneratedPage
  mediaCredits?: string[]
  site?: GeneratedSite
}

/** Widths that match the breakpoints in lib/responsiveRuntime.ts — the frame is
 * a real viewport, so these exercise the same media queries a device does. */
const VIEWPORTS = {
  desktop: { label: 'Desktop', width: null },
  tablet: { label: 'Tablet', width: 768 },
  mobile: { label: 'Mobile', width: 375 },
} as const

type ViewportKey = keyof typeof VIEWPORTS

export function PagePreview({ page, mediaCredits, site }: PagePreviewProps) {
  const [viewport, setViewport] = useState<ViewportKey>('desktop')
  const [scrollRoot, setScrollRoot] = useState<HTMLElement | null>(null)
  const [layout, setLayout] = useState<PreviewLayout | null>(null)
  const [layoutError, setLayoutError] = useState<string | null>(null)

  // The header/footer the visitor sees are the *wrapped* ones the push builds:
  // they carry the menus a `menu` element resolves its items from, plus the
  // overlay/shrink behavior. Rendering site.header_schema raw would show a
  // header with no nav — which is what the old preview did.
  useEffect(() => {
    if (!site) return
    let cancelled = false
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
  }, [site])

  const builderStyles = site?.builder_styles as PublicStyleTokens | null | undefined

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
    const families = (site?.google_fonts ?? []).filter((f) => f && f.trim() !== '')
    if (families.length === 0) return []
    const params = families.map((f) => `family=${f.replace(/ /g, '+')}`).join('&')
    return [`https://fonts.googleapis.com/css2?${params}&display=swap`]
  }, [site?.google_fonts])

  const handleScrollRootChange = useCallback((element: HTMLElement | null) => {
    setScrollRoot(element)
  }, [])

  const frameWidth = VIEWPORTS[viewport].width

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wider text-blue-600">
            {page.is_homepage ? 'Homepage' : 'Page'} · /{page.slug || ''}
          </div>
          <h2 className="truncate text-base font-semibold text-slate-900">{page.title}</h2>
        </div>
        <div className="flex gap-1 rounded-xl bg-slate-100 p-1">
          {(Object.keys(VIEWPORTS) as ViewportKey[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setViewport(key)}
              className={
                viewport === key
                  ? 'rounded-lg bg-white px-3 py-1 text-xs font-semibold text-slate-900 shadow-sm'
                  : 'rounded-lg px-3 py-1 text-xs font-medium text-slate-600 hover:text-slate-900'
              }
            >
              {VIEWPORTS[key].label}
            </button>
          ))}
        </div>
      </div>

      {layoutError && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          Couldn't load the header/footer layout ({layoutError}). The page body below still
          renders, but without the real navigation.
        </div>
      )}

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-slate-100">
        <div className="flex justify-center">
          <PreviewFrame
            styles={styles}
            stylesheetHrefs={fontHrefs}
            title={`Preview of ${page.title}`}
            className="h-[720px] w-full border-0 bg-white"
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
      </div>

      <PageMetaPanel page={page} mediaCredits={mediaCredits} />
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
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">SEO</div>
        <dl className="mt-2 space-y-2 text-xs">
          <div>
            <dt className="font-semibold text-slate-500">Title</dt>
            <dd className="mt-0.5 text-slate-800">{page.seo?.title || page.title}</dd>
          </div>
          <div>
            <dt className="font-semibold text-slate-500">Description</dt>
            <dd className="mt-0.5 text-slate-800">
              {page.seo?.description || page.description || '—'}
            </dd>
          </div>
        </dl>
      </div>

      {mediaCredits && mediaCredits.length > 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Photo credits
          </div>
          <ul className="mt-2 space-y-1 text-xs text-slate-600">
            {mediaCredits.map((credit, i) => (
              <li key={i}>{credit}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
