// Vendored from webtree-public/lib/lightbox.ts — near-verbatim.
//
// Deliberate difference: upstream reaches for the global `document`; the
// preview renders inside an iframe, so every DOM lookup here takes the frame's
// own document explicitly (same reasoning as PreviewSiteShell's `scrollRoot`).
import { getNodeField } from './blockRuntime'
import { getNodeDomId } from './responsiveRuntime'
import { getNodeChildren, normalizeSchemaNodes } from './schema'

export type LightboxSlide = {
  src: string
  alt: string
  /** Editorial caption; falls back to `alt` when the image carries none. */
  caption: string
}

export type LightboxOpenEvent = {
  slides: LightboxSlide[]
  index: number
  /** The tile that opened the viewer — focus returns here on close. */
  trigger: HTMLElement
}

export type LightboxRuntimeOptions = {
  /** The document the preview tree is portalled into. */
  doc: Document
  /** DOM ids of the gallery grids to arm (from `collectLightboxGroupIds`). */
  groupIds: string[]
  onOpen: (event: LightboxOpenEvent) => void
}

const GROUP_ATTR = 'data-wt-lightbox-group'
const ITEM_ATTR = 'data-wt-lightbox-item'
const CAPTION_ATTR = 'data-wt-caption'
const STYLE_ELEMENT_ID = 'wt-lightbox-runtime'

/**
 * DOM ids of every node marked as a lightbox gallery group.
 *
 * Nested markers are not collected twice: a marked node's subtree is not
 * re-scanned, so an (unlikely) gallery inside a gallery yields one outer group
 * rather than two overlapping sets.
 */
export function collectLightboxGroupIds(schemas: unknown[]): string[] {
  const ids: string[] = []
  const seen = new Set<string>()

  const visit = (node: unknown) => {
    if (getNodeField(node as Record<string, unknown>, 'lightbox') === true) {
      const nodeId = getNodeDomId(node as Record<string, unknown>)
      if (nodeId && !seen.has(nodeId)) {
        seen.add(nodeId)
        ids.push(nodeId)
      }
      return
    }
    for (const child of getNodeChildren(node as never)) visit(child)
  }

  for (const schema of schemas) {
    for (const node of normalizeSchemaNodes(schema as never)) visit(node)
  }
  return ids
}

/**
 * The enlargeable images inside a group root, in DOM order. Read from the DOM
 * rather than the schema: the rendered tree is what the visitor actually sees.
 */
export function readGroupImages(root: ParentNode): HTMLImageElement[] {
  return Array.from(root.querySelectorAll<HTMLImageElement>('img.wt-image')).filter(
    // A linked tile navigates; enlarging it too would make one click ambiguous.
    (img) => !img.closest('a') && Boolean(img.getAttribute('src'))
  )
}

export function toSlide(img: HTMLImageElement): LightboxSlide {
  const alt = img.getAttribute('alt') || ''
  return {
    src: img.currentSrc || img.getAttribute('src') || '',
    alt,
    caption: img.getAttribute(CAPTION_ATTR) || alt,
  }
}

// Pointer affordance + a visible focus ring for keyboard users. Injected rather
// than shipped in a stylesheet so pages without a gallery pay nothing.
const RUNTIME_CSS = `
[${ITEM_ATTR}] { cursor: zoom-in; }
[${ITEM_ATTR}]:focus-visible {
  outline: 3px solid var(--builder-color-primary, #2563eb);
  outline-offset: 3px;
}
@media (hover: hover) {
  [${ITEM_ATTR}] { transition: opacity 200ms ease, transform 200ms ease; }
  [${ITEM_ATTR}]:hover { opacity: 0.92; transform: scale(1.015); }
}
@media (prefers-reduced-motion: reduce) {
  [${ITEM_ATTR}] { transition: none; }
  [${ITEM_ATTR}]:hover { transform: none; }
}
`

function ensureRuntimeStylesheet(doc: Document) {
  if (doc.getElementById(STYLE_ELEMENT_ID)) return
  const style = doc.createElement('style')
  style.id = STYLE_ELEMENT_ID
  style.textContent = RUNTIME_CSS
  doc.head.appendChild(style)
}

/**
 * Arm every group. Returns a teardown that unbinds listeners and strips the
 * runtime-added attributes, so a re-render leaves no residue.
 */
export function startLightboxRuntime(options: LightboxRuntimeOptions): () => void {
  const noop = () => {}
  const { doc } = options
  if (!doc) return noop

  const cleanups: Array<() => void> = []
  let armed = false

  for (const groupId of options.groupIds) {
    const root = doc.querySelector<HTMLElement>(
      `[data-wt-node-id="${CSS.escape(groupId)}"]`
    )
    if (!root) continue

    const images = readGroupImages(root)
    // A single image is not a gallery — an enlarge affordance that can't be
    // navigated is just a click that swallows itself. Leave it as a plain tile.
    if (images.length < 2) continue

    if (!armed) {
      ensureRuntimeStylesheet(doc)
      armed = true
    }

    root.setAttribute(GROUP_ATTR, '')
    images.forEach((img, index) => {
      img.setAttribute(ITEM_ATTR, String(index))
      img.setAttribute('role', 'button')
      img.setAttribute('tabindex', '0')
      img.setAttribute(
        'aria-label',
        `${img.getAttribute('alt') || `Image ${index + 1}`} — enlarge (${index + 1} of ${images.length})`
      )
    })

    // One delegated listener per group, not one per tile. Slides are re-read on
    // open so a lazily-swapped `src` is picked up at the moment it's needed.
    const open = (target: EventTarget | null) => {
      const img = (target as HTMLElement | null)?.closest?.(
        `img[${ITEM_ATTR}]`
      ) as HTMLImageElement | null
      if (!img || !root.contains(img)) return false
      const current = readGroupImages(root)
      const index = current.indexOf(img)
      if (index < 0) return false
      // Focus the tile before handing off: the viewer restores focus to
      // whatever was active when it opened, and browsers disagree about
      // whether clicking a tabindex'd element focuses it (Safari does not).
      // Focusing here makes "close returns you to the photo you opened" hold
      // for pointer users too, not just keyboard ones.
      img.focus?.()
      options.onOpen({ slides: current.map(toSlide), index, trigger: img })
      return true
    }

    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0) return
      // Let ctrl/cmd/shift-click keep their browser meanings.
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      if (open(event.target)) event.preventDefault()
    }

    const onKeydown = (event: KeyboardEvent) => {
      if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return
      if (open(event.target)) event.preventDefault()
    }

    root.addEventListener('click', onClick as EventListener)
    root.addEventListener('keydown', onKeydown as EventListener)

    cleanups.push(() => {
      root.removeEventListener('click', onClick as EventListener)
      root.removeEventListener('keydown', onKeydown as EventListener)
      root.removeAttribute(GROUP_ATTR)
      for (const img of images) {
        img.removeAttribute(ITEM_ATTR)
        img.removeAttribute('role')
        img.removeAttribute('tabindex')
        img.removeAttribute('aria-label')
      }
    })
  }

  if (cleanups.length === 0) return noop
  return () => {
    for (const cleanup of cleanups) cleanup()
    cleanups.length = 0
  }
}

/** Wrap-around step used by both the overlay and its tests. */
export function stepIndex(index: number, delta: number, count: number): number {
  if (count <= 0) return 0
  return (((index + delta) % count) + count) % count
}
