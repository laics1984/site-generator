/**
 * PARTIAL PORT of webtree-public/components/blocks/DynamicFieldBlock.vue (and
 * CmsArchiveHeaderBlock.vue, which upstream routes to the same placeholder
 * shape here).
 *
 * These blocks bind to the "current CMS item" — an article's title, body,
 * date, and so on — which only exists on a published detail/archive page. A
 * previewed site has no CMS entries, so the field has nothing to resolve
 * against. Rendering the node's own styles with a labelled stand-in keeps the
 * element's box in the layout and names what will fill it, rather than
 * fabricating an article.
 *
 * The generator emits these only inside CMS detail templates, so a typical
 * generated marketing page never reaches this block.
 */
import type { CSSProperties } from 'react'
import type { PublicBlockNode } from '../lib/public'
import { getNodeClasses, getNodeStyles } from '../lib/blockRuntime'
import { getNodeDomId } from '../lib/responsiveRuntime'
import { getNodeName } from '../lib/schema'

export function DynamicFieldBlock({ node }: { node: PublicBlockNode }) {
  const nodeClasses = getNodeClasses(node)
  const nodeStyles = getNodeStyles(node) as CSSProperties
  const nodeDomId = getNodeDomId(node) || undefined
  const label = getNodeName(node) || String(node?.type || 'Dynamic field')

  return (
    <div
      className={['wt-dynamic-field', 'wt-cms-list__placeholder', nodeClasses]
        .filter(Boolean)
        .join(' ')}
      style={nodeStyles}
      data-wt-node-id={nodeDomId}
    >
      {label} — filled from the CMS entry after push
    </div>
  )
}
