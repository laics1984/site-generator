/**
 * PORT of webtree-public/components/blocks/LinkBlock.vue — keep in lockstep.
 *
 * Upstream renders a <NuxtLink> that routes within the published site. The
 * preview has no router and no published pages to route to, so links render as
 * inert anchors: navigating away would only break the preview frame. Styling —
 * which is what the preview is for — is identical.
 */
import type { CSSProperties } from 'react'
import type { PublicBlockNode } from '../lib/public'
import { getNodeClasses, getNodeStyles, getStringField } from '../lib/blockRuntime'
import { getNodeDomId } from '../lib/responsiveRuntime'

export function LinkBlock({ node }: { node: PublicBlockNode }) {
  const href = getStringField(node, 'href') || '#'
  const label = getStringField(node, 'label', 'innerText', 'text') || 'Link'
  const nodeClasses = getNodeClasses(node)
  const nodeStyles = getNodeStyles(node) as CSSProperties
  const nodeDomId = getNodeDomId(node) || undefined

  return (
    <a
      className={['wt-link', 'wt-ui-link', nodeClasses].filter(Boolean).join(' ')}
      style={nodeStyles}
      data-wt-node-id={nodeDomId}
      href={href}
      onClick={(event) => event.preventDefault()}
      title={href}
    >
      {label}
    </a>
  )
}
