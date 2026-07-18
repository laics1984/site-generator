/**
 * An iframe that renders its children into its own document via a portal.
 *
 * Why an iframe rather than a div: the generated site and this tool are two
 * different stylesheets fighting over the same class names and element
 * selectors. The tool is Tailwind 3, the public renderer is Tailwind 4 plus its
 * own `wt-*` layer, and Tailwind's preflight restyles bare `h1`/`img`/`button`
 * — all of which would silently repaint the very thing the preview is supposed
 * to show faithfully. A separate document gives the render the same clean slate
 * the published page gets.
 *
 * It also makes the viewport real: `buildResponsiveStylesheet()` emits actual
 * `@media (max-width: ...)` rules, and CSS units like `dvh` resolve against the
 * frame. Resizing the frame therefore exercises the same breakpoints a phone
 * does, instead of us simulating them.
 */
import { useEffect, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { createPortal } from 'react-dom'

export interface PreviewFrameProps {
  /** Injected into the frame's <head> as-is. */
  styles: string[]
  /** Stylesheet URLs (Google Fonts) to <link> into the frame. */
  stylesheetHrefs?: string[]
  title?: string
  className?: string
  style?: CSSProperties
  /** Receives the frame's scrolling element once mounted. */
  onScrollRootChange?: (element: HTMLElement | null) => void
  children: ReactNode
}

export function PreviewFrame({
  styles,
  stylesheetHrefs = [],
  title = 'Site preview',
  className,
  style,
  onScrollRootChange,
  children,
}: PreviewFrameProps) {
  const frameRef = useRef<HTMLIFrameElement | null>(null)
  const [frameBody, setFrameBody] = useState<HTMLElement | null>(null)

  // The iframe document isn't ready synchronously on every browser, and Vite's
  // HMR can re-run this against an already-populated frame, so (re)initialise
  // on load as well as on mount.
  useEffect(() => {
    const frame = frameRef.current
    if (!frame) return

    const attach = () => {
      const doc = frame.contentDocument
      if (!doc?.body) return
      doc.documentElement.lang = 'en'
      setFrameBody(doc.body)
      onScrollRootChange?.(doc.scrollingElement as HTMLElement | null)
    }

    attach()
    frame.addEventListener('load', attach)
    return () => {
      frame.removeEventListener('load', attach)
      onScrollRootChange?.(null)
    }
  }, [onScrollRootChange])

  const doc = frameBody?.ownerDocument

  // Styles go in via effects rather than a portal into <head>: they must land
  // before the portalled tree paints, and React gives no ordering guarantee
  // between two portals into the same document.
  useEffect(() => {
    if (!doc) return
    const nodes = styles.map((css) => {
      const el = doc.createElement('style')
      el.setAttribute('data-preview-style', '')
      el.textContent = css
      doc.head.appendChild(el)
      return el
    })
    return () => nodes.forEach((el) => el.remove())
  }, [doc, styles])

  useEffect(() => {
    if (!doc) return
    const nodes = stylesheetHrefs.map((href) => {
      const el = doc.createElement('link')
      el.rel = 'stylesheet'
      el.href = href
      doc.head.appendChild(el)
      return el
    })
    return () => nodes.forEach((el) => el.remove())
  }, [doc, stylesheetHrefs])

  return (
    <iframe
      ref={frameRef}
      title={title}
      className={className}
      style={style}
      // about:blank gives a same-origin document we can portal into.
      src="about:blank"
    >
      {frameBody && createPortal(children, frameBody)}
    </iframe>
  )
}
