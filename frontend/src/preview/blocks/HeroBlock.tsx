/**
 * PORT of webtree-public/components/blocks/HeroBlock.vue — keep in lockstep.
 *
 * Note this is the `hero` *element type*, which the generator does not emit —
 * its heroes are section/container trees named "Hero …". Ported anyway so the
 * registry matches upstream's and a hand-authored hero element still renders.
 */
import type { CSSProperties } from 'react'
import type { PublicBlockNode } from '../lib/public'
import { getNodeClasses, getNodeStyles, getStringField } from '../lib/blockRuntime'
import { getNodeDomId } from '../lib/responsiveRuntime'

export function HeroBlock({ node }: { node: PublicBlockNode }) {
  const eyebrow = getStringField(node, 'eyebrow')
  const title = getStringField(node, 'title')
  const description = getStringField(node, 'description')
  const image = getStringField(node, 'image', 'imageUrl', 'src')
  const nodeClasses = getNodeClasses(node)
  const nodeStyles = getNodeStyles(node) as CSSProperties
  const nodeDomId = getNodeDomId(node) || undefined

  return (
    <section
      className={['wt-hero', nodeClasses].filter(Boolean).join(' ')}
      style={nodeStyles}
      data-wt-node-id={nodeDomId}
    >
      {eyebrow && <p className="wt-eyebrow wt-ui-pill">{eyebrow}</p>}
      <h1 className="wt-title wt-ui-heading">{title}</h1>
      {description && <p className="wt-description wt-ui-muted">{description}</p>}
      {image && (
        <img
          className="wt-hero-image"
          src={image}
          alt={title || ''}
          loading="eager"
          fetchPriority="high"
        />
      )}
    </section>
  )
}
