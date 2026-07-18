/**
 * PARTIAL PORT of webtree-public/components/blocks/CmsListBlock.vue.
 *
 * The real block fetches published articles/events from the runtime content
 * API and renders them in grid/list/featured layouts with pagination. A
 * previewed site has not been pushed, so those entries do not exist yet —
 * there is nothing truthful to render in their place.
 *
 * So this renders the parts that ARE known — the section's own styles/padding
 * and its heading/description — and then upstream's own empty-state
 * (`wt-cms-list__placeholder`) explaining that entries appear after the push.
 * That keeps the section's real height and chrome in the page flow without
 * inventing article cards that would misrepresent the published layout.
 */
import type { CSSProperties } from 'react'
import type { PublicBlockNode } from '../lib/public'
import { getNodeClasses, getNodeContentRecord, getNodeStyles } from '../lib/blockRuntime'
import { getNodeDomId } from '../lib/responsiveRuntime'

const DEFAULT_HEADING: Record<string, string> = {
  articles: 'Latest Articles',
  events: 'Upcoming Events',
}

export function CmsListBlock({ node }: { node: PublicBlockNode }) {
  const content = getNodeContentRecord(node) ?? {}
  const nodeClasses = getNodeClasses(node)
  const nodeStyles = getNodeStyles(node) as CSSProperties
  const nodeDomId = getNodeDomId(node) || undefined

  const source = content.source === 'events' ? 'events' : 'articles'
  const showHeading = content.showHeading !== false
  const heading =
    typeof content.heading === 'string' && content.heading.trim()
      ? content.heading
      : DEFAULT_HEADING[source]
  const description = typeof content.description === 'string' ? content.description : ''
  const showDescription = content.showDescription === true && description.trim().length > 0

  return (
    <section
      className={['wt-cms-list', nodeClasses].filter(Boolean).join(' ')}
      style={nodeStyles}
      data-wt-node-id={nodeDomId}
      data-cms-source={source}
    >
      {(showHeading || showDescription) && (
        <header className="wt-cms-list__header">
          {showHeading && heading && <h2 className="wt-cms-list__heading">{heading}</h2>}
          {showDescription && <p className="wt-cms-list__description">{description}</p>}
        </header>
      )}
      <div className="wt-cms-list__placeholder">
        {source === 'events' ? 'Events' : 'Articles'} appear here once the site is pushed.
      </div>
    </section>
  )
}
