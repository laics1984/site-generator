/**
 * PORT of webtree-public/components/public/PublicSiteShell.vue — keep in
 * lockstep. This is where the header/hero relationship is decided: overlay
 * intent, the scroll reveal, shrink-on-scroll, and the spacer that keeps a
 * hero's heading out from under a floating header.
 *
 * Deliberate difference: upstream reads `scrollY` off `window`; the preview
 * renders inside an iframe, so the scroll container is the frame's own
 * document. `scrollRoot` carries it in.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import type { PublicBlockNode, PublicMenu, PublicSchemaTree, PublicStyleTokens } from './lib/public'
import { SchemaRenderer } from './SchemaRenderer'
import { GalleryLightbox } from './GalleryLightbox'
import { getNodeStyles } from './lib/blockRuntime'
import {
  type Band,
  inkClassForBand,
  pickBandAtY,
  probeY,
  readBandRects,
} from './lib/adaptiveInk'
import { isFirstSectionHeaderOverlaySafe } from './lib/headerOverlay'
import {
  collectLightboxGroupIds,
  startLightboxRuntime,
  type LightboxSlide,
} from './lib/lightbox'
import {
  findFirstNonBreadcrumbNode,
  getNodeChildren,
  getNodeName,
  isHeroSectionName,
  normalizeBodySectionNodes,
  normalizeSchemaNodes,
} from './lib/schema'
import { buildCssVars } from './lib/styles'
import {
  BuilderStylesContext,
  HeaderOverlayContext,
  HeaderSchemaContext,
  HeaderShrinkContext,
  MenusContext,
} from './context'

type Schema = PublicSchemaTree | PublicBlockNode[] | null | undefined

export interface PreviewSitePayload {
  builderStyles?: PublicStyleTokens | null
  headerSchema?: Schema
  footerSchema?: Schema
  menus?: PublicMenu[] | null
}

const BACKGROUND_STYLE_KEYS = [
  'background',
  'backgroundColor',
  'backgroundImage',
  'backgroundSize',
  'backgroundPosition',
  'backgroundRepeat',
  'backgroundAttachment',
  'backgroundClip',
  'backgroundOrigin',
] as const

function findHeaderBarNode(root: PublicBlockNode): PublicBlockNode | null {
  const children = getNodeChildren(root)
  for (const child of children) {
    if ((child as Record<string, unknown>)?.headerBar === true) {
      return child
    }
  }
  return null
}

function pickBackgroundStylesFrom(node: PublicBlockNode): CSSProperties | undefined {
  const styles = getNodeStyles(node)
  const picked: Record<string, string | number> = {}
  for (const key of BACKGROUND_STYLE_KEYS) {
    const value = styles[key]
    if (value !== undefined && value !== '') {
      picked[key] = value
    }
  }
  return Object.keys(picked).length ? (picked as CSSProperties) : undefined
}

function pickRootBackgroundStyles(schema: unknown): CSSProperties | undefined {
  const [root] = normalizeSchemaNodes(schema as Schema)
  if (!root) return undefined

  // Self-chrome archetypes (floating pill) carry their background on the inner
  // headerBar container — the root is transparent. The wrapper must also stay
  // transparent so the hero section shows through; the bar paints its own chrome.
  const bar = findHeaderBarNode(root)
  if (bar) return undefined

  return pickBackgroundStylesFrom(root)
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

// Reveal-on-scroll config (mirrors builder HeaderBehavior). Default ON at 80px.
const DEFAULT_HEADER_SCROLL_REVEAL_OFFSET_PX = 80
const HEADER_SCROLL_REVEAL_OFFSET_MIN_PX = 0
const HEADER_SCROLL_REVEAL_OFFSET_MAX_PX = 600

// Shrink-on-scroll config. Off by default — unlike reveal, this has no
// pre-existing behavior to preserve.
const DEFAULT_HEADER_SHRINK_OFFSET_PX = 80
const HEADER_SHRINK_OFFSET_MIN_PX = 0
const HEADER_SHRINK_OFFSET_MAX_PX = 600
const HEADER_SHRINK_AMOUNT_MIN_PERCENT = 50
const HEADER_SHRINK_AMOUNT_MAX_PERCENT = 100
const DEFAULT_HEADER_SHRINK_AMOUNT_PERCENT = 80

// Mirror of builder's `HEADER_OVERLAY_SPACER_BUFFER_PX` / `headerRootMinHeight`.
const HEADER_OVERLAY_SPACER_BUFFER_PX = 32
const HEADER_OVERLAY_SPACER_FALLBACK_MIN_HEIGHT = '96px'

export interface PreviewSiteShellProps {
  site: PreviewSitePayload
  bodySchema?: Schema
  /** The element the preview scrolls in (the iframe's scrollingElement). */
  scrollRoot?: HTMLElement | null
  children?: (slotProps: {
    headerOverlaySpacerPaddingTop?: string
    globalHeroMinHeight?: string
  }) => ReactNode
}

export function PreviewSiteShell({
  site,
  bodySchema,
  scrollRoot,
  children,
}: PreviewSiteShellProps) {
  const cssVars = useMemo(() => buildCssVars(site.builderStyles), [site.builderStyles])
  const runtimeMenus = useMemo(() => site.menus ?? [], [site.menus])

  // Click-to-enlarge on gallery grids (`lightbox` markers from the section
  // catalog). PORT of useSchemaLightbox — the composable's job is split between
  // this state and the effect below, because the runtime needs the iframe's
  // document, which only exists once the root has mounted into it.
  const rootRef = useRef<HTMLDivElement | null>(null)
  const [frameDoc, setFrameDoc] = useState<Document | null>(null)
  const [lightboxOpen, setLightboxOpen] = useState(false)
  const [lightboxSlides, setLightboxSlides] = useState<LightboxSlide[]>([])
  const [lightboxIndex, setLightboxIndex] = useState(0)
  const closeLightbox = useCallback(() => setLightboxOpen(false), [])

  const attachRoot = useCallback((node: HTMLDivElement | null) => {
    rootRef.current = node
    setFrameDoc(node?.ownerDocument ?? null)
  }, [])

  useEffect(() => {
    if (!frameDoc) return
    // Re-arming means the tree was replaced; a viewer left open would be
    // floating over content that no longer produced it.
    setLightboxOpen(false)
    const groupIds = collectLightboxGroupIds([site.headerSchema, bodySchema, site.footerSchema])
    if (groupIds.length === 0) return
    return startLightboxRuntime({
      doc: frameDoc,
      groupIds,
      onOpen: (event) => {
        setLightboxSlides(event.slides)
        setLightboxIndex(event.index)
        setLightboxOpen(true)
      },
    })
  }, [frameDoc, site.headerSchema, site.footerSchema, bodySchema])

  const pageWidthMode = (() => {
    const page = asRecord(site.builderStyles)?.page
    const record = asRecord(page)
    return record?.widthMode === 'full' ? 'full' : 'contained'
  })()

  const readHeaderBehavior = (): Record<string, unknown> | null => {
    const headerSchema = asRecord(site.headerSchema)
    if (!headerSchema) return null
    return asRecord(headerSchema.behavior)
  }

  // Site-wide overlay intent from the generator (behavior.overlay). On its own
  // it does NOT float the header on a page — each page's first section must
  // also carry the `headerOverlaySafe` marker, so pages that open with a
  // compact hero on a light background keep the solid sticky header.
  const staticHeaderOverlay = readHeaderBehavior()?.overlay === true

  // Per-page signal: does THIS page's first real section carry the full-bleed
  // background-hero signature emitted by schema_builder.py's
  // _build_hero_background? No new backend field needed.
  const heroIsBackgroundLayout = (() => {
    const nodes = normalizeBodySectionNodes(bodySchema)
    const first = findFirstNonBreadcrumbNode(nodes)?.node
    if (!first) return false
    if ((first as Record<string, unknown>)?.name !== 'Hero') return false
    const styles = getNodeStyles(first)
    const minHeight = styles.minHeight
    const backgroundImage = styles.backgroundImage
    return (
      typeof minHeight === 'string' &&
      minHeight.includes('--builder-hero-min-height') &&
      typeof backgroundImage === 'string' &&
      backgroundImage.includes('linear-gradient')
    )
  })()

  const firstSectionOverlaySafe = isFirstSectionHeaderOverlaySafe(bodySchema)

  // The transparent phase runs when either the legacy full-bleed style
  // signature matches, or the generator asked for overlay site-wide AND this
  // page's first section is marked overlay-safe.
  const wantsHeaderOverlay = heroIsBackgroundLayout || (staticHeaderOverlay && firstSectionOverlaySafe)

  // Only an explicit `false` disables the reveal; missing field stays ON.
  const headerRevealOnScroll = readHeaderBehavior()?.revealBackgroundOnScroll !== false

  const headerRevealOffset = (() => {
    const raw = readHeaderBehavior()?.scrollRevealOffset
    if (typeof raw !== 'number' || !Number.isFinite(raw)) {
      return DEFAULT_HEADER_SCROLL_REVEAL_OFFSET_PX
    }
    return Math.min(
      HEADER_SCROLL_REVEAL_OFFSET_MAX_PX,
      Math.max(HEADER_SCROLL_REVEAL_OFFSET_MIN_PX, Math.round(raw))
    )
  })()

  const headerShrinkOnScroll = readHeaderBehavior()?.shrinkOnScroll === true

  const headerShrinkOffset = (() => {
    // Overlay headers share the reveal trigger — one scroll moment, two effects.
    if (wantsHeaderOverlay) return headerRevealOffset
    const raw = readHeaderBehavior()?.scrollShrinkOffset
    if (typeof raw !== 'number' || !Number.isFinite(raw)) {
      return DEFAULT_HEADER_SHRINK_OFFSET_PX
    }
    return Math.min(
      HEADER_SHRINK_OFFSET_MAX_PX,
      Math.max(HEADER_SHRINK_OFFSET_MIN_PX, Math.round(raw))
    )
  })()

  const headerShrinkRatio = (() => {
    const raw = readHeaderBehavior()?.shrinkAmount
    const amount =
      typeof raw === 'number' && Number.isFinite(raw)
        ? Math.min(
            HEADER_SHRINK_AMOUNT_MAX_PERCENT,
            Math.max(HEADER_SHRINK_AMOUNT_MIN_PERCENT, Math.round(raw))
          )
        : DEFAULT_HEADER_SHRINK_AMOUNT_PERCENT
    return amount / 100
  })()

  const [isScrolled, setIsScrolled] = useState(false)
  const [isShrunk, setIsShrunk] = useState(false)

  // Adaptive ink (`behavior.adaptiveInk` — the self-chrome floating pill).
  // That bar never solidifies, so it has no chrome of its own to stay legible
  // against: its ink, glass tint and hairline follow whichever marked section
  // is under it. See lib/adaptiveInk.
  const headerAdaptiveInk = readHeaderBehavior()?.adaptiveInk === true
  const headerRef = useRef<HTMLElement | null>(null)
  const [inkBand, setInkBand] = useState<Band | null>(null)

  useEffect(() => {
    const root = scrollRoot
    if (!root) return
    const scroller = root.ownerDocument.defaultView
    if (!scroller) return

    const handleHeaderScroll = () => {
      const scrollY = root.scrollTop
      setIsScrolled(scrollY > headerRevealOffset)
      setIsShrunk(headerShrinkOnScroll && scrollY > headerShrinkOffset)

      const header = headerRef.current
      setInkBand(
        headerAdaptiveInk && header
          ? pickBandAtY(
              // The frame's own document, not the host page's: everything the
              // preview renders lives inside the iframe.
              readBandRects(root.ownerDocument),
              probeY(header.getBoundingClientRect())
            )
          : null
      )
    }

    // rAF-gated: the adaptive-ink branch measures the marked sections, which is
    // layout work, and scroll fires far more often than a frame.
    let frame = 0
    const onScroll = () => {
      if (frame) return
      frame = scroller.requestAnimationFrame(() => {
        frame = 0
        handleHeaderScroll()
      })
    }

    scroller.addEventListener('scroll', onScroll, { passive: true })
    scroller.addEventListener('resize', onScroll, { passive: true })
    handleHeaderScroll()
    return () => {
      scroller.removeEventListener('scroll', onScroll)
      scroller.removeEventListener('resize', onScroll)
      if (frame) scroller.cancelAnimationFrame(frame)
    }
  }, [
    scrollRoot,
    headerRevealOffset,
    headerShrinkOnScroll,
    headerShrinkOffset,
    headerAdaptiveInk,
    // A new page swaps the whole body for one with different bands without
    // scrolling, so nothing else would re-measure.
    bodySchema,
  ])

  const runtimeHeaderShrink = useMemo(
    () => ({ active: isShrunk, ratio: headerShrinkRatio }),
    [isShrunk, headerShrinkRatio]
  )

  const runtimeHeaderOverlay = (() => {
    if (!wantsHeaderOverlay) return false
    // Reveal disabled → stay transparent regardless of scroll position.
    if (!headerRevealOnScroll) return true
    return !isScrolled
  })()

  const runtimeHeaderPosition = readHeaderBehavior()?.position === 'sticky' ? 'sticky' : 'static'

  const headerWrapperStyle = pickRootBackgroundStyles(site.headerSchema)
  const footerWrapperStyle = pickRootBackgroundStyles(site.footerSchema)

  const headerRootMinHeight = (() => {
    const [headerRoot] = normalizeSchemaNodes(site.headerSchema)
    const minHeight = headerRoot ? getNodeStyles(headerRoot).minHeight : undefined
    return typeof minHeight === 'string' && minHeight.trim().length > 0
      ? minHeight
      : HEADER_OVERLAY_SPACER_FALLBACK_MIN_HEIGHT
  })()

  const firstBodySectionIsHero = (() => {
    const nodes = normalizeBodySectionNodes(bodySchema)
    const first = findFirstNonBreadcrumbNode(nodes)?.node
    return Boolean(first && isHeroSectionName(getNodeName(first)))
  })()

  const shouldSpaceFirstSectionForOverlay = runtimeHeaderOverlay && firstBodySectionIsHero
  const headerOverlaySpacerPaddingTop = shouldSpaceFirstSectionForOverlay
    ? `calc(${headerRootMinHeight} + ${HEADER_OVERLAY_SPACER_BUFFER_PX}px)`
    : undefined

  // The site-wide "Hero height" default (full screen vs banded) — needed by
  // SchemaRenderer to tell a real full-screen hero apart from one that merely
  // falls back to the site default var.
  const globalHeroMinHeight = (() => {
    const hero = asRecord(asRecord(site.builderStyles)?.hero)
    const minHeight = hero?.minHeight
    return typeof minHeight === 'string' && minHeight.trim().length > 0 ? minHeight : undefined
  })()

  const headerClassName = [
    'wt-page-header',
    runtimeHeaderPosition === 'sticky' && !runtimeHeaderOverlay ? 'wt-page-header--sticky' : '',
    runtimeHeaderOverlay ? 'wt-page-header--overlay' : '',
    runtimeHeaderOverlay && runtimeHeaderPosition === 'sticky'
      ? 'wt-page-header--overlay-sticky'
      : '',
    !runtimeHeaderOverlay ? 'wt-page-header--solid' : '',
    (headerAdaptiveInk && inkClassForBand(inkBand)) || '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <MenusContext.Provider value={runtimeMenus}>
      <BuilderStylesContext.Provider value={site.builderStyles}>
        <HeaderSchemaContext.Provider value={site.headerSchema}>
          <HeaderOverlayContext.Provider value={runtimeHeaderOverlay}>
            <HeaderShrinkContext.Provider value={runtimeHeaderShrink}>
              <div
                ref={attachRoot}
                className="wt-site"
                style={cssVars as CSSProperties}
                data-page-width-mode={pageWidthMode}
              >
                {site.headerSchema && (
                  <header
                    ref={headerRef}
                    className={headerClassName}
                    style={runtimeHeaderOverlay ? undefined : headerWrapperStyle}
                  >
                    <SchemaRenderer schema={site.headerSchema} scope="header" />
                  </header>
                )}
                <main className="wt-main">
                  {children?.({ headerOverlaySpacerPaddingTop, globalHeroMinHeight })}
                </main>
                {site.footerSchema && (
                  <footer className="wt-page-footer" style={footerWrapperStyle}>
                    <SchemaRenderer schema={site.footerSchema} scope="footer" />
                  </footer>
                )}
                <GalleryLightbox
                  open={lightboxOpen}
                  slides={lightboxSlides}
                  index={lightboxIndex}
                  doc={frameDoc}
                  onIndexChange={setLightboxIndex}
                  onClose={closeLightbox}
                />
              </div>
            </HeaderShrinkContext.Provider>
          </HeaderOverlayContext.Provider>
        </HeaderSchemaContext.Provider>
      </BuilderStylesContext.Provider>
    </MenusContext.Provider>
  )
}
