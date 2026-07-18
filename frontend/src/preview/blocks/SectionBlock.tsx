/**
 * PORT of webtree-public/components/blocks/SectionBlock.vue — keep in lockstep.
 * Same photo/texture/divider handling as ContainerBlock, minus the column and
 * header-root concerns (a `section` is never a header/body root).
 */
import type { CSSProperties } from 'react'
import type { PublicBlockNode } from '../lib/public'
import { renderChildren } from '../ElementRenderer'
import { SectionDividerLayer } from './SectionDivider'
import { getNodeClasses, getNodeStyles } from '../lib/blockRuntime'
import { getNodeDomId } from '../lib/responsiveRuntime'
import { getNodeChildren } from '../lib/schema'
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
import { useRuntimeBuilderStyles } from '../context'

export function SectionBlock({ node }: { node: PublicBlockNode }) {
  const builderStyles = useRuntimeBuilderStyles()
  const children = getNodeChildren(node)
  const nodeClasses = getNodeClasses(node)
  const nodeStyles = getNodeStyles(node)
  const nodeDomId = getNodeDomId(node) || undefined
  const divider = getNodeDivider(node)

  const hasVideoLayer = hasBackgroundVideo(nodeStyles)
  const hasPhotoLayer = !hasVideoLayer && hasBackgroundImage(nodeStyles)
  const hasMediaLayer = hasPhotoLayer || hasVideoLayer
  const photoSettings = getBackgroundPhotoSettings(nodeStyles)
  const videoSettings = getBackgroundVideoSettings(nodeStyles)

  const resolvedTextureImage = (() => {
    if (hasMediaLayer) return undefined
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
    const merged: Record<string, unknown> = { ...base }
    if ((hasMediaLayer || divider) && !base.position) {
      merged.position = 'relative'
    }

    const textureImage = resolvedTextureImage
    if (textureImage !== undefined) {
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

  const className = ['wt-section', nodeClasses, hasMediaLayer ? 'wt-section--has-photo' : '']
    .filter(Boolean)
    .join(' ')

  return (
    <section className={className} style={resolvedStyles} data-wt-node-id={nodeDomId}>
      {hasMediaLayer && (
        <div className="wt-section__bg-layer" style={photoLayerClipStyle} aria-hidden="true">
          {hasVideoLayer ? (
            <video
              className="wt-section__bg-video"
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
            <div className="wt-section__bg-photo" style={photoLayerStyle} />
          )}
          {overlayStyle && <div className="wt-section__bg-overlay" style={overlayStyle} />}
        </div>
      )}

      {divider && <SectionDividerLayer divider={divider} />}

      {hasMediaLayer ? (
        <div className="wt-section__content">{renderChildren(children)}</div>
      ) : (
        renderChildren(children)
      )}
    </section>
  )
}
