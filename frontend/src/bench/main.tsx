/**
 * Private browser render harness for design review and benchmarking.
 *
 * backend/scripts/design_screenshots.py injects `window.__BENCH__` (a generated
 * site and the layout `/api/preview/layout` would return for it) and waits for
 * `window.__BENCH_READY__`. The render is the same tree PagePreview mounts —
 * PreviewFrame → PreviewSiteShell → SchemaRenderer — with the same preview.css
 * and responsive stylesheet, so a screenshot shows what the preview shows.
 */
import { useCallback, useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'

import type { GeneratedSite, PreviewLayout } from '@/lib/types'
import { PreviewScene } from '@/preview/PreviewScene'
import { measureReviewPage } from './reviewMetrics'

declare global {
  interface Window {
    __BENCH__?: { site: GeneratedSite; layout: PreviewLayout; slug?: string }
    __BENCH_READY__?: boolean
    __BENCH_MEASURE__?: () => ReturnType<typeof measureReviewPage>
  }
}

function Bench({ site, layout, slug }: { site: GeneratedSite; layout: PreviewLayout; slug?: string }) {
  const page = site.pages.find((p) => (slug === undefined ? p.is_homepage : p.slug === slug)) ?? site.pages[0]
  const [scrollRoot, setScrollRoot] = useState<HTMLElement | null>(null)
  const onScrollRoot = useCallback((el: HTMLElement | null) => setScrollRoot(el), [])

  // Ready once the shell has written the theme's custom properties into the
  // frame and its fonts have loaded — the screenshot script copies the frame's
  // document, and a copy taken earlier renders every token at its fallback.
  useEffect(() => {
    if (!scrollRoot) return
    const doc = scrollRoot.ownerDocument
    let cancelled = false
    const poll = async () => {
      while (!cancelled && !doc.querySelector('[data-scope="body"]')) {
        await new Promise((resolve) => window.setTimeout(resolve, 50))
      }
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
      await Promise.race([doc.fonts.ready, new Promise((resolve) => window.setTimeout(resolve, 1500))])
      if (!cancelled) {
        window.__BENCH_MEASURE__ = () => measureReviewPage(doc)
        window.__BENCH_READY__ = true
        document.documentElement.dataset.benchReady = "true"
      }
    }
    void poll()
    return () => {
      cancelled = true
    }
  }, [scrollRoot])

  return (
    <PreviewScene
      page={page}
      site={site}
      layout={layout}
      scrollRoot={scrollRoot}
      onScrollRootChange={onScrollRoot}
      style={{ width: '100vw', height: '100vh', border: 0, display: 'block' }}
    />
  )
}

const bench = window.__BENCH__
const root = ReactDOM.createRoot(document.getElementById('root')!)
root.render(bench ? <Bench {...bench} /> : <p>No window.__BENCH__ payload.</p>)
