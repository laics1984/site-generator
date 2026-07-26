/**
 * PORT of webtree-public/components/blocks/TextBlock.vue — keep in lockstep.
 *
 * Includes the progressive "show more" for clamped text (e.g. team-member bios
 * marked `wt-clamp`): the element ships a static line-clamp inline so it is
 * truncated on first paint, then measures overflow and offers a toggle only
 * when the content is actually cut off.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import type { PublicBlockNode } from '../lib/public'
import { getNodeClasses, getNodeStyles, getStringField } from '../lib/blockRuntime'
import { getNodeDomId } from '../lib/responsiveRuntime'

export function TextBlock({ node }: { node: PublicBlockNode }) {
  const html = getStringField(node, 'html', 'innerText', 'text') || ''
  // Semantic tag for this text node ('h1' on hero headlines, set by the
  // generator — schema_builder.py's `_text(..., html_tag=)`). Absent → 'div',
  // so existing sites render exactly as before. Upstream binds `:innerHTML` on
  // a dynamic `<component :is>` for an SSR reason that doesn't apply here; the
  // resulting DOM is the same.
  const Tag = (getStringField(node, 'htmlTag') || 'div') as 'div'
  const nodeClasses = getNodeClasses(node)
  const nodeStyles = getNodeStyles(node) as CSSProperties
  const nodeDomId = getNodeDomId(node) || undefined
  const isClamp = /\bwt-clamp\b/.test(nodeClasses)

  const textEl = useRef<HTMLDivElement | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)

  useLayoutEffect(() => {
    if (!isClamp) return
    const measure = () => {
      const el = textEl.current
      if (!el) return
      setOverflowing(el.scrollHeight - el.clientHeight > 1)
    }
    measure()
    // Re-measure once web fonts settle (line count can shift).
    const fonts = (textEl.current?.ownerDocument as Document & { fonts?: FontFaceSet })?.fonts
    fonts?.ready?.then(measure).catch(() => {})
  }, [isClamp, html])

  useEffect(() => {
    if (!isClamp) setExpanded(false)
  }, [isClamp])

  if (!isClamp) {
    return (
      <Tag
        className={['wt-text', nodeClasses].filter(Boolean).join(' ')}
        style={nodeStyles}
        data-wt-node-id={nodeDomId}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    )
  }

  // Expanded: lift the line-clamp so the full bio shows.
  const clampStyles: CSSProperties = expanded
    ? { ...nodeStyles, WebkitLineClamp: 'unset', overflow: 'visible' }
    : nodeStyles

  return (
    <div className="wt-clamp-wrap">
      <Tag
        ref={textEl}
        className={['wt-text', nodeClasses].filter(Boolean).join(' ')}
        style={clampStyles}
        data-wt-node-id={nodeDomId}
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {overflowing && (
        <button
          type="button"
          className="wt-clamp-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  )
}
