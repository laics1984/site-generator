/**
 * PORT of webtree-public/components/blocks/ContainerBlock.vue — keep in
 * lockstep. Handles container / 2col / 3col plus the header / body / footer
 * roots. The style resolution below (photo + texture + overlay-header
 * stripping + shrink) is what makes a section look the way it does; it is
 * ported line-for-line rather than approximated.
 */
import type { CSSProperties } from 'react'
import type { PublicBlockNode } from '../lib/public'
import { renderChildren } from '../ElementRenderer'
import { SectionDividerLayer } from './SectionDivider'
import { getNodeClasses, getNodeStyles, getStringField } from '../lib/blockRuntime'
import { getNodeDomId } from '../lib/responsiveRuntime'
import { getNodeChildren, normalizeBlockType } from '../lib/schema'
import { getNodeDivider } from '../lib/sectionDivider'
import {
  getBackgroundPhotoSettings,
  getBackgroundVideoSettings,
  hasBackgroundImage,
  hasBackgroundVideo,
  pickBorderRadiusStyles,
  pickPhotoLayerStyles,
  stripPhotoStyles,
  toRgbaString,
} from '../lib/backgroundPhoto'
import {
  getNodeBackgroundTexture,
  resolveColorToHex,
  resolvePageBackgroundHex,
  resolvePaletteHex,
  resolveSectionBackgroundImage,
  resolveThemeTexture,
} from '../lib/backgroundTexture'
import {
  getShrunkHeaderPaddingYStyles,
  getShrunkMinHeight,
  HEADER_ROW_CONTAINER_TYPES,
} from '../lib/headerShrink'
import {
  useRuntimeBuilderStyles,
  useRuntimeHeaderOverlay,
  useRuntimeHeaderShrink,
} from '../context'

// Node types eligible for the live grain/mesh texture override. Roots
// (header/footer/body) are intentionally excluded — mirrors builder's
// SECTION_DIVIDER_TARGET_TYPES, which also excludes __header/__body/__footer.
const TEXTURE_ELIGIBLE_TYPES = new Set(['container', '2col', '3col'])

export function ContainerBlock({ node }: { node: PublicBlockNode }) {
  const nodeType = normalizeBlockType(node?.type)
  const builderStyles = useRuntimeBuilderStyles()
  const runtimeHeaderOverlay = useRuntimeHeaderOverlay()
  const runtimeHeaderShrink = useRuntimeHeaderShrink()

  const nodeClasses = getNodeClasses(node)
  const nodeStyles = getNodeStyles(node)
  const nodeDomId = getNodeDomId(node) || undefined
  const anchorId = getStringField(node, 'anchorId') || undefined
  const divider = getNodeDivider(node)

  const isTwoColumnLayout = nodeType === '2col'
  const isThreeColumnLayout = nodeType === '3col'
  const isColumnLayout = isTwoColumnLayout || isThreeColumnLayout
  const isBodyRoot = nodeType === 'body'
  const isHeaderRoot = nodeType === 'header'
  const isFooterRoot = nodeType === 'footer'

  // The header ROOT node renders its own backgroundColor/border/shadow inline,
  // which sits inside the shell's <header> wrapper and paints over the
  // wrapper's transparent background — the class alone never made the header
  // look transparent. Strip the background-ish keys here, on the node that
  // actually paints them, whenever the overlay is active.
  const isOverlayHeaderActive = isHeaderRoot && runtimeHeaderOverlay
  const isHeaderShrinkActive = isHeaderRoot && runtimeHeaderShrink.active

  // Some presets (and every generator-produced header) leave the header root's
  // own padding at '0' and put the real vertical spacing on a direct-child row
  // container instead, so shrinking only the root is a silent no-op. Clone one
  // level deep — same pattern the builder's Editor.tsx uses.
  const baseChildren = getNodeChildren(node)
  const children =
    !isHeaderRoot || !runtimeHeaderShrink.active
      ? baseChildren
      : baseChildren.map((child) => {
          if (!HEADER_ROW_CONTAINER_TYPES.has(normalizeBlockType(child?.type))) {
            return child
          }
          const childStyles = getNodeStyles(child)
          const rowPaddingOverride = getShrunkHeaderPaddingYStyles(
            childStyles,
            runtimeHeaderShrink.ratio
          )
          if (!rowPaddingOverride) {
            return child
          }
          return {
            ...child,
            styles: {
              ...childStyles,
              ...rowPaddingOverride,
              transition: 'padding-top 200ms ease, padding-bottom 200ms ease',
            },
          }
        })

  // Video wins over a photo on the same element (the builder sets one or the
  // other, but suppress the photo defensively if both are present).
  const hasVideoLayer = hasBackgroundVideo(nodeStyles)
  const hasPhotoLayer = !hasVideoLayer && hasBackgroundImage(nodeStyles)
  const hasMediaLayer = hasPhotoLayer || hasVideoLayer
  const photoSettings = getBackgroundPhotoSettings(nodeStyles)
  const videoSettings = getBackgroundVideoSettings(nodeStyles)

  // Live-recompute the decorative grain/mesh backgroundImage from
  // `backgroundTexture` instead of trusting whatever Python baked at
  // generation time — Python only bakes once, with no re-bake on save/publish.
  const resolvedTextureImage = (() => {
    if (hasMediaLayer) return undefined
    if (!TEXTURE_ELIGIBLE_TYPES.has(nodeType)) return undefined
    const palette = resolvePaletteHex(builderStyles)
    const pageBackgroundHex = resolvePageBackgroundHex(builderStyles, palette)
    const bgColorValue = nodeStyles.backgroundColor
    return resolveSectionBackgroundImage(
      {
        backgroundTexture: getNodeBackgroundTexture(node),
        backgroundColorHex:
          typeof bgColorValue === 'string'
            ? resolveColorToHex(bgColorValue, palette, pageBackgroundHex)
            : null,
      },
      {
        primaryHex: resolveColorToHex(palette.primary, palette, pageBackgroundHex),
        themeTexture: resolveThemeTexture(builderStyles),
        plainBackgroundHexes: [palette.background, palette.surface, pageBackgroundHex].map((hex) =>
          resolveColorToHex(hex, palette, pageBackgroundHex)
        ),
      }
    )
  })()

  const resolvedStyles = (() => {
    const base = hasMediaLayer ? stripPhotoStyles(nodeStyles) : { ...nodeStyles }

    const fallbackMinHeight = isBodyRoot
      ? '40px'
      : isColumnLayout
        ? '180px'
        : isHeaderRoot || isFooterRoot || nodeType === 'container'
          ? '10px'
          : undefined

    const merged: Record<string, unknown> = {
      ...base,
      minHeight: isBodyRoot
        ? base.height || base.minHeight || fallbackMinHeight
        : base.minHeight || fallbackMinHeight,
      height: isBodyRoot ? 'auto' : base.height || 'auto',
    }

    if ((hasMediaLayer || divider) && !base.position) {
      merged.position = 'relative'
    }

    const textureImage = resolvedTextureImage
    if (textureImage !== undefined) {
      // A gradient-fill section is flattened to a solid brand fill once any
      // explicit texture is chosen — so "Flat" produces a visible solid and
      // Grain/Mesh layer over it instead of silently doing nothing.
      const bgShorthand = typeof merged.background === 'string' ? merged.background : undefined
      const bgImage = typeof merged.backgroundImage === 'string' ? merged.backgroundImage : undefined
      const hasGradientFill =
        Boolean(bgShorthand?.includes('gradient')) || Boolean(bgImage?.includes('gradient'))
      if (hasGradientFill) {
        delete merged.background
        if (!merged.backgroundColor) {
          merged.backgroundColor = 'var(--builder-color-primary, #2563eb)'
        }
      }
      if (textureImage) {
        merged.backgroundImage = textureImage
        merged.backgroundRepeat = 'repeat'
        merged.backgroundPosition = 'top left'
        merged.backgroundSize = 'auto'
      } else {
        delete merged.backgroundImage
        delete merged.backgroundSize
        delete merged.backgroundPosition
        delete merged.backgroundRepeat
      }
    }

    if (isOverlayHeaderActive) {
      merged.background = 'transparent'
      merged.backgroundColor = 'transparent'
      delete merged.backgroundImage
      delete merged.backgroundSize
      delete merged.backgroundPosition
      delete merged.backgroundRepeat
      delete merged.backgroundAttachment
      delete merged.backgroundClip
      delete merged.backgroundOrigin
      merged.borderBottomColor = 'transparent'
      merged.boxShadow = 'none'
      merged.backdropFilter = 'none'
      merged.WebkitBackdropFilter = 'none'
    }

    if (isHeaderShrinkActive) {
      const paddingOverride = getShrunkHeaderPaddingYStyles(nodeStyles, runtimeHeaderShrink.ratio)
      if (paddingOverride) {
        merged.paddingTop = paddingOverride.paddingTop
        merged.paddingBottom = paddingOverride.paddingBottom
      }
      // The preset headers floor the bar height with a fixed root minHeight —
      // shrink it too, or the padding shrink stays invisible.
      const minHeightOverride = getShrunkMinHeight(nodeStyles.minHeight, runtimeHeaderShrink.ratio)
      if (minHeightOverride) {
        merged.minHeight = minHeightOverride
      }
    }

    if (isHeaderRoot) {
      merged.transition =
        'background-color 200ms ease, border-color 200ms ease, box-shadow 200ms ease, backdrop-filter 200ms ease, padding-top 200ms ease, padding-bottom 200ms ease, min-height 200ms ease'
    }

    return merged as CSSProperties
  })()

  const photoLayerClipStyle = pickBorderRadiusStyles(nodeStyles) as CSSProperties
  const photoLayerStyle = pickPhotoLayerStyles(
    nodeStyles,
    photoSettings.photoOpacity
  ) as CSSProperties
  const overlayStyle: CSSProperties | null =
    photoSettings.overlayOpacity <= 0
      ? null
      : {
          backgroundColor: toRgbaString(photoSettings.overlayColor, photoSettings.overlayOpacity),
        }

  const columnClasses = [
    isColumnLayout ? 'wt-container-block--column-layout' : '',
    isTwoColumnLayout ? 'wt-container-block--two-col' : '',
    isThreeColumnLayout ? 'wt-container-block--three-col' : '',
  ]
  const className = [
    'wt-container-block',
    nodeClasses,
    ...(hasMediaLayer ? [] : columnClasses),
    isBodyRoot ? 'wt-container-block--body-root' : '',
    hasMediaLayer ? 'wt-container-block--has-photo' : '',
  ]
    .filter(Boolean)
    .join(' ')

  const Tag = nodeType === 'section' ? 'section' : 'div'

  return (
    <Tag className={className} style={resolvedStyles} id={anchorId} data-wt-node-id={nodeDomId}>
      {hasMediaLayer && (
        <div className="wt-container-block__bg-layer" style={photoLayerClipStyle} aria-hidden="true">
          {hasVideoLayer ? (
            // Preview shows the poster frame: upstream only starts playback on
            // the client after reduced-motion / small-screen / Data Saver
            // guards, and an autoplaying video in a tool pane helps nobody.
            <video
              className="wt-container-block__bg-video"
              style={{ opacity: Math.min(100, Math.max(0, photoSettings.photoOpacity)) / 100 }}
              src={videoSettings.src || undefined}
              poster={videoSettings.poster || undefined}
              muted
              loop
              playsInline
              preload="metadata"
              tabIndex={-1}
            />
          ) : (
            <div className="wt-container-block__bg-photo" style={photoLayerStyle} />
          )}
          {overlayStyle && <div className="wt-container-block__bg-overlay" style={overlayStyle} />}
        </div>
      )}

      {divider && <SectionDividerLayer divider={divider} />}

      {hasMediaLayer ? (
        <div className={['wt-container-block__content', ...columnClasses].filter(Boolean).join(' ')}>
          {renderChildren(children)}
        </div>
      ) : (
        renderChildren(children)
      )}
    </Tag>
  )
}
