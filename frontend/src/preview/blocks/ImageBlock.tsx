/**
 * PORT of webtree-public/components/blocks/ImageBlock.vue — keep in lockstep.
 */
import type { CSSProperties } from 'react'
import type { PublicBlockNode } from '../lib/public'
import { getBooleanField, getNodeClasses, getNodeStyles, getStringField } from '../lib/blockRuntime'
import { getShrunkLogoStyles } from '../lib/headerShrink'
import { getImageElementStyles, getImageWrapperStyles } from '../lib/imageStyles'
import { getNodeDomId } from '../lib/responsiveRuntime'
import { getNodeName } from '../lib/schema'
import { useRuntimeHeaderShrink } from '../context'

export function ImageBlock({ node }: { node: PublicBlockNode }) {
  const src = getStringField(node, 'src', 'imageUrl')
  const alt = getStringField(node, 'alt', 'title') || ''
  const href = getStringField(node, 'href') || ''
  const ariaLabel = getStringField(node, 'ariaLabel') || undefined
  const isHero =
    getBooleanField(node, 'priority') || getStringField(node, 'fetchpriority') === 'high'
  const nodeClasses = getNodeClasses(node)
  const nodeDomId = getNodeDomId(node) || undefined

  // Mirrors builder's `isBrandHeaderElement` exact-match (not a loose regex) —
  // only the header's own brand/logo node shrinks, never an unrelated body
  // image that happens to share the name.
  const name = getNodeName(node)
  const isBrandLogo = name === 'Brand' || name === 'Brand Logo'
  const runtimeHeaderShrink = useRuntimeHeaderShrink()
  const isLogoShrinkActive = isBrandLogo && runtimeHeaderShrink.active

  const styles = getNodeStyles(node)

  const wrapperStyle = (() => {
    let wrapperStyles = getImageWrapperStyles(styles, nodeClasses)
    // A brand logo must render at its natural aspect, sized to fit — never the
    // default full-width `height:100%` `object-fit:cover` box, which crops the
    // logo into a band. The active px dimension (height, with width:auto) stays
    // on the wrapper so the on-scroll shrink still drives it.
    if (isBrandLogo) {
      wrapperStyles = {
        ...wrapperStyles,
        display: 'inline-flex',
        alignItems: 'center',
        width: 'auto',
        overflow: 'visible',
      }
    }
    if (!isLogoShrinkActive) {
      return wrapperStyles as CSSProperties
    }
    const shrunk = getShrunkLogoStyles(styles, runtimeHeaderShrink.ratio)
    return (
      shrunk
        ? { ...wrapperStyles, ...shrunk, transition: 'width 200ms ease, height 200ms ease' }
        : wrapperStyles
    ) as CSSProperties
  })()

  // Styling that must live on the <img> element itself.
  const imgStyle = (() => {
    const base = getImageElementStyles(styles)
    // Logo: fill the wrapper's (shrinkable) height, keep width auto for aspect,
    // and `contain` so it's never cropped. Overrides `.wt-image`'s 100%x100%.
    if (isBrandLogo) {
      return { ...base, objectFit: 'contain', width: 'auto', height: '100%' } as CSSProperties
    }
    return base as CSSProperties
  })()

  if (!src) return null

  const img = (
    <img
      className="wt-image"
      src={src}
      alt={alt}
      style={imgStyle}
      loading={isHero ? 'eager' : 'lazy'}
      fetchPriority={isHero ? 'high' : 'auto'}
    />
  )

  return (
    <div
      className={['wt-image-block', nodeClasses].filter(Boolean).join(' ')}
      style={wrapperStyle}
      data-wt-node-id={nodeDomId}
    >
      {href ? (
        // Inert in preview, same reasoning as LinkBlock.
        <a
          className="wt-image-link"
          href={href}
          aria-label={ariaLabel}
          onClick={(event) => event.preventDefault()}
        >
          {img}
        </a>
      ) : (
        img
      )}
    </div>
  )
}
