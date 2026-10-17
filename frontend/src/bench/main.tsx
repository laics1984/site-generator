/**
 * Design-benchmark render harness (dev only — see bench.html).
 *
 * backend/scripts/design_screenshots.py injects `window.__BENCH__` (a generated
 * site and the layout `/api/preview/layout` would return for it) and waits for
 * `window.__BENCH_READY__`. The render is the same tree PagePreview mounts —
 * PreviewFrame → PreviewSiteShell → SchemaRenderer — with the same preview.css
 * and responsive stylesheet, so a screenshot shows what the preview shows.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import ReactDOM from 'react-dom/client'

import type { GeneratedSite, PreviewLayout } from '@/lib/types'
import { PreviewFrame } from '@/preview/PreviewFrame'
import { PreviewSiteShell } from '@/preview/PreviewSiteShell'
import { SchemaRenderer } from '@/preview/SchemaRenderer'
import { buildResponsiveStylesheet } from '@/preview/lib/responsiveRuntime'
import { asPublicSchema } from '@/preview/lib/adapt'
import type { PublicStyleTokens } from '@/preview/lib/public'
import previewCss from '@/preview/preview.css?raw'

declare global {
  interface Window {
    __BENCH__?: { site: GeneratedSite; layout: PreviewLayout; slug?: string }
    __BENCH_READY__?: boolean
  }
}

function Bench({ site, layout, slug }: { site: GeneratedSite; layout: PreviewLayout; slug?: string }) {
  const page = site.pages.find((p) => (slug === undefined ? p.is_homepage : p.slug === slug)) ?? site.pages[0]
  const [scrollRoot, setScrollRoot] = useState<HTMLElement | null>(null)
  const headerSchema = useMemo(() => asPublicSchema(layout.header), [layout])
  const footerSchema = useMemo(() => asPublicSchema(layout.footer), [layout])
  const bodySchema = useMemo(() => asPublicSchema(page.body_schema), [page])
  const previewSite = useMemo(
    () => ({
      builderStyles: site.builder_styles as PublicStyleTokens | null | undefined,
      headerSchema,
      footerSchema,
      menus: layout.menus ?? [],
    }),
    [site, headerSchema, footerSchema, layout],
  )
  const styles = useMemo(
    () => [previewCss, buildResponsiveStylesheet({ headerSchema, bodySchema, footerSchema })],
    [headerSchema, bodySchema, footerSchema],
  )
  const fontHrefs = useMemo(() => {
    const families = (site.google_fonts ?? []).filter((f) => f && f.trim() !== '')
    if (!families.length) return []
    return [`https://fonts.googleapis.com/css2?${families.map((f) => `family=${f.replace(/ /g, '+')}`).join('&')}&display=swap`]
  }, [site.google_fonts])
  const onScrollRoot = useCallback((el: HTMLElement | null) => setScrollRoot(el), [])

  // Ready once the shell has written the theme's custom properties into the
  // frame and its fonts have loaded — the screenshot script copies the frame's
  // document, and a copy taken earlier renders every token at its fallback.
  useEffect(() => {
    if (!scrollRoot) return
    const doc = scrollRoot.ownerDocument
    let cancelled = false
    const poll = async () => {
      while (!cancelled && !doc.querySelector('[style*="--builder-button-background"]')) {
        await new Promise((resolve) => window.setTimeout(resolve, 50))
      }
      await doc.fonts.ready
      await new Promise((resolve) => window.setTimeout(resolve, 300))
      if (!cancelled) window.__BENCH_READY__ = true
    }
    void poll()
    return () => {
      cancelled = true
    }
  }, [scrollRoot])

  return (
    <PreviewFrame
      styles={styles}
      stylesheetHrefs={fontHrefs}
      title={page.title}
      onScrollRootChange={onScrollRoot}
      style={{ width: '100vw', height: '100vh', border: 0, display: 'block' }}
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
  )
}

const bench = window.__BENCH__
const root = ReactDOM.createRoot(document.getElementById('root')!)
root.render(bench ? <Bench {...bench} /> : <p>No window.__BENCH__ payload.</p>)
