/**
 * PORT of webtree-public/components/public/GalleryLightbox.vue — keep in
 * lockstep. Markup and class names are identical; the visuals come from that
 * component's `<style>` block, vendored into preview.css.
 *
 * Deliberate differences:
 *  - The preview tree lives in an iframe, so the overlay portals into that
 *    frame's body (fixed positioning then covers the frame's viewport, exactly
 *    as it covers the browser viewport on the published page), and the scroll
 *    lock / key listener bind to the frame's document.
 *  - Slide changes fade in rather than cross-fading. Upstream uses Vue's
 *    `<Transition>`, which keeps the outgoing frame mounted; React has no
 *    dependency-free equivalent, and the preview is not the place to add one.
 *    The preview is therefore very slightly plainer than the live site — the
 *    direction this port is allowed to differ in.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { LightboxSlide } from './lib/lightbox'
import { stepIndex } from './lib/lightbox'

const FOCUSABLE = 'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
const SWIPE_THRESHOLD = 48

export interface GalleryLightboxProps {
  open: boolean
  slides: LightboxSlide[]
  index: number
  /** The document the preview is portalled into. */
  doc: Document | null
  onIndexChange: (index: number) => void
  onClose: () => void
}

export function GalleryLightbox({
  open,
  slides,
  index,
  doc,
  onIndexChange,
  onClose,
}: GalleryLightboxProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null)
  const restoreFocusRef = useRef<HTMLElement | null>(null)
  const pointerRef = useRef<{ id: number; x: number; y: number } | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const count = slides.length
  const current = slides[index] ?? null
  const hasMultiple = count > 1
  const currentSrc = current?.src

  useEffect(() => {
    if (!currentSrc || !doc?.defaultView) return
    // Already cached (back-and-forth through a set) — don't flash a spinner.
    const probe = new doc.defaultView.Image()
    probe.src = currentSrc
    setIsLoading(!probe.complete)
  }, [currentSrc, doc])

  const go = useCallback(
    (delta: number) => {
      if (count <= 1) return
      onIndexChange(stepIndex(index, delta, count))
    },
    [count, index, onIndexChange]
  )

  const goTo = useCallback(
    (next: number) => {
      if (next === index || next < 0 || next >= count) return
      onIndexChange(next)
    },
    [count, index, onIndexChange]
  )

  // Neighbour preload — only n±1. Warming a whole gallery would compete with
  // the image the visitor is actually waiting for.
  useEffect(() => {
    if (!open || count < 2 || !doc?.defaultView) return
    const ImageCtor = doc.defaultView.Image
    for (const delta of [1, -1]) {
      const src = slides[stepIndex(index, delta, count)]?.src
      if (!src) continue
      const img = new ImageCtor()
      img.src = src
    }
  }, [open, index, count, slides, doc])

  // Keyboard: navigation, dismissal, and a focus trap — the viewer is modal, so
  // Tab must not escape into the page behind it.
  useEffect(() => {
    if (!open || !doc) return

    const onKeydown = (event: KeyboardEvent) => {
      switch (event.key) {
        case 'Escape':
          event.preventDefault()
          onClose()
          return
        case 'ArrowRight':
          event.preventDefault()
          go(1)
          return
        case 'ArrowLeft':
          event.preventDefault()
          go(-1)
          return
        case 'Home':
          event.preventDefault()
          goTo(0)
          return
        case 'End':
          event.preventDefault()
          goTo(count - 1)
          return
        case 'Tab': {
          const root = dialogRef.current
          if (!root) return
          const nodes = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
            (el) => el.offsetParent !== null || el === doc.activeElement
          )
          if (nodes.length === 0) return
          const first = nodes[0]
          const last = nodes[nodes.length - 1]
          const active = doc.activeElement as HTMLElement | null
          if (event.shiftKey && (active === first || !root.contains(active))) {
            event.preventDefault()
            last.focus()
          } else if (!event.shiftKey && active === last) {
            event.preventDefault()
            first.focus()
          }
        }
      }
    }

    doc.addEventListener('keydown', onKeydown)
    return () => doc.removeEventListener('keydown', onKeydown)
  }, [open, doc, go, goTo, count, onClose])

  // Scroll lock + focus lifecycle.
  useEffect(() => {
    if (!open || !doc?.defaultView) return
    const body = doc.body
    const previousOverflow = body.style.overflow
    const previousPaddingRight = body.style.paddingRight

    restoreFocusRef.current = (doc.activeElement as HTMLElement | null) ?? null
    // Compensate the scrollbar so the page behind doesn't jump sideways.
    const gap = doc.defaultView.innerWidth - doc.documentElement.clientWidth
    if (gap > 0) body.style.paddingRight = `${gap}px`
    body.style.overflow = 'hidden'
    dialogRef.current?.focus()

    return () => {
      body.style.overflow = previousOverflow
      body.style.paddingRight = previousPaddingRight
      // Return focus to the tile that opened the viewer, so keyboard users
      // resume where they left off instead of at the top of the document.
      restoreFocusRef.current?.focus?.()
      restoreFocusRef.current = null
    }
  }, [open, doc])

  if (!open || !current || !doc?.body) return null

  // Pointer events rather than touch events so a trackpad drag works too. The
  // gesture only commits when horizontal travel clearly dominates, otherwise a
  // vertical flick would steal the browser's own scroll gesture.
  const onPointerDown = (event: React.PointerEvent) => {
    if (!hasMultiple || event.pointerType === 'mouse') return
    pointerRef.current = { id: event.pointerId, x: event.clientX, y: event.clientY }
  }

  const onPointerUp = (event: React.PointerEvent) => {
    const start = pointerRef.current
    if (!start || start.id !== event.pointerId) return
    pointerRef.current = null
    const dx = event.clientX - start.x
    const dy = event.clientY - start.y
    if (Math.abs(dx) < SWIPE_THRESHOLD || Math.abs(dx) <= Math.abs(dy)) return
    go(dx < 0 ? 1 : -1)
  }

  // Everything that isn't the photo or a control reads as backdrop — including
  // the caption bar, which is just text floating on it. Anchoring dismissal to
  // "not the content" rather than to specific elements means no dead zone where
  // a tap looks like it should close the viewer and silently does nothing.
  const onBackdropClick = (event: React.MouseEvent) => {
    const target = event.target as HTMLElement | null
    if (target?.closest?.('.wt-lightbox__image, button')) return
    onClose()
  }

  return createPortal(
    <div
      ref={dialogRef}
      className="wt-lightbox"
      role="dialog"
      aria-modal="true"
      aria-label={`Image viewer, ${index + 1} of ${count}`}
      tabIndex={-1}
      onClick={onBackdropClick}
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
      onPointerCancel={() => {
        pointerRef.current = null
      }}
    >
      <button
        type="button"
        className="wt-lightbox__close"
        aria-label="Close image viewer"
        onClick={onClose}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>

      {hasMultiple && (
        <button
          type="button"
          className="wt-lightbox__nav wt-lightbox__nav--prev"
          aria-label="Previous image"
          onClick={() => go(-1)}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M15 5l-7 7 7 7" />
          </svg>
        </button>
      )}

      <div className="wt-lightbox__stage">
        {isLoading && <div className="wt-lightbox__spinner" aria-hidden="true" />}
        <img
          key={current.src}
          className="wt-lightbox__image"
          src={current.src}
          alt={current.alt}
          decoding="async"
          onLoad={() => setIsLoading(false)}
          onError={() => setIsLoading(false)}
        />
      </div>

      {hasMultiple && (
        <button
          type="button"
          className="wt-lightbox__nav wt-lightbox__nav--next"
          aria-label="Next image"
          onClick={() => go(1)}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M9 5l7 7-7 7" />
          </svg>
        </button>
      )}

      <div className="wt-lightbox__bar" aria-live="polite">
        {current.caption && (
          <p
            className="wt-lightbox__caption"
            aria-hidden={current.caption === current.alt ? true : undefined}
          >
            {current.caption}
          </p>
        )}
        {hasMultiple && (
          <p className="wt-lightbox__counter">
            {index + 1} / {count}
          </p>
        )}
      </div>
    </div>,
    doc.body
  )
}
